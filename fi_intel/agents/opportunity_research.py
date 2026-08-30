"""Opportunity research: from a fired signal to a structured Opportunity.

The reasoning model is exposed through a protocol so callers can select an
implementation. Insufficient evidence produces an explicit result.
"""

from contextvars import ContextVar
from datetime import timedelta
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from fi_intel.agents.entailment import EntailmentVerifier
from fi_intel.agents.grounding import ground_claim
from fi_intel.agents.investigation import (
    InMemoryInvestigationStore,
    InvestigationBudgetError,
    InvestigationPolicy,
    InvestigationSession,
    InvestigationState,
    InvestigationStore,
    InvestigationTrajectory,
    StopReason,
)
from fi_intel.agents.validate import validate_opportunity
from fi_intel.entities.identifiers import IdentifierScheme
from fi_intel.entities.models import EntityType
from fi_intel.graph.entry import (
    GraphEntityReference,
    GraphEntryRequest,
    GraphEntryStatus,
)
from fi_intel.graph.registry import Signal
from fi_intel.ingest.store import DocumentStore
from fi_intel.logging import get_logger
from fi_intel.retrieval.planning import EvidencePolarity, RetrievalQueryPlan
from fi_intel.tools.evidence import (
    EntailmentStatus,
    EvidenceItem,
    EvidenceStrength,
    FalsifierTest,
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
    graph_paths: list[dict[str, object]] = Field(default_factory=list)
    timeseries: list[dict[str, str]] = Field(default_factory=list)
    precedents: list[dict[str, object]] = Field(default_factory=list)
    evidence_excerpts: list[str]
    contradiction_evidence_indices: list[int] = Field(default_factory=list)
    candidate_hypotheses: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
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
    "timing, materiality, or contradiction. You must inspect the separately "
    "retrieved contradiction candidates before supporting an opportunity. State "
    "what would falsify the hypothesis. If the evidence does not support a "
    "conclusion, set insufficient_evidence=true rather than constructing a "
    "narrative. Use the typed graph paths, time series, and governed precedents "
    "when supplied, but never treat a precedent's condition resolution as a won "
    "commercial outcome. Returning nothing is a valid, expected outcome."
)


_NEIGHBORHOOD_PREDICATES = frozenset(
    {
        "ISSUES",
        "MATURES_ON",
        "CALLABLE_ON",
        "REFINANCES",
        "RATING_ACTION_ON",
        "REPORTS_METRIC",
        "LEADERSHIP_CHANGE_AT",
        "PROGRAMME_APPROVED_BY",
    }
)


class OpportunityResearcher:
    def __init__(
        self,
        tools: ResearchTools,
        model: ReasoningModel,
        publication_store: DocumentStore,
        *,
        investigation_store: InvestigationStore | None = None,
        investigation_policy: InvestigationPolicy | None = None,
        entailment_verifier: EntailmentVerifier | None = None,
        require_semantic_entailment: bool = False,
        run_id: str = "local-research",
    ) -> None:
        self._tools = tools
        self._model = model
        self._publication_store = publication_store
        self._model_version = str(getattr(model, "model_version", "unreported"))
        self._investigation_store = investigation_store or InMemoryInvestigationStore()
        self._investigation_policy = investigation_policy or InvestigationPolicy()
        self._run_id = run_id
        self._entailment_verifier = entailment_verifier
        self._require_semantic_entailment = require_semantic_entailment
        if require_semantic_entailment and entailment_verifier is None:
            raise ValueError("governed semantic entailment requires a verifier")
        self._last_trajectory: ContextVar[InvestigationTrajectory | None] = ContextVar(
            f"last_investigation_{id(self)}", default=None
        )
        self._log = get_logger(component="agents.opportunity_research")

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def last_trajectory(self) -> InvestigationTrajectory | None:
        return self._last_trajectory.get()

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
            require_semantic_entailment=self._require_semantic_entailment,
        )
        return opportunity, evidence

    async def _verify_semantic_claims(
        self,
        opportunity: Opportunity,
        evidence: list[EvidenceItem],
    ) -> Opportunity:
        verifier = self._entailment_verifier
        if verifier is None:
            return opportunity
        evidence_by_id = {item.evidence_id: item for item in evidence}
        claims: list[OpportunityClaim] = []
        for claim in opportunity.claims:
            if claim.entailment_status is EntailmentStatus.SUPPORTED:
                claims.append(claim)
                continue
            decision = await verifier.verify(
                claim.text,
                [evidence_by_id[item] for item in claim.evidence_ids],
            )
            if decision.status is not EntailmentStatus.SUPPORTED:
                raise EvidenceCitationError(
                    f"semantic entailment rejected claim {claim.text!r}: {decision.reason}"
                )
            claims.append(
                claim.model_copy(update={"entailment_status": EntailmentStatus.SUPPORTED})
            )
        return opportunity.model_copy(
            update={
                "claims": claims,
                "summary": " ".join(claim.text for claim in claims),
            }
        )

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
            grounding = ground_claim(
                claim.text,
                [evidence[index] for index in claim.evidence_indices],
            )
            claims.append(
                OpportunityClaim(
                    text=claim.text,
                    claim_type=claim.claim_type,
                    evidence_ids=claim_ids,
                    confidence=claim.confidence,
                    uncertainty=claim.uncertainty,
                    field_evidence=grounding.mappings,
                    entailment_status=(
                        EntailmentStatus.SUPPORTED
                        if grounding.supported and not grounding.requires_semantic_review
                        else EntailmentStatus.NEEDS_SEMANTIC_REVIEW
                    ),
                )
            )

        cited = list(cited_by_id.values())
        return (
            Opportunity(
                # Analyst-visible titles cannot introduce uncited prose.
                title=claims[0].text,
                signal_id=signal.signal_id,
                entity_key=signal.entity_key,
                status=response.status,
                summary=" ".join(claim.text for claim in claims),
                falsifier=response.falsifier,
                falsifier_test=FalsifierTest(condition=response.falsifier),
                evidence_ids=[item.evidence_id for item in cited],
                claims=claims,
                prompt_version=RESEARCH_PROMPT_VERSION,
                model_version="pending",
                insufficient_evidence=False,
                evidence_strength=(
                    EvidenceStrength.MIXED
                    if any(
                        claim.claim_type is OpportunityClaimKind.CONTRADICTION for claim in claims
                    )
                    else EvidenceStrength.STRONG
                ),
                uncertainty_category=(
                    "mixed" if response.status is OpportunityStatus.WATCH else "bounded"
                ),
            ),
            cited,
        )

    async def research_signal(  # noqa: C901
        self, signal: Signal
    ) -> tuple[Opportunity, list[EvidenceItem]]:
        """Execute a bounded evidence-first trajectory and persist every step."""

        session = await InvestigationSession.start(
            run_id=self._run_id,
            signal_id=signal.signal_id,
            store=self._investigation_store,
            policy=self._investigation_policy,
        )
        try:
            unauthorized_triggers = set(signal.source_ids) - self._tools.access.allowed_source_ids
            if unauthorized_triggers:
                await session.finish(
                    InvestigationState.FAILED_TERMINAL,
                    StopReason.POLICY_REJECTED,
                )
                raise EvidenceCitationError(
                    "signal contains unauthorized triggering sources "
                    f"{sorted(unauthorized_triggers)}"
                )

            resolved_entity_key = signal.entity_key
            if self._tools.supports_graph_entry:
                entry = await session.run_step(
                    "graph_entry",
                    {
                        "entity_key": signal.entity_key,
                        "entity_name": signal.entity_name,
                        "as_of": signal.as_of.isoformat(),
                    },
                    lambda: self._tools.resolve_graph_entry(
                        GraphEntryRequest(
                            principal=self._tools.access.principal,
                            as_of=signal.as_of,
                            reference=GraphEntityReference(
                                display_name=signal.entity_name,
                                entity_type=EntityType.ORGANIZATION,
                                identifier_scheme=IdentifierScheme.LEI,
                                identifier_value=signal.entity_key,
                            ),
                            signal_id=signal.signal_id,
                            allowed_relation_families=_NEIGHBORHOOD_PREDICATES,
                        )
                    ),
                )
                if entry.status is not GraphEntryStatus.RESOLVED or entry.lei is None:
                    result = await self._abstain(
                        signal,
                        "The signal entity could not be resolved without ambiguity.",
                        "A reviewed entity link resolves the identity ambiguity.",
                    )
                    await session.finish(
                        InvestigationState.ABSTAINED,
                        StopReason.AMBIGUOUS_IDENTITY,
                    )
                    return result
                resolved_entity_key = entry.lei

            profile = await session.run_step(
                "entity_profile",
                {"entity_key": resolved_entity_key, "as_of": signal.as_of.isoformat()},
                lambda: self._tools.entity_profile(resolved_entity_key),
            )
            graph_paths = (
                await session.run_step(
                    "entity_neighborhood",
                    {
                        "entity_key": resolved_entity_key,
                        "allowed_predicates": sorted(_NEIGHBORHOOD_PREDICATES),
                        "max_hops": 2,
                    },
                    lambda: self._tools.entity_neighborhood(
                        resolved_entity_key,
                        allowed_predicates=_NEIGHBORHOOD_PREDICATES,
                        max_hops=2,
                    ),
                )
                if self._tools.supports_neighborhood
                else []
            )
            structured_query = " ".join(
                [
                    signal.entity_name,
                    signal.pattern.replace("_", " "),
                    *signal.evidence.values(),
                ]
            )
            search_plan = RetrievalQueryPlan(
                entity_name=signal.entity_name,
                canonical_entity_lei=resolved_entity_key,
                event_type=signal.pattern,
                date_from=(signal.as_of - timedelta(days=730)).date(),
                date_to=signal.as_of.date(),
                support_terms=tuple(signal.evidence.values()),
                limit=10,
            )
            planned_search = getattr(self._tools, "planned_corpus_search", None)
            if callable(planned_search) and self._tools.supports_planned_search:
                corpus_evidence = await session.run_step(
                    "support_search",
                    {"plan": search_plan, "polarity": EvidencePolarity.SUPPORT},
                    lambda: planned_search(
                        search_plan,
                        polarity=EvidencePolarity.SUPPORT,
                    ),
                )
            else:
                corpus_evidence = await session.run_step(
                    "support_search",
                    {"query": structured_query, "limit": 10, "entity_lei": signal.entity_key},
                    lambda: self._tools.corpus_search(
                        structured_query,
                        limit=10,
                        entity_lei=resolved_entity_key,
                    ),
                )
            contradiction_plan = search_plan.model_copy(update={"limit": 5})
            if callable(planned_search) and self._tools.supports_planned_search:
                contradiction_evidence = await session.run_step(
                    "contradiction_search",
                    {"plan": contradiction_plan, "polarity": EvidencePolarity.CONTRADICTION},
                    lambda: planned_search(
                        contradiction_plan,
                        polarity=EvidencePolarity.CONTRADICTION,
                    ),
                )
            else:
                legacy_contradiction_search = getattr(self._tools, "contradiction_search", None)
                contradiction_evidence = (
                    await session.run_step(
                        "contradiction_search",
                        {
                            "query": structured_query,
                            "limit": 5,
                            "entity_lei": signal.entity_key,
                        },
                        lambda: legacy_contradiction_search(
                            structured_query,
                            limit=5,
                            entity_lei=resolved_entity_key,
                        ),
                    )
                    if callable(legacy_contradiction_search)
                    else []
                )

            graph_evidence_raw = profile.get("evidence", [])
            graph_evidence = (
                [item for item in graph_evidence_raw if isinstance(item, EvidenceItem)]
                if isinstance(graph_evidence_raw, list)
                else []
            )
            evidence_by_id = {
                item.evidence_id: item
                for item in [*graph_evidence, *corpus_evidence, *contradiction_evidence]
            }
            evidence = list(evidence_by_id.values())
            contradiction_ids = {item.evidence_id for item in contradiction_evidence}
            contradiction_indices = [
                index
                for index, item in enumerate(evidence)
                if item.evidence_id in contradiction_ids
            ]

            profile_assertions_raw = profile.get("assertions", [])
            profile_assertions = (
                [item for item in profile_assertions_raw if isinstance(item, GraphFact)]
                if isinstance(profile_assertions_raw, list)
                else []
            )
            evidence_indices = {item.evidence_id: index for index, item in enumerate(evidence)}
            profile_assertions = [
                fact.model_copy(update={"evidence_index": evidence_indices.get(fact.evidence_id)})
                for fact in profile_assertions
            ]
            timeseries: list[dict[str, str]] = []
            if (
                signal.pattern == "negative_rating_action_with_capital_decline"
                and self._tools.supports_timeseries
            ):
                timeseries = await session.run_step(
                    "timeseries_lookup",
                    {"entity_key": resolved_entity_key, "metric": "cet1"},
                    lambda: self._tools.timeseries_lookup(resolved_entity_key, "cet1"),
                )
            precedents = (
                await session.run_step(
                    "precedent_search",
                    {"query": f"{signal.entity_name} {signal.pattern}", "limit": 5},
                    lambda: self._tools.precedent_search(
                        f"{signal.entity_name} {signal.pattern}", limit=5
                    ),
                )
                if self._tools.supports_precedents
                else []
            )
            self._log.info(
                "research.signal",
                pattern=signal.pattern,
                entity=signal.entity_name,
                evidence_count=len(evidence),
                contradiction_count=len(contradiction_indices),
                graph_assertions=profile.get("assertion_count", 0),
            )
            if not evidence:
                result = await self._abstain(
                    signal,
                    "No authorized, citable document evidence was retrieved.",
                    "Authorized evidence about this entity entering the corpus.",
                )
                await session.finish(
                    InvestigationState.ABSTAINED,
                    StopReason.INSUFFICIENT_EVIDENCE,
                )
                return result

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
                graph_paths=[path.model_dump(mode="json") for path in graph_paths],
                timeseries=timeseries,
                precedents=[item.model_dump(mode="json") for item in precedents],
                evidence_excerpts=[item.excerpt for item in evidence],
                contradiction_evidence_indices=contradiction_indices,
                candidate_hypotheses=[signal.hypothesis or signal.pattern.replace("_", " ")],
                required_evidence=["support", "contradiction search", "temporal validity"],
                instruction=RESEARCH_INSTRUCTION,
            )
            response = await session.run_step(
                "reasoning",
                request,
                lambda: self._model.research(request),
            )
            if response.insufficient_evidence or response.status in {
                OpportunityStatus.INSUFFICIENT_EVIDENCE,
                OpportunityStatus.REJECTED,
            }:
                result = await self._abstain(
                    signal,
                    "The reasoning stage declined to publish a supported opportunity.",
                    response.falsifier,
                )
                await session.finish(
                    InvestigationState.ABSTAINED,
                    StopReason.INSUFFICIENT_EVIDENCE,
                )
                return result
            opportunity, cited = self._materialize_supported(response, signal, evidence)
            opportunity = opportunity.model_copy(update={"model_version": self._model_version})
            if any(
                claim.entailment_status is EntailmentStatus.NEEDS_SEMANTIC_REVIEW
                for claim in opportunity.claims
            ):
                opportunity = await session.run_step(
                    "entailment",
                    {
                        "claim_ids": [
                            index
                            for index, claim in enumerate(opportunity.claims)
                            if claim.entailment_status is EntailmentStatus.NEEDS_SEMANTIC_REVIEW
                        ],
                        "verifier": (
                            self._entailment_verifier.model_version
                            if self._entailment_verifier is not None
                            else "not-configured"
                        ),
                    },
                    lambda: self._verify_semantic_claims(opportunity, cited),
                )
            validated = await session.run_step(
                "validation",
                opportunity,
                lambda: self._validate(opportunity, cited, signal),
            )
            contradicted = response.status is OpportunityStatus.CONTRADICTED
            await session.finish(
                (InvestigationState.CONTRADICTED if contradicted else InvestigationState.SUPPORTED),
                StopReason.CONTRADICTED if contradicted else StopReason.SUPPORTED,
            )
            return validated
        except InvestigationBudgetError:
            await session.finish(InvestigationState.DEFERRED, StopReason.BUDGET_EXHAUSTED)
            return await self._abstain(
                signal,
                "Investigation stopped at its governed budget.",
                "Additional authorized investigation capacity becomes available.",
            )
        except Exception as exc:
            if session.trajectory.state is InvestigationState.RUNNING:
                await session.finish(
                    (
                        InvestigationState.FAILED_TERMINAL
                        if isinstance(exc, (ValueError, TypeError))
                        else InvestigationState.FAILED_RETRYABLE
                    ),
                    StopReason.TOOL_FAILURE,
                )
            raise
        finally:
            self._last_trajectory.set(session.trajectory)
