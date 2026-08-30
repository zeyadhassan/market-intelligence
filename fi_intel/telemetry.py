"""OpenTelemetry boundary and measurable service-level objectives.

Callers pass stable domain identifiers to spans, while metrics intentionally
use only bounded-cardinality dimensions. Raw document text, queries, tokens,
credentials, and principal IDs must never enter telemetry attributes.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Final

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter, Histogram, Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer


class PipelineStage(StrEnum):
    INGEST = "ingest"
    RESOLVE = "resolve"
    EXTRACT = "extract"
    PROJECT = "project"
    DETECT = "detect"
    RETRIEVE = "retrieve"
    RESEARCH = "research"
    PUBLISH = "publish"
    REVIEW = "review"
    ANALYZE = "analyze"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class SLOTargets:
    """Initial pilot targets from the capability transformation report."""

    ingestion_completeness: float = 0.995
    source_freshness: float = 0.99
    evidence_resolution: float = 1.0
    citation_coverage: float = 1.0
    entitlement_leaks: int = 0
    search_p95_seconds: float = 2.0
    signal_to_brief_p95_seconds: float = 15 * 60.0

    def __post_init__(self) -> None:
        ratios = (
            self.ingestion_completeness,
            self.source_freshness,
            self.evidence_resolution,
            self.citation_coverage,
        )
        if any(value < 0.0 or value > 1.0 for value in ratios):
            raise ValueError("SLO ratios must be between zero and one")
        if self.entitlement_leaks != 0:
            raise ValueError("the entitlement leak objective must remain zero")
        if self.search_p95_seconds <= 0 or self.signal_to_brief_p95_seconds <= 0:
            raise ValueError("SLO latency targets must be positive")


@dataclass(frozen=True)
class TelemetryConfig:
    service_name: str = "fi-intel"
    service_version: str = "unknown"
    environment: str = "development"
    trace_endpoint: str | None = None
    metric_endpoint: str | None = None
    export_interval_ms: int = 30_000

    def __post_init__(self) -> None:
        if not self.service_name.strip():
            raise ValueError("service_name cannot be empty")
        if self.export_interval_ms < 1_000:
            raise ValueError("export_interval_ms must be at least 1000")


_SPAN_ID_KEYS: Final = frozenset(
    {
        "run_id",
        "raw_asset_id",
        "document_version_id",
        "claim_id",
        "assertion_id",
        "signal_id",
        "brief_id",
        "pattern_id",
        "source_id",
    }
)


def _safe_span_attributes(attributes: Mapping[str, str] | None) -> dict[str, str]:
    if attributes is None:
        return {}
    unexpected = set(attributes) - _SPAN_ID_KEYS
    if unexpected:
        raise ValueError(f"telemetry attributes are not allow-listed: {sorted(unexpected)}")
    return {f"fi_intel.{key}": value for key, value in attributes.items() if value}


class Telemetry:
    """Owned providers and instruments for one process.

    Providers are not installed globally by default. Dependency injection
    avoids cross-test contamination and lets workers shut exporters down
    cleanly during rolling deployments.
    """

    def __init__(
        self,
        config: TelemetryConfig,
        *,
        span_exporter: SpanExporter | None = None,
        metric_readers: tuple[MetricReader, ...] = (),
        install_global: bool = False,
    ) -> None:
        resource = Resource.create(
            {
                "service.name": config.service_name,
                "service.version": config.service_version,
                "deployment.environment.name": config.environment,
            }
        )

        self._tracer_provider = TracerProvider(resource=resource)
        resolved_span_exporter = span_exporter
        if resolved_span_exporter is None and config.trace_endpoint is not None:
            resolved_span_exporter = OTLPSpanExporter(endpoint=config.trace_endpoint)
        if resolved_span_exporter is not None:
            self._tracer_provider.add_span_processor(BatchSpanProcessor(resolved_span_exporter))

        readers = list(metric_readers)
        if config.metric_endpoint is not None:
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=config.metric_endpoint),
                    export_interval_millis=config.export_interval_ms,
                )
            )
        self._meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        if install_global:
            trace.set_tracer_provider(self._tracer_provider)
            metrics.set_meter_provider(self._meter_provider)

        self.tracer: Tracer = self._tracer_provider.get_tracer("fi_intel")
        self.meter: Meter = self._meter_provider.get_meter("fi_intel")
        self._stage_runs: Counter = self.meter.create_counter(
            "fi_intel.pipeline.stage.runs",
            description="Completed pipeline stage executions",
        )
        self._stage_latency: Histogram = self.meter.create_histogram(
            "fi_intel.pipeline.stage.duration",
            unit="s",
            description="Pipeline stage duration",
        )
        self._http_requests: Counter = self.meter.create_counter(
            "fi_intel.http.server.requests",
            description="Authenticated application requests",
        )
        self._http_latency: Histogram = self.meter.create_histogram(
            "fi_intel.http.server.duration",
            unit="s",
            description="Application request duration",
        )
        self._policy_decisions: Counter = self.meter.create_counter(
            "fi_intel.policy.decisions",
            description="Authorization decisions by bounded policy class",
        )
        self._queue_age: Histogram = self.meter.create_histogram(
            "fi_intel.review.queue.age",
            unit="s",
            description="Age of pending human-review work",
        )
        self._source_operations: Counter = self.meter.create_counter(
            "fi_intel.source.operations",
            description="Source poll outcomes by bounded source and state",
        )
        self._queue_transitions: Counter = self.meter.create_counter(
            "fi_intel.queue.transitions",
            description="Durable queue transitions by queue and state",
        )
        self._coverage_decisions: Counter = self.meter.create_counter(
            "fi_intel.coverage.decisions",
            description="Fail-closed coverage outcomes",
        )
        self._retrieval_candidates: Histogram = self.meter.create_histogram(
            "fi_intel.retrieval.candidates",
            description="Candidate counts by governed route and evidence side",
        )
        self._model_outcomes: Counter = self.meter.create_counter(
            "fi_intel.model.outcomes",
            description="Governed model-call outcomes by component",
        )
        self._result_transitions: Counter = self.meter.create_counter(
            "fi_intel.result.transitions",
            description="Opportunity result lifecycle transitions",
        )
        self._delivery_transitions: Counter = self.meter.create_counter(
            "fi_intel.delivery.transitions",
            description="Digest delivery transitions",
        )

    @contextmanager
    def stage(
        self,
        stage: PipelineStage,
        *,
        identifiers: Mapping[str, str] | None = None,
    ) -> Iterator[None]:
        """Trace and measure a pipeline stage, recording failures explicitly."""

        started = monotonic()
        status = "success"
        with self.tracer.start_as_current_span(
            f"fi_intel.{stage.value}",
            kind=SpanKind.INTERNAL,
            attributes=_safe_span_attributes(identifiers),
        ) as span:
            try:
                yield
            except Exception as exc:
                status = "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            finally:
                dimensions = {"stage": stage.value, "status": status}
                self._stage_runs.add(1, dimensions)
                self._stage_latency.record(monotonic() - started, dimensions)

    def record_http(self, method: str, route: str, status_code: int, duration: float) -> None:
        status_class = f"{status_code // 100}xx"
        dimensions = {
            "http.request.method": method.upper(),
            "http.route": route,
            "http.response.status_class": status_class,
        }
        self._http_requests.add(1, dimensions)
        self._http_latency.record(duration, dimensions)

    def record_policy_decision(
        self,
        decision: PolicyDecision,
        *,
        resource_type: str,
        reason_code: str,
    ) -> None:
        self._policy_decisions.add(
            1,
            {
                "decision": decision.value,
                "resource.type": resource_type,
                "reason.code": reason_code,
            },
        )

    def record_review_queue_age(self, subject_type: str, age_seconds: float) -> None:
        if age_seconds < 0:
            raise ValueError("review queue age cannot be negative")
        self._queue_age.record(age_seconds, {"subject.type": subject_type})

    def record_source_operation(self, source_id: str, status: str) -> None:
        self._source_operations.add(1, {"source.id": source_id, "status": status})

    def record_queue_transition(self, queue: str, state: str, count: int = 1) -> None:
        if count < 0:
            raise ValueError("queue transition count cannot be negative")
        self._queue_transitions.add(count, {"queue": queue, "state": state})

    def record_coverage(self, *, complete: bool, reason_class: str = "none") -> None:
        self._coverage_decisions.add(
            1,
            {"complete": str(complete).lower(), "reason.class": reason_class},
        )

    def record_retrieval(self, route: str, side: str, candidates: int) -> None:
        if candidates < 0:
            raise ValueError("retrieval candidate count cannot be negative")
        self._retrieval_candidates.record(candidates, {"route": route, "side": side})

    def record_model_outcome(self, component: str, outcome: str) -> None:
        self._model_outcomes.add(1, {"component": component, "outcome": outcome})

    def record_result_transition(self, lifecycle: str, decision: str) -> None:
        self._result_transitions.add(1, {"lifecycle": lifecycle, "decision": decision})

    def record_delivery_transition(self, state: str, count: int = 1) -> None:
        if count < 0:
            raise ValueError("delivery transition count cannot be negative")
        self._delivery_transitions.add(count, {"state": state})

    def shutdown(self) -> None:
        """Flush telemetry before terminating a worker or API process."""

        self._meter_provider.shutdown()
        self._tracer_provider.shutdown()
