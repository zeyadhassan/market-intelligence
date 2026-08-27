"""Evidence model: every claim carries a resolvable evidence ID.

An EvidenceItem resolves to a real document and character span.
`evidence_id` is `source_id/doc_id:start-end`, which is
both human-readable and machine-resolvable.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OpportunityStatus(StrEnum):
    SUPPORTED = "supported"
    WATCH = "watch"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REJECTED = "rejected"
    HELD = "held"
    CONTRADICTED = "contradicted"


class OpportunityClaimKind(StrEnum):
    THESIS = "thesis"
    COMMERCIAL_ANGLE = "commercial_angle"
    TIMING = "timing"
    MATERIALITY = "materiality"
    CONTRADICTION = "contradiction"


class EvidenceStrength(StrEnum):
    STRONG = "strong"
    MIXED = "mixed"
    LIMITED = "limited"
    INSUFFICIENT = "insufficient"


class EntailmentStatus(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    NEEDS_SEMANTIC_REVIEW = "needs_semantic_review"
    REJECTED = "rejected"


class FieldEvidenceMapping(BaseModel):
    """Evidence ownership for one displayed material field."""

    model_config = ConfigDict(frozen=True)

    field_name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    derivation: str | None = None


class FalsifierTest(BaseModel):
    """A testable condition, rather than unconstrained narrative prose."""

    model_config = ConfigDict(frozen=True)

    condition: str = Field(min_length=1)
    observation: str = Field(default="authoritative contradictory evidence")
    deadline: str | None = None


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(min_length=1)
    source_id: str
    doc_id: str
    char_start: int
    char_end: int
    excerpt: str
    source_url: str | None = None
    source_version_id: str | None = None
    content_hash: str | None = None
    extraction_version: str | None = None
    lexical_score: float | None = None
    vector_score: float | None = None
    reranker_score: float | None = None
    fallback_tier: str = "canonical_entity"
    admission_reason: str = "entitlement-safe hybrid candidate"

    @classmethod
    def make_id(cls, source_id: str, doc_id: str, start: int, end: int) -> str:
        return f"{source_id}/{doc_id}:{start}-{end}"

    @classmethod
    def parse_id(cls, evidence_id: str) -> tuple[str, str, int, int]:
        try:
            src_doc, span = evidence_id.rsplit(":", 1)
            source_id, doc_id = src_doc.split("/", 1)
            start, end = span.split("-", 1)
            return source_id, doc_id, int(start), int(end)
        except ValueError as exc:
            msg = f"malformed evidence_id {evidence_id!r}"
            raise ValueError(msg) from exc


class GraphFact(BaseModel):
    """One typed graph assertion exposed to reasoning with source-span provenance."""

    model_config = ConfigDict(frozen=True)

    assertion_id: str
    predicate: str
    subject_type: str
    subject_key: str
    subject_name: str
    object_type: str
    object_key: str
    object_name: str
    properties: dict[str, str | int | float | bool] = Field(default_factory=dict)
    valid_from: str
    valid_to: str | None = None
    recorded_at: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_id: str
    evidence_index: int | None = None


class GraphPath(BaseModel):
    """One bounded typed path retaining topology and edge provenance."""

    model_config = ConfigDict(frozen=True)

    path_id: str
    hop_count: int = Field(ge=1, le=2)
    start_key: str
    end_key: str
    assertions: tuple[GraphFact, ...]
    contradiction: bool = False
    ambiguous: bool = False


class PrecedentEpisode(BaseModel):
    """One previously resolved signal episode and its recorded outcomes."""

    model_config = ConfigDict(frozen=True)

    signal_id: str
    pattern: str
    entity_key: str
    entity_name: str
    resolved_at: str
    outcome_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    outcome_status: str = "unknown_outcome"


class OpportunityClaim(BaseModel):
    """One independently publishable statement with its own citations."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    claim_type: OpportunityClaimKind = OpportunityClaimKind.THESIS
    evidence_ids: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: str = ""
    field_evidence: tuple[FieldEvidenceMapping, ...] = ()
    entailment_status: EntailmentStatus = EntailmentStatus.NEEDS_SEMANTIC_REVIEW


class Opportunity(BaseModel):
    """Structured research output; insufficient evidence is a valid outcome."""

    model_config = ConfigDict(frozen=True)

    title: str
    signal_id: str | None = None
    entity_key: str
    status: OpportunityStatus = OpportunityStatus.SUPPORTED
    summary: str
    falsifier: str = Field(
        min_length=1,
        description="What would prove this hypothesis wrong.",
    )
    falsifier_test: FalsifierTest | None = None
    evidence_ids: list[str]
    claims: list[OpportunityClaim] = Field(default_factory=list)
    prompt_version: str = "unknown"
    model_version: str = "unknown"
    schema_version: str = "opportunity-v2"
    insufficient_evidence: bool = False
    evidence_strength: EvidenceStrength = EvidenceStrength.LIMITED
    uncertainty_category: str = "uncalibrated"
