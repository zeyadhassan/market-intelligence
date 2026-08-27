"""Daily brief compiler with explicit, measurable research capacity.

Routing is patterns -> priority triage -> capacity admission -> deep research
-> assembly. Historical P90 usage is preferred; cold-start estimates are
conservative. Capacity pressure defers lower-ranked work and is visible in
the result. It never causes top-N padding or an unsupported narrative.
"""

import asyncio
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from fi_intel.agents.investigation import InvestigationTrajectory
from fi_intel.agents.opportunity_research import OpportunityResearcher
from fi_intel.config import Settings
from fi_intel.governance.model_usage import (
    ModelCallEstimate,
    ModelCapacityLimits,
    ModelUsageLog,
    ModelUsageSnapshot,
)
from fi_intel.graph.coverage import DetectorCoverageGap
from fi_intel.graph.registry import PatternRegistry, Signal
from fi_intel.logging import get_logger, safe_error_summary
from fi_intel.tools.evidence import EvidenceItem, Opportunity


class BudgetExceededError(RuntimeError):
    """Deprecated compatibility error; capacity now defers instead of aborting."""


class BriefItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal: Signal
    opportunity: Opportunity
    evidence: list[EvidenceItem]
    investigation: InvestigationTrajectory | None = None


class SignalResearchFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal: Signal
    state: str
    error_type: str
    safe_error_summary: str


class TriageScoreDistribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    threshold: int = Field(ge=0, le=100)
    signal_count: int = Field(ge=0)
    at_or_above_threshold: int = Field(ge=0)
    below_threshold: int = Field(ge=0)
    minimum: int | None = Field(default=None, ge=0, le=100)
    median: float | None = Field(default=None, ge=0.0, le=100.0)
    maximum: int | None = Field(default=None, ge=0, le=100)


class Brief(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: datetime
    desk: str
    items: list[BriefItem]
    nothing_material: bool
    research_usage: ModelUsageSnapshot = Field(default_factory=ModelUsageSnapshot)
    unresearched_signals: list[Signal] = Field(default_factory=list)
    deferred_signals: list[Signal] = Field(default_factory=list)
    abstained_signals: list[Signal] = Field(default_factory=list)
    failed_signals: list[SignalResearchFailure] = Field(default_factory=list)
    dark_detectors: list[DetectorCoverageGap] = Field(default_factory=list)
    triage_scores: TriageScoreDistribution
    coverage_complete: bool = True

    @property
    def deep_research_cost(self) -> float:
        """Actual metered spend, retained as a compatibility accessor."""

        return self.research_usage.cost_usd


class BriefCompiler:
    def __init__(
        self,
        registry: PatternRegistry,
        researcher: OpportunityResearcher,
        *,
        capacity_limits: ModelCapacityLimits | None = None,
        usage_log: ModelUsageLog | None = None,
        run_id: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        if (usage_log is None) != (run_id is None):
            raise ValueError("usage_log and run_id must be supplied together")
        self._registry = registry
        self._researcher = researcher
        self._limits = capacity_limits or ModelCapacityLimits()
        self._usage_log = usage_log
        self._run_id = run_id
        active_settings = settings or Settings()
        self._triage_priority_threshold = active_settings.triage_priority_threshold
        self._signal_concurrency = active_settings.daily_signal_concurrency
        self._signal_timeout_seconds = active_settings.daily_signal_timeout_seconds
        self._log = get_logger(component="agents.brief")

    async def compile(self, as_of: datetime, desk: str, enabled: set[str] | None = None) -> Brief:
        signals = await self._registry.run(as_of, enabled=enabled)
        coverage_gaps = list(self._registry.last_coverage_gaps)
        triage_scores = self._score_distribution(signals)
        self._log.info("brief.patterns", fired=len(signals), desk=desk)
        if not signals:
            return Brief(
                as_of=as_of,
                desk=desk,
                items=[],
                nothing_material=not coverage_gaps,
                dark_detectors=coverage_gaps,
                triage_scores=triage_scores,
                coverage_complete=not coverage_gaps,
            )

        high = [signal for signal in signals if signal.priority >= self._triage_priority_threshold]
        self._log.info("brief.triage", kept=len(high), dropped=len(signals) - len(high))

        items: list[BriefItem] = []
        abstained: list[Signal] = []
        deferred: list[Signal] = []
        failures: list[SignalResearchFailure] = []
        usage = await self._snapshot()
        admitted: list[Signal] = []
        for index, signal in enumerate(high):
            estimate = await self._estimate()
            if not self._limits.allows(usage, estimate):
                deferred.extend(high[index:])
                self._log.info(
                    "brief.capacity_deferred",
                    calls=usage.calls,
                    tokens=usage.total_tokens,
                    latency_ms=usage.latency_ms,
                    deferred=len(deferred),
                )
                break
            usage = usage.project(estimate)
            admitted.append(signal)

        semaphore = asyncio.Semaphore(self._signal_concurrency)

        async def research_one(
            signal: Signal,
        ) -> tuple[
            Signal,
            Opportunity | None,
            list[EvidenceItem],
            InvestigationTrajectory | None,
            Exception | None,
        ]:
            try:
                async with semaphore, asyncio.timeout(self._signal_timeout_seconds):
                    opportunity, evidence = await self._researcher.research_signal(signal)
                return signal, opportunity, evidence, self._researcher.last_trajectory, None
            except Exception as exc:  # per-signal isolation is the harness boundary
                return signal, None, [], self._researcher.last_trajectory, exc

        researched = await asyncio.gather(*(research_one(signal) for signal in admitted))
        for signal, opportunity, evidence, trajectory, error in researched:
            if error is not None:
                failures.append(
                    SignalResearchFailure(
                        signal=signal,
                        state=(trajectory.state.value if trajectory else "failed_retryable"),
                        error_type=type(error).__name__,
                        safe_error_summary=safe_error_summary(error),
                    )
                )
                self._log.warning(
                    "brief.signal_failed",
                    signal_id=signal.signal_id,
                    error_type=type(error).__name__,
                )
                continue
            if opportunity is None:
                raise RuntimeError("research task returned neither a result nor a failure")
            if opportunity.insufficient_evidence:
                abstained.append(signal)
                continue
            items.append(
                BriefItem(
                    signal=signal,
                    opportunity=opportunity,
                    evidence=evidence,
                    investigation=trajectory,
                )
            )

        # Observed values can only raise the conservative capacity projection.
        usage = usage.conservative_merge(await self._snapshot())

        self._log.info(
            "brief.deep_research",
            items=len(items),
            calls=usage.calls,
            tokens=usage.total_tokens,
            latency_ms=usage.latency_ms,
            cost_usd=usage.cost_usd,
        )
        coverage_complete = not deferred and not coverage_gaps and not failures
        return Brief(
            as_of=as_of,
            desk=desk,
            items=items,
            nothing_material=len(items) == 0 and coverage_complete,
            research_usage=usage,
            unresearched_signals=[
                signal for signal in signals if signal.priority < self._triage_priority_threshold
            ],
            deferred_signals=deferred,
            abstained_signals=abstained,
            failed_signals=failures,
            dark_detectors=coverage_gaps,
            triage_scores=triage_scores,
            coverage_complete=coverage_complete,
        )

    def _score_distribution(self, signals: list[Signal]) -> TriageScoreDistribution:
        scores = sorted(signal.priority for signal in signals)
        count = len(scores)
        median = None
        if count:
            middle = count // 2
            median = (
                float(scores[middle]) if count % 2 else (scores[middle - 1] + scores[middle]) / 2.0
            )
        at_or_above = sum(score >= self._triage_priority_threshold for score in scores)
        return TriageScoreDistribution(
            threshold=self._triage_priority_threshold,
            signal_count=count,
            at_or_above_threshold=at_or_above,
            below_threshold=count - at_or_above,
            minimum=scores[0] if scores else None,
            median=median,
            maximum=scores[-1] if scores else None,
        )

    async def _snapshot(self) -> ModelUsageSnapshot:
        if self._usage_log is None or self._run_id is None:
            return ModelUsageSnapshot()
        return await self._usage_log.snapshot(self._run_id, "research")

    async def _estimate(self) -> ModelCallEstimate:
        fallback = self._limits.cold_start_estimate
        if self._usage_log is None:
            return fallback
        return await self._usage_log.estimate("research", self._researcher.model_version, fallback)
