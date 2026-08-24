"""Daily brief compiler: tiered routing with per-tier budget caps.

Routing: patterns -> triage (priority threshold) -> deep research (only
signals above threshold) -> assembly. Each tier carries a budget; the deep
research tier aborts the run if its projected cost exceeds the ceiling —
budget pressure must never push the system to fill a page (invariant 8),
and silent overspend is worse than an aborted run.

"A day with nothing material" is a first-class output: the brief says so
and does not pad.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from fi_intel.agents.opportunity_research import OpportunityResearcher
from fi_intel.graph.registry import PatternRegistry, Signal
from fi_intel.logging import get_logger
from fi_intel.tools.evidence import EvidenceItem, Opportunity
from fi_intel.tools.research_tools import ResearchTools

TRIAGE_PRIORITY_THRESHOLD = 60  # below this, signals are noted but not researched


class BudgetExceededError(RuntimeError):
    """Projected deep-research cost exceeded the configured ceiling."""


class Tier(StrEnum):
    PATTERNS = "patterns"
    TRIAGE = "triage"
    DEEP_RESEARCH = "deep_research"
    ASSEMBLY = "assembly"


class BriefItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal: Signal
    opportunity: Opportunity
    evidence: list[EvidenceItem]
    deep_researched: bool


class Brief(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: datetime
    desk: str
    items: list[BriefItem]
    deep_research_cost: float
    aborted_by_budget: bool
    nothing_material: bool


class BriefCompiler:
    def __init__(
        self,
        registry: PatternRegistry,
        tools: ResearchTools,
        researcher: OpportunityResearcher,
        budget_ceiling: float = 1000.0,
        per_signal_cost: float = 200.0,
    ) -> None:
        if budget_ceiling <= 0:
            msg = "budget_ceiling must be positive"
            raise ValueError(msg)
        if per_signal_cost <= 0:
            msg = "per_signal_cost must be positive"
            raise ValueError(msg)
        self._registry = registry
        self._tools = tools
        self._researcher = researcher
        self._ceiling = budget_ceiling
        self._per_signal = per_signal_cost
        self._log = get_logger(component="agents.brief")

    async def compile(
        self, as_of: datetime, desk: str, enabled: set[str] | None = None
    ) -> Brief:
        # patterns tier
        signals = await self._registry.run(as_of, enabled=enabled)
        self._log.info("brief.patterns", fired=len(signals), desk=desk)

        if not signals:
            return Brief(
                as_of=as_of,
                desk=desk,
                items=[],
                deep_research_cost=0.0,
                aborted_by_budget=False,
                nothing_material=True,
            )

        # triage tier: only high-priority signals go to deep research.
        high = [s for s in signals if s.priority >= TRIAGE_PRIORITY_THRESHOLD]
        self._log.info("brief.triage", kept=len(high), dropped=len(signals) - len(high))

        # deep research tier: projected cost vs ceiling; abort, never overspend.
        projected_cost = len(high) * self._per_signal
        if projected_cost > self._ceiling:
            self._log.info(
                "brief.budget_abort",
                projected=projected_cost,
                ceiling=self._ceiling,
                candidates=len(high),
            )
            raise BudgetExceededError(
                f"projected deep-research cost {projected_cost:.0f} "
                f"exceeds ceiling {self._ceiling:.0f}"
            )

        items: list[BriefItem] = []
        spent = 0.0
        for signal in high:
            opportunity, evidence = await self._researcher.research_signal(signal)
            spent += self._per_signal
            items.append(
                BriefItem(
                    signal=signal,
                    opportunity=opportunity,
                    evidence=evidence,
                    deep_researched=True,
                )
            )

        self._log.info("brief.deep_research", items=len(items), cost=spent)
        return Brief(
            as_of=as_of,
            desk=desk,
            items=items,
            deep_research_cost=spent,
            aborted_by_budget=False,
            nothing_material=len(items) == 0,
        )
