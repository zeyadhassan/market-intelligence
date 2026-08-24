"""Evidence model: every claim carries a resolvable evidence ID.

An EvidenceItem resolves to a real document and character span
(invariant 7). `evidence_id` is `source_id/doc_id:start-end`, which is
both human-readable and machine-resolvable.
"""

from pydantic import BaseModel, ConfigDict, Field


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(min_length=1)
    source_id: str
    doc_id: str
    char_start: int
    char_end: int
    excerpt: str

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


class Opportunity(BaseModel):
    """Structured research output. `insufficient_evidence` is a blessed,
    expected outcome — the agent may always return nothing (invariant 8)."""

    model_config = ConfigDict(frozen=True)

    title: str
    entity_key: str
    summary: str
    falsifier: str = Field(
        min_length=1,
        description="What would prove this hypothesis wrong.",
    )
    evidence_ids: list[str]
    insufficient_evidence: bool = False
