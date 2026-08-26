"""Daily brief compiler with explicit, measurable research capacity.

Routing is patterns -> priority triage -> capacity admission -> deep research
-> assembly. Historical P90 usage is preferred; cold-start estimates are
conservative. Capacity pressure defers lower-ranked work and is visible in
the result. It never causes top-N padding or an unsupported narrative.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from fi_intel.agents.opportunity_research import OpportunityResearcher
from fi_intel.config import Settings
from fi_intel.governance.model_usage import (
    ModelCallEstimate,
    ModelCapacityLimits,
    ModelUsageLog,
    ModelUsageSnapshot,
)
from fi_intel.graph.registry import PatternRegistry, Signal
from fi_intel.logging import get_logger
from fi_intel.tools.evidence import EvidenceItem, Opportunity


class BudgetExceededError(RuntimeError):
    """Deprecated compatibility error; capacity now defers instead of aborting."""


class BriefItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal: Signal
    opportunity: Opportunity
    evidence: list[EvidenceItem]


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
        self._triage_priority_threshold = (
            settings or Settings()
        ).triage_priority_threshold
        self._log = get_logger(component="agents.brief")

    async def compile(self, as_of: datetime, desk: str, enabled: set[str] | None = None) -> Brief:
        signals = await self._registry.run(as_of, enabled=enabled)
        self._log.info("brief.patterns", fired=len(signals), desk=desk)
        if not signals:
            return Brief(as_of=as_of, desk=desk, items=[], nothing_material=True)

        high = [
            signal
            for signal in signals
            if signal.priority >= self._triage_priority_threshold
        ]
        self._log.info("brief.triage", kept=len(high), dropped=len(signals) - len(high))

        items: list[BriefItem] = []
        abstained: list[Signal] = []
        deferred: list[Signal] = []
        usage = await self._snapshot()
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

            # Book the projection even if best-effort usage logging is delayed
            # or fails. Observed values can only raise that capacity floor.
            projected = usage.project(estimate)
            opportunity, evidence = await self._researcher.research_signal(signal)
            usage = projected.conservative_merge(await self._snapshot())
            if opportunity.insufficient_evidence:
                abstained.append(signal)
                continue
            items.append(
                BriefItem(signal=signal, opportunity=opportunity, evidence=evidence)
            )

        self._log.info(
            "brief.deep_research",
            items=len(items),
            calls=usage.calls,
            tokens=usage.total_tokens,
            latency_ms=usage.latency_ms,
            cost_usd=usage.cost_usd,
        )
        return Brief(
            as_of=as_of,
            desk=desk,
            items=items,
            nothing_material=len(items) == 0,
            research_usage=usage,
            unresearched_signals=[
                signal
                for signal in signals
                if signal.priority < self._triage_priority_threshold
            ],
            deferred_signals=deferred,
            abstained_signals=abstained,
            coverage_complete=not deferred,
        )

    async def _snapshot(self) -> ModelUsageSnapshot:
        if self._usage_log is None or self._run_id is None:
            return ModelUsageSnapshot()
        return await self._usage_log.snapshot(self._run_id, "research")

    async def _estimate(self) -> ModelCallEstimate:
        fallback = self._limits.cold_start_estimate
        if self._usage_log is None:
            return fallback
        return await self._usage_log.estimate(
            "research", self._researcher.model_version, fallback
        )
