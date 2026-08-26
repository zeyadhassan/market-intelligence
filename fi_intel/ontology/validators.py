"""Deterministic admission checks for extracted claim candidates."""

from pydantic import BaseModel, ConfigDict, ValidationError

from fi_intel.ingest.extract import PREDICATE_PROPERTY_REQUIREMENTS, RawClaim
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.sources.canonical import CanonicalDocument, document_text


class ProposedType(BaseModel):
    """An out-of-vocabulary type queued for human review."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    doc_id: str
    proposed_name: str
    kind: str
    context_snippet: str
    extractor_version: str


class SemanticRejection(BaseModel):
    """One rejected claim and all deterministic rejection reasons."""

    model_config = ConfigDict(frozen=True)

    claim: RawClaim
    reasons: tuple[str, ...]


class ValidationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: list[RawClaim]
    rejected_offsets: list[RawClaim]
    rejected_semantics: list[SemanticRejection]
    proposed_types: list[ProposedType]


_DOMAIN_RANGE: dict[EdgeType, tuple[frozenset[NodeType], frozenset[NodeType]]] = {
    EdgeType.SUBSIDIARY_OF: (
        frozenset({NodeType.ORGANIZATION}),
        frozenset({NodeType.ORGANIZATION}),
    ),
    EdgeType.RATING_ACTION_ON: (
        frozenset({NodeType.ORGANIZATION}),
        frozenset({NodeType.RATING}),
    ),
    EdgeType.LEADERSHIP_CHANGE_AT: (
        frozenset({NodeType.PERSON, NodeType.EVENT}),
        frozenset({NodeType.ORGANIZATION}),
    ),
    EdgeType.PROGRAMME_APPROVED_BY: (
        frozenset({NodeType.PROGRAMME}),
        frozenset({NodeType.ORGANIZATION}),
    ),
    EdgeType.ISSUES: (
        frozenset({NodeType.ORGANIZATION}),
        frozenset({NodeType.INSTRUMENT}),
    ),
    EdgeType.MATURES_ON: (
        frozenset({NodeType.INSTRUMENT}),
        frozenset({NodeType.EVENT}),
    ),
    EdgeType.CALLABLE_ON: (
        frozenset({NodeType.INSTRUMENT}),
        frozenset({NodeType.EVENT}),
    ),
    EdgeType.REFINANCES: (
        frozenset({NodeType.INSTRUMENT, NodeType.PROGRAMME}),
        frozenset({NodeType.INSTRUMENT}),
    ),
    EdgeType.REPORTS_METRIC: (
        frozenset({NodeType.ORGANIZATION}),
        frozenset({NodeType.METRIC}),
    ),
    EdgeType.MANDATE_OF: (
        frozenset({NodeType.EVENT}),
        frozenset({NodeType.ORGANIZATION}),
    ),
}

def _semantic_reasons(claim: RawClaim) -> tuple[str, ...]:
    reasons: list[str] = []
    allowed_subjects, allowed_objects = _DOMAIN_RANGE[claim.predicate]
    if claim.subject.node_type not in allowed_subjects:
        reasons.append(
            f"subject type {claim.subject.node_type} is invalid for {claim.predicate}"
        )
    if claim.object.node_type not in allowed_objects:
        reasons.append(f"object type {claim.object.node_type} is invalid for {claim.predicate}")

    supplied = set(claim.properties.model_dump(exclude_none=True, by_alias=True))
    missing = PREDICATE_PROPERTY_REQUIREMENTS.get(claim.predicate, frozenset()) - supplied
    if missing:
        reasons.append(f"missing required properties: {', '.join(sorted(missing))}")

    if (
        claim.properties.maturity_date is not None
        and claim.properties.maturity_date != claim.valid_from.date()
    ):
        reasons.append("maturity_date does not match valid_from")
    if (
        claim.properties.first_call_date is not None
        and claim.properties.first_call_date != claim.valid_from.date()
    ):
        reasons.append("first_call_date does not match valid_from")
    if (
        claim.properties.currency is not None
        and claim.properties.currency != "USD"
        and (
            claim.properties.limit_usd_bn is not None
            or claim.properties.amount_usd_mn is not None
        )
    ):
        reasons.append("USD-denominated amount fields require currency USD")
    return tuple(reasons)


def validate_claims(
    claims: list[RawClaim], doc: CanonicalDocument, extractor_version: str
) -> ValidationOutcome:
    """Validate spans, graph domain/range, and predicate properties per claim."""
    del extractor_version
    accepted: list[RawClaim] = []
    rejected_offsets: list[RawClaim] = []
    rejected_semantics: list[SemanticRejection] = []
    for claim in claims:
        start, end = claim.snippet_offset
        text = document_text(doc)
        slice_ok = 0 <= start < end <= len(text) and text[start:end] == claim.snippet_text
        mention_ok = claim.subject.name.lower() in claim.snippet_text.lower()
        if not slice_ok or not mention_ok:
            rejected_offsets.append(claim)
            continue

        reasons = _semantic_reasons(claim)
        if reasons:
            rejected_semantics.append(SemanticRejection(claim=claim, reasons=reasons))
            continue
        accepted.append(claim)
    return ValidationOutcome(
        accepted=accepted,
        rejected_offsets=rejected_offsets,
        rejected_semantics=rejected_semantics,
        proposed_types=[],
    )


def proposed_type_from_validation_error(
    error: ValidationError, doc: CanonicalDocument, extractor_version: str
) -> ProposedType:
    """Turn a closed-enum validation failure into a review-queue entry."""
    kind = "edge"
    name = "unknown"
    for validation_error in error.errors():
        location = ".".join(str(part) for part in validation_error.get("loc", ()))
        value = validation_error.get("input", "unknown")
        if "predicate" in location:
            kind, name = "edge", str(value)
        elif "node_type" in location:
            kind, name = "node", str(value)
    return ProposedType(
        source_id=doc.source_id,
        doc_id=doc.doc_id,
        proposed_name=name,
        kind=kind,
        context_snippet=doc.body[:120],
        extractor_version=extractor_version,
    )
