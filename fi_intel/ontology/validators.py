"""Extraction validators: closed-vocabulary enforcement and offset checks.

Two gates, both deterministic:

1. Vocabulary: a claim's predicate/node types are already the closed enums
   (the response schema constrains them), so an out-of-vocabulary value
   surfaces as a Pydantic ValidationError at parse time. The validator's
   job is to catch that and route it to the proposed_type review queue —
   never to auto-admit it (invariant 5).

2. Offsets: the claim's snippet_offset must slice real text out of the
   source document, and that slice must contain the subject mention. A
   claim whose offsets don't resolve is rejected, not written — a citation
   that doesn't resolve is a hallucination with good grammar.
"""

from pydantic import BaseModel, ConfigDict, ValidationError

from fi_intel.ingest.extract import RawClaim
from fi_intel.sources.canonical import CanonicalDocument


class ProposedType(BaseModel):
    """An out-of-vocabulary type the model tried to use. Queued for human
    review; never auto-admitted to the T-Box."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    proposed_name: str
    kind: str  # 'node' | 'edge'
    context_snippet: str
    extractor_version: str


class ValidationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: list[RawClaim]
    rejected_offsets: list[RawClaim]
    proposed_types: list[ProposedType]


def validate_claims(
    claims: list[RawClaim], doc: CanonicalDocument, extractor_version: str
) -> ValidationOutcome:
    """Split model output into accepted claims, offset rejections, and
    out-of-vocabulary proposals."""
    accepted: list[RawClaim] = []
    rejected: list[RawClaim] = []
    for claim in claims:
        start, end = claim.snippet_offset
        slice_ok = (
            0 <= start < end <= len(doc.body)
            and doc.body[start:end] == claim.snippet_text
        )
        mention_ok = claim.subject.name.lower() in claim.snippet_text.lower()
        if slice_ok and mention_ok:
            accepted.append(claim)
        else:
            rejected.append(claim)
    return ValidationOutcome(
        accepted=accepted, rejected_offsets=rejected, proposed_types=[]
    )


def proposed_type_from_validation_error(
    error: ValidationError, doc: CanonicalDocument, extractor_version: str
) -> ProposedType:
    """Turn a closed-enum validation failure into a review-queue entry."""
    # Find which field failed on an enum; default to edge/predicate.
    kind = "edge"
    name = "unknown"
    for err in error.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        value = err.get("input", "unknown")
        if "predicate" in loc:
            kind, name = "edge", str(value)
        elif "node_type" in loc:
            kind, name = "node", str(value)
    return ProposedType(
        doc_id=doc.doc_id,
        proposed_name=name,
        kind=kind,
        context_snippet=doc.body[:120],
        extractor_version=extractor_version,
    )
