"""Opportunity research: from a fired signal to a structured Opportunity.

The reasoning model is exposed through a protocol so callers can select an
implementation. Insufficient evidence produces an explicit result.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from fi_intel.agents.validate import validate_opportunity
from fi_intel.graph.registry import Signal
from fi_intel.ingest.store import DocumentStore
from fi_intel.logging import get_logger
from fi_intel.tools.evidence import (
    EvidenceItem,
    GraphFact,
    Opportunity,
    OpportunityClaim,
    OpportunityClaimKind,
    OpportunityStatus,
)
from fi_intel.tools.research_tools import ResearchTools

RESEARCH_PROMPT_VERSION = "research-v2"


class EvidenceCitationError(ValueError):
    """A model response cited evidence outside its immutable request bundle."""


class ResearchRequest(BaseModel):
    """Versioned request sent to the reasoning model."""

    model_config = ConfigDict(frozen=True)

    prompt_version: str
    signal_pattern: str
    entity_name: str
    signal_evidence: dict[str, str] = Field(default_factory=dict)
    profile_predicates: list[str] = Field(default_factory=list)
    profile_assertions: list[GraphFact] = Field(default_factory=list)
    evidence_excerpts: list[str]
    instruction: str


class ResearchClaim(BaseModel):
    """One atomic model-authored statement with evidence indices."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    claim_type: OpportunityClaimKind = OpportunityClaimKind.THESIS
    evidence_indices: list[int]
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: str = ""


class ResearchResponse(BaseModel):
    """Structured model output. Material prose lives only in cited claims."""

    model_config = ConfigDict(frozen=True)

    title: str
    status: OpportunityStatus = OpportunityStatus.SUPPORTED
    claims: list[ResearchClaim] = Field(default_factory=list)
    falsifier: str
    insufficient_evidence: bool = False


@runtime_checkable
class ReasoningModel(Protocol):
    async def research(self, request: ResearchRequest) -> ResearchResponse: ...


RESEARCH_INSTRUCTION = (
    "You are a research analyst for a Financial Institutions desk. Given a "
    "signal, typed graph assertions, and evidence excerpts, decide whether "
    "there is a real business "
    "opportunity. Return atomic claims; every claim must cite one or more "
    "evidence excerpts by index and classify it as thesis, commercial_angle, "
    "timing, materiality, or contradiction. State "
    "what would falsify the hypothesis. If the evidence does not support a "
    "conclusion, set insufficient_evidence=true rather than constructing a "
    "narrative. Returning nothing is a valid, expected outcome."
)


class OpportunityResearcher:
    def __init__(
        self,
        tools: ResearchTools,
        model: ReasoningModel,
        publication_store: DocumentStore,
    ) -> None:
        self._tools = tools
        self._model = model
        self._publication_store = publication_store
        self._model_version = str(getattr(model, "model_version", "unreported"))
        self._log = get_logger(component="agents.opportunity_research")

    @property
    def model_version(self) -> str:
        return self._model_version

    async def _validate(
        self,
        opportunity: Opportunity,
        evidence: list[EvidenceItem],
        signal: Signal,
    ) -> tuple[Opportunity, list[EvidenceItem]]:
        await validate_opportunity(
            opportunity,
            self._publication_store,
            evidence,
            as_of=signal.as_of,
            access=self._tools.access,
        )
        return opportunity, evidence

    async def _abstain(
        self, signal: Signal, summary: str, falsifier: str
    ) -> tuple[Opportunity, list[EvidenceItem]]:
        opportunity = Opportunity(
            title=f"Insufficient evidence: {signal.pattern} on {signal.entity_name}",
            signal_id=signal.signal_id,
            entity_key=signal.entity_key,
            status=OpportunityStatus.INSUFFICIENT_EVIDENCE,
            summary=summary,
            falsifier=falsifier,
            evidence_ids=[],
            claims=[],
            prompt_version=RESEARCH_PROMPT_VERSION,
            model_version=self._model_version,
            insufficient_evidence=True,
        )
        return await self._validate(opportunity, [], signal)

    @staticmethod
    def _materialize_supported(
        response: ResearchResponse,
        signal: Signal,
        evidence: list[EvidenceItem],
    ) -> tuple[Opportunity, list[EvidenceItem]]:
        if not response.claims:
            raise EvidenceCitationError("supported research response contained no atomic claims")

        cited_by_id: dict[str, EvidenceItem] = {}
        claims: list[OpportunityClaim] = []
        for claim in response.claims:
            if not claim.evidence_indices:
                raise EvidenceCitationError(f"claim {claim.text!r} contained no citations")
            invalid = [
                index for index in claim.evidence_indices if index < 0 or index >= len(evidence)
            ]
            if invalid:
                raise EvidenceCitationError(
                    f"claim {claim.text!r} cited invalid evidence indices {invalid}"
                )
            claim_ids = list(
                dict.fromkeys(evidence[index].evidence_id for index in claim.evidence_indices)
            )
            for index in claim.evidence_indices:
                item = evidence[index]
                cited_by_id.setdefault(item.evidence_id, item)
            claims.append(
                OpportunityClaim(
                    text=claim.text,
                    claim_type=claim.claim_type,
                    evidence_ids=claim_ids,
                    confidence=claim.confidence,
                    uncertainty=claim.uncertainty,
                )
            )

        cited = list(cited_by_id.values())
        return (
            Opportunity(
                title=response.title,
                signal_id=signal.signal_id,
                entity_key=signal.entity_key,
                status=response.status,
                summary=" ".join(claim.text for claim in claims),
                falsifier=response.falsifier,
                evidence_ids=[item.evidence_id for item in cited],
                claims=claims,
                prompt_version=RESEARCH_PROMPT_VERSION,
                model_version="pending",
                insufficient_evidence=False,
            ),
            cited,
        )

    async def research_signal(self, signal: Signal) -> tuple[Opportunity, list[EvidenceItem]]:
        """signal_intake -> graph_context_hydration -> precedent_retrieval
        -> hypothesis_scoring -> (compliance_gate is validate_opportunity)."""
        unauthorized_triggers = set(signal.source_ids) - self._tools.access.allowed_source_ids
        if unauthorized_triggers:
            raise EvidenceCitationError(
                f"signal contains unauthorized triggering sources {sorted(unauthorized_triggers)}"
            )

        # graph_context_hydration: pull the entity's graph state.
        profile = await self._tools.entity_profile(signal.entity_key)

        # precedent_retrieval: corpus evidence ABOUT THIS ENTITY. An
        # entity-scoped query is the honest corroboration check; a free-text
        # search over the whole corpus returns near-uniform noise for an
        # unknown entity, which would masquerade as evidence.
        structured_query = " ".join(
            [
                signal.entity_name,
                signal.pattern.replace("_", " "),
                *signal.evidence.values(),
            ]
        )
        corpus_evidence = await self._tools.corpus_search(
            structured_query, limit=10, entity_lei=signal.entity_key
        )

        graph_evidence_raw = profile.get("evidence", [])
        graph_evidence = (
            [item for item in graph_evidence_raw if isinstance(item, EvidenceItem)]
            if isinstance(graph_evidence_raw, list)
            else []
        )
        evidence_by_id = {
            item.evidence_id: item for item in [*graph_evidence, *corpus_evidence]
        }
        evidence = list(evidence_by_id.values())

        profile_assertions_raw = profile.get("assertions", [])
        profile_assertions = (
            [item for item in profile_assertions_raw if isinstance(item, GraphFact)]
            if isinstance(profile_assertions_raw, list)
            else []
        )
        evidence_indices = {
            item.evidence_id: index for index, item in enumerate(evidence)
        }
        profile_assertions = [
            fact.model_copy(update={"evidence_index": evidence_indices.get(fact.evidence_id)})
            for fact in profile_assertions
        ]

        self._log.info(
            "research.signal",
            pattern=signal.pattern,
            entity=signal.entity_name,
            evidence_count=len(evidence),
            graph_assertions=profile["assertion_count"],
        )

        # Graph facts may justify detection, but publication requires text
        # evidence that the caller is authorized to inspect and cite.
        if not evidence:
            return await self._abstain(
                signal,
                "No authorized, citable document evidence was retrieved.",
                "Authorized evidence about this entity entering the corpus.",
            )

        # hypothesis_scoring: ask the reasoning model, citing only shown evidence.
        profile_predicates_raw = profile.get("predicates", [])
        profile_predicates = (
            [str(predicate) for predicate in profile_predicates_raw]
            if isinstance(profile_predicates_raw, list)
            else []
        )
        request = ResearchRequest(
            prompt_version=RESEARCH_PROMPT_VERSION,
            signal_pattern=signal.pattern,
            entity_name=signal.entity_name,
            signal_evidence=signal.evidence,
            profile_predicates=profile_predicates,
            profile_assertions=profile_assertions,
            evidence_excerpts=[e.excerpt for e in evidence],
            instruction=RESEARCH_INSTRUCTION,
        )
        response = await self._model.research(request)

        if response.insufficient_evidence:
            return await self._abstain(
                signal,
                "The reasoning stage abstained because the evidence was insufficient.",
                response.falsifier,
            )
        if response.status in {
            OpportunityStatus.INSUFFICIENT_EVIDENCE,
            OpportunityStatus.REJECTED,
        }:
            return await self._abstain(
                signal,
                "The reasoning stage declined to publish a supported opportunity.",
                response.falsifier,
            )
        opportunity, cited = self._materialize_supported(response, signal, evidence)
        opportunity = opportunity.model_copy(update={"model_version": self._model_version})
        return await self._validate(opportunity, cited, signal)
