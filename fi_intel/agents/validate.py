"""Output validation: every evidence ID must resolve to a real document
and character span. Output with an unresolvable ID fails and is not
published (invariant 7).
"""

from fi_intel.ingest.store import DocumentStore
from fi_intel.tools.evidence import EvidenceItem, Opportunity


class EvidenceValidationError(ValueError):
    """Raised when an output carries an evidence ID that does not resolve."""


async def validate_opportunity(
    opportunity: Opportunity, store: DocumentStore
) -> Opportunity:
    """Resolve every evidence ID against the persisted corpus.

    Returns the opportunity unchanged if all IDs resolve; raises otherwise.
    An opportunity with insufficient_evidence=True and no IDs is valid —
    "we found nothing" needs no citations.
    """
    if opportunity.insufficient_evidence:
        return opportunity
    if not opportunity.evidence_ids:
        msg = f"opportunity {opportunity.title!r} makes claims with no evidence IDs"
        raise EvidenceValidationError(msg)
    for evidence_id in opportunity.evidence_ids:
        source_id, doc_id, start, end = EvidenceItem.parse_id(evidence_id)
        docs = await store.load_documents(source_id)
        doc = next((d for d in docs if d.doc_id == doc_id), None)
        if doc is None:
            msg = f"unresolvable evidence_id {evidence_id!r}: no such document"
            raise EvidenceValidationError(msg)
        text = doc.title + "\n" + doc.body
        if not (0 <= start < end <= len(text)):
            msg = f"unresolvable evidence_id {evidence_id!r}: span out of range"
            raise EvidenceValidationError(msg)
    return opportunity
