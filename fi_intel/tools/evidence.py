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


class OpportunityClaimKind(StrEnum):
    THESIS = "thesis"
    COMMERCIAL_ANGLE = "commercial_angle"
    TIMING = "timing"
    MATERIALITY = "materiality"
    CONTRADICTION = "contradiction"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(min_length=1)
    source_id: str
    doc_id: str
    char_start: int
    char_end: int
    excerpt: str
    source_url: str | None = None

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
    properties: dict[str, str] = Field(default_factory=dict)
    valid_from: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_id: str
    evidence_index: int | None = None


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


class OpportunityClaim(BaseModel):
    """One independently publishable statement with its own citations."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    claim_type: OpportunityClaimKind = OpportunityClaimKind.THESIS
    evidence_ids: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: str = ""


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
    evidence_ids: list[str]
    claims: list[OpportunityClaim] = Field(default_factory=list)
    prompt_version: str = "unknown"
    model_version: str = "unknown"
    schema_version: str = "opportunity-v2"
    insufficient_evidence: bool = False
