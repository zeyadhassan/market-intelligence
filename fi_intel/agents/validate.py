"""Fail-closed validation for claim-level publication evidence."""

from datetime import datetime

from fi_intel.agents.grounding import ground_claim, title_agrees_with_claims
from fi_intel.governance.policy import GraphAccessContext
from fi_intel.ingest.store import DocumentStore
from fi_intel.sources.canonical import document_text
from fi_intel.tools.evidence import EntailmentStatus, EvidenceItem, Opportunity


class EvidenceValidationError(ValueError):
    """Raised when an output carries evidence that cannot be published."""


def _validate_claim_citations(opportunity: Opportunity) -> set[str]:
    if not opportunity.evidence_ids:
        msg = f"opportunity {opportunity.title!r} makes claims with no evidence IDs"
        raise EvidenceValidationError(msg)
    if not opportunity.claims:
        msg = f"opportunity {opportunity.title!r} has no atomic claims"
        raise EvidenceValidationError(msg)
    expected_summary = " ".join(claim.text for claim in opportunity.claims)
    if opportunity.summary != expected_summary:
        msg = "opportunity summary contains prose outside its atomic cited claims"
        raise EvidenceValidationError(msg)

    global_ids = set(opportunity.evidence_ids)
    claim_ids: set[str] = set()
    for claim in opportunity.claims:
        if not claim.evidence_ids:
            msg = f"claim {claim.text!r} has no evidence IDs"
            raise EvidenceValidationError(msg)
        unknown = set(claim.evidence_ids) - global_ids
        if unknown:
            msg = f"claim {claim.text!r} cites IDs outside the opportunity: {sorted(unknown)}"
            raise EvidenceValidationError(msg)
        claim_ids.update(claim.evidence_ids)
    if claim_ids != global_ids:
        orphaned = sorted(global_ids - claim_ids)
        msg = f"opportunity carries citations unused by any atomic claim: {orphaned}"
        raise EvidenceValidationError(msg)
    return global_ids


def _expected_evidence(
    evidence: list[EvidenceItem] | None, global_ids: set[str]
) -> dict[str, EvidenceItem]:
    expected = {item.evidence_id: item for item in evidence or []}
    if evidence is not None and set(expected) != global_ids:
        msg = "provided evidence bundle does not exactly match opportunity citations"
        raise EvidenceValidationError(msg)
    return expected


async def _validate_resolvable(
    evidence_ids: list[str],
    store: DocumentStore,
    expected: dict[str, EvidenceItem],
    as_of: datetime | None,
    access: GraphAccessContext | None,
) -> dict[str, EvidenceItem]:
    resolved_items: dict[str, EvidenceItem] = {}
    for evidence_id in evidence_ids:
        source_id, doc_id, start, end = EvidenceItem.parse_id(evidence_id)
        if access is not None and source_id not in access.allowed_source_ids:
            msg = f"evidence_id {evidence_id!r} is outside the caller's source grants"
            raise EvidenceValidationError(msg)
        doc = await store.load_document(source_id, doc_id)
        if doc is None:
            msg = f"unresolvable evidence_id {evidence_id!r}: no such document"
            raise EvidenceValidationError(msg)
        if as_of is not None and doc.recorded_at > as_of:
            msg = f"unresolvable evidence_id {evidence_id!r}: document was not known at as_of"
            raise EvidenceValidationError(msg)
        if (
            access is not None
            and str(doc.barrier_side) == "private"
            and str(access.principal.side) != "private"
        ):
            msg = f"evidence_id {evidence_id!r} crosses the information barrier"
            raise EvidenceValidationError(msg)
        text = document_text(doc)
        if not (0 <= start < end <= len(text)):
            msg = f"unresolvable evidence_id {evidence_id!r}: span out of range"
            raise EvidenceValidationError(msg)
        expected_item = expected.get(evidence_id)
        if expected_item is not None and text[start:end] != expected_item.excerpt:
            msg = f"unresolvable evidence_id {evidence_id!r}: excerpt does not match span"
            raise EvidenceValidationError(msg)
        resolved_items[evidence_id] = expected_item or EvidenceItem(
            evidence_id=evidence_id,
            source_id=source_id,
            doc_id=doc_id,
            source_url=doc.url,
            char_start=start,
            char_end=end,
            excerpt=text[start:end],
            content_hash=doc.content_hash(),
        )
    return resolved_items


async def validate_opportunity(
    opportunity: Opportunity,
    store: DocumentStore,
    evidence: list[EvidenceItem] | None = None,
    *,
    as_of: datetime | None = None,
    access: GraphAccessContext | None = None,
    require_semantic_entailment: bool = False,
) -> Opportunity:
    """Resolve every atomic citation against persisted source text."""
    if opportunity.insufficient_evidence:
        if opportunity.evidence_ids or opportunity.claims:
            msg = "insufficient-evidence opportunity must not carry claims or citations"
            raise EvidenceValidationError(msg)
        return opportunity

    global_ids = _validate_claim_citations(opportunity)
    expected = _expected_evidence(evidence, global_ids)
    resolved = await _validate_resolvable(opportunity.evidence_ids, store, expected, as_of, access)
    for claim in opportunity.claims:
        decision = ground_claim(claim.text, [resolved[item] for item in claim.evidence_ids])
        if not decision.supported:
            msg = f"claim {claim.text!r} is not entailed: {'; '.join(decision.reasons)}"
            raise EvidenceValidationError(msg)
        if require_semantic_entailment and (
            decision.requires_semantic_review
            or claim.entailment_status is EntailmentStatus.NEEDS_SEMANTIC_REVIEW
        ):
            raise EvidenceValidationError(
                f"claim {claim.text!r} requires a governed semantic entailment decision"
            )
        if claim.entailment_status is EntailmentStatus.REJECTED:
            raise EvidenceValidationError(f"claim {claim.text!r} failed semantic verification")
    if not title_agrees_with_claims(
        opportunity.title, [claim.text for claim in opportunity.claims]
    ):
        raise EvidenceValidationError("opportunity title disagrees with its validated claims")
    return opportunity
