"""Explicit bridge from v1 canonical adapters into the v2 application path.

This module is for demos and staged migration only. It serializes an already
canonical v1 document as the raw payload, so it cannot provide original vendor
bytes or headers. Production source connectors must construct
``RawSourceEnvelope`` directly at the network boundary.
"""

from fi_intel.application.raw import RawHeader, RawSourceEnvelope
from fi_intel.ledger.models import AccessPolicy
from fi_intel.sources.canonical import CanonicalDocument

PROTOTYPE_CANONICAL_MEDIA_TYPE = "application/vnd.fi-intel.prototype-canonical+json"


def envelope_from_v1_canonical(
    document: CanonicalDocument,
    *,
    source_revision: str,
    access_policy: AccessPolicy,
) -> RawSourceEnvelope:
    """Wrap a v1 canonical document without pretending it is source-original."""
    if document.barrier_side is not access_policy.barrier_side:
        raise ValueError("document barrier differs from compatibility policy")
    return RawSourceEnvelope(
        source_id=document.source_id,
        external_id=document.doc_id,
        source_revision=source_revision,
        payload=document.model_dump_json().encode("utf-8"),
        media_type=PROTOTYPE_CANONICAL_MEDIA_TYPE,
        headers=(RawHeader(name="x-fi-intel-compatibility", value="v1-canonical"),),
        fetched_at=document.recorded_at,
        source_published_at=document.published_at,
        access_policy=access_policy,
    )


class PrototypeCanonicalizer:
    """Decode the explicit v1 compatibility envelope."""

    async def canonicalize(self, envelope: RawSourceEnvelope) -> CanonicalDocument:
        if envelope.media_type != PROTOTYPE_CANONICAL_MEDIA_TYPE:
            raise ValueError("prototype canonicalizer received real raw media")
        return CanonicalDocument.model_validate_json(envelope.payload)
