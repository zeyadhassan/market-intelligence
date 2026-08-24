"""Opportunity research: from a fired signal to a structured Opportunity.

This is the FI-specific rewiring of the research loop. The stages mirror
the milestone's node list; the reasoning model is a protocol so unit tests
stub it and assert on the constructed request. When evidence is
insufficient, the agent returns an insufficient_evidence Opportunity — a
blessed, explicit outcome (invariant 8), not a narrative.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from fi_intel.graph.registry import Signal
from fi_intel.logging import get_logger
from fi_intel.tools.evidence import EvidenceItem, Opportunity
from fi_intel.tools.research_tools import ResearchTools

RESEARCH_PROMPT_VERSION = "research-v1"


class ResearchRequest(BaseModel):
    """The exact prompt sent to the reasoning model. Tests assert on this."""

    model_config = ConfigDict(frozen=True)

    prompt_version: str
    signal_pattern: str
    entity_name: str
    evidence_excerpts: list[str]
    instruction: str


class ResearchResponse(BaseModel):
    """What the reasoning model returns."""

    model_config = ConfigDict(frozen=True)

    title: str
    summary: str
    falsifier: str
    # Indices into the evidence_excerpts list it was shown — the model may
    # only cite evidence it was given, never invent citations.
    evidence_indices: list[int]
    insufficient_evidence: bool = False


@runtime_checkable
class ReasoningModel(Protocol):
    async def research(self, request: ResearchRequest) -> ResearchResponse: ...


RESEARCH_INSTRUCTION = (
    "You are a research analyst for a Financial Institutions desk. Given a "
    "signal and evidence excerpts, decide whether there is a real business "
    "opportunity. Every claim must cite an evidence excerpt by index. State "
    "what would falsify the hypothesis. If the evidence does not support a "
    "conclusion, set insufficient_evidence=true rather than constructing a "
    "narrative. Returning nothing is a valid, expected outcome."
)


class OpportunityResearcher:
    def __init__(self, tools: ResearchTools, model: ReasoningModel) -> None:
        self._tools = tools
        self._model = model
        self._log = get_logger(component="agents.opportunity_research")

    async def research_signal(self, signal: Signal) -> tuple[Opportunity, list[EvidenceItem]]:
        """signal_intake -> graph_context_hydration -> precedent_retrieval
        -> hypothesis_scoring -> (compliance_gate is validate_opportunity)."""
        # graph_context_hydration: pull the entity's graph state.
        profile = await self._tools.entity_profile(signal.entity_key)

        # precedent_retrieval: corpus evidence ABOUT THIS ENTITY. An
        # entity-scoped query is the honest corroboration check; a free-text
        # search over the whole corpus returns near-uniform noise for an
        # unknown entity, which would masquerade as evidence.
        evidence = await self._tools.corpus_search(
            signal.entity_name, limit=10, entity_lei=signal.entity_key
        )

        self._log.info(
            "research.signal",
            pattern=signal.pattern,
            entity=signal.entity_name,
            evidence_count=len(evidence),
            graph_assertions=profile["assertion_count"],
        )

        # Insufficient evidence = no graph assertions AND no corpus documents
        # for the entity. Either alone is enough to proceed.
        if profile["assertion_count"] == 0 and not evidence:
            return (
                Opportunity(
                    title=f"Insufficient evidence: {signal.pattern} on {signal.entity_name}",
                    entity_key=signal.entity_key,
                    summary="No graph assertions or corroborating documents for this entity.",
                    falsifier="Evidence about this entity entering the corpus or graph.",
                    evidence_ids=[],
                    insufficient_evidence=True,
                ),
                [],
            )

        # hypothesis_scoring: ask the reasoning model, citing only shown evidence.
        request = ResearchRequest(
            prompt_version=RESEARCH_PROMPT_VERSION,
            signal_pattern=signal.pattern,
            entity_name=signal.entity_name,
            evidence_excerpts=[e.excerpt for e in evidence],
            instruction=RESEARCH_INSTRUCTION,
        )
        response = await self._model.research(request)

        # The model may only cite evidence indices it was shown.
        cited = [evidence[i] for i in response.evidence_indices if i < len(evidence)]
        opportunity = Opportunity(
            title=response.title,
            entity_key=signal.entity_key,
            summary=response.summary,
            falsifier=response.falsifier,
            evidence_ids=[e.evidence_id for e in cited],
            insufficient_evidence=response.insufficient_evidence,
        )
        return opportunity, cited
