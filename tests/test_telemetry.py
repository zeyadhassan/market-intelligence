from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fi_intel.telemetry import (
    PipelineStage,
    PolicyDecision,
    SLOTargets,
    Telemetry,
    TelemetryConfig,
)


def _metric_points(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    data = reader.get_metrics_data()
    assert data is not None
    return {
        metric.name: list(metric.data.data_points)
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def test_stage_records_trace_success_and_bounded_metrics() -> None:
    spans = InMemorySpanExporter()
    metrics = InMemoryMetricReader()
    telemetry = Telemetry(
        TelemetryConfig(service_version="test"),
        span_exporter=spans,
        metric_readers=(metrics,),
    )

    with telemetry.stage(
        PipelineStage.EXTRACT,
        identifiers={"run_id": "run-1", "document_version_id": "version-1"},
    ):
        pass

    telemetry._tracer_provider.force_flush()  # noqa: SLF001
    exported = spans.get_finished_spans()
    points = _metric_points(metrics)
    assert exported[0].name == "fi_intel.extract"
    assert exported[0].attributes["fi_intel.run_id"] == "run-1"
    assert points["fi_intel.pipeline.stage.runs"][0].attributes == {
        "stage": "extract",
        "status": "success",
    }
    telemetry.shutdown()


def test_stage_records_exception_without_swallowing_it() -> None:
    spans = InMemorySpanExporter()
    telemetry = Telemetry(TelemetryConfig(), span_exporter=spans)

    with pytest.raises(RuntimeError, match="failed"):
        with telemetry.stage(PipelineStage.PUBLISH, identifiers={"brief_id": "brief-1"}):
            raise RuntimeError("failed")

    telemetry._tracer_provider.force_flush()  # noqa: SLF001
    assert spans.get_finished_spans()[0].status.status_code.name == "ERROR"
    telemetry.shutdown()


def test_telemetry_rejects_sensitive_or_unbounded_attributes() -> None:
    telemetry = Telemetry(TelemetryConfig())
    with pytest.raises(ValueError, match="not allow-listed"):
        with telemetry.stage(PipelineStage.RETRIEVE, identifiers={"query": "secret query"}):
            pass
    telemetry.shutdown()


def test_http_policy_and_queue_metrics_have_only_bounded_dimensions() -> None:
    reader = InMemoryMetricReader()
    telemetry = Telemetry(TelemetryConfig(), metric_readers=(reader,))
    telemetry.record_http("get", "/signals/{signal_id}", 200, 0.25)
    telemetry.record_policy_decision(
        PolicyDecision.DENY,
        resource_type="signal",
        reason_code="desk_mismatch",
    )
    telemetry.record_review_queue_age("claim", 60.0)

    points = _metric_points(reader)
    assert points["fi_intel.http.server.requests"][0].attributes["http.route"] == (
        "/signals/{signal_id}"
    )
    assert points["fi_intel.policy.decisions"][0].attributes["decision"] == "deny"
    assert points["fi_intel.review.queue.age"][0].attributes["subject.type"] == "claim"
    telemetry.shutdown()


def test_slo_targets_fail_closed_for_invalid_objectives() -> None:
    assert SLOTargets().entitlement_leaks == 0
    with pytest.raises(ValueError, match="ratios"):
        SLOTargets(citation_coverage=1.1)
    with pytest.raises(ValueError, match="remain zero"):
        SLOTargets(entitlement_leaks=1)
