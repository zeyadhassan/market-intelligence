"""Ledger-first assertion admission contracts."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fi_intel.application.assertion_admission import LedgerAssertionAdmissionSink
from fi_intel.ingest.store import InMemoryDocumentStore
from fi_intel.ledger import (
    AccessPolicy,
    DocumentIdentity,
    DocumentVersion,
    InMemoryIntelligenceLedger,
    OutboxEvent,
    RawAsset,
    document_identity_id,
    document_version_id,
    outbox_event_id,
    raw_asset_id,
)
from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.sources.base import FetchCursor
from fi_intel.sources.canonical import BarrierSide, CanonicalDocument, DocumentClass

NOW = datetime(2026, 8, 27, 10, tzinfo=UTC)


async def test_accepted_assertion_is_authoritative_before_projection() -> None:
    policy = AccessPolicy(
        policy_id=uuid4(),
        barrier_side=BarrierSide.PUBLIC,
        allowed_entitlement_groups=frozenset({"fi_gcc_public"}),
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    ledger = InMemoryIntelligenceLedger()
    await ledger.register_policy(policy)
    source_id = "official"
    external_id = "announcement-1"
    document_id = document_identity_id(source_id, external_id)
    asset_id = raw_asset_id(source_id, external_id, "rev-1")
    text_hash = hashlib.sha256(b"Programme approved\nExample Bank programme approved.").hexdigest()
    version_id = document_version_id(document_id, "rev-1", text_hash)
    raw = RawAsset(
        raw_asset_id=asset_id,
        source_id=source_id,
        external_id=external_id,
        source_revision="rev-1",
        object_uri=f"raw/{asset_id}",
        content_hash=hashlib.sha256(b"raw").hexdigest(),
        media_type="text/html",
        fetched_at=NOW,
        policy_id=policy.policy_id,
    )
    version = DocumentVersion(
        document_version_id=version_id,
        document_id=document_id,
        raw_asset_id=asset_id,
        version_number=1,
        source_revision="rev-1",
        normalized_object_uri=f"normalized/{version_id}",
        normalized_text_hash=text_hash,
        title="Programme approved",
        language="en",
        document_class=DocumentClass.REGULATORY,
        published_at=NOW,
        recorded_at=NOW,
        parser_version="official-html-v1",
        policy_id=policy.policy_id,
    )
    event = OutboxEvent(
        event_id=outbox_event_id("document.versioned.v1", version_id, 1),
        event_type="document.versioned.v1",
        aggregate_type="document",
        aggregate_id=version_id,
        aggregate_version=1,
        occurred_at=NOW,
        correlation_id=uuid4(),
        policy_id=policy.policy_id,
        payload={"document_version_id": str(version_id)},
    )
    await ledger.commit_document_version(
        raw,
        DocumentIdentity(
            document_id=document_id,
            source_id=source_id,
            external_id=external_id,
            created_at=NOW,
        ),
        version,
        event,
    )
    documents = InMemoryDocumentStore()
    document = CanonicalDocument(
        source_id=source_id,
        doc_id=str(version_id),
        title="Programme approved",
        body="Example Bank programme approved.",
        published_at=NOW,
        recorded_at=NOW,
        document_class=DocumentClass.REGULATORY,
        mentioned_names=("Example Bank",),
    )
    await documents.commit_batch(
        [document],
        [],
        FetchCursor(source_id=source_id, position="rev-1", updated_at=NOW),
    )
    quote = "Example Bank programme approved."
    start = len(document.title) + 1
    assertion = Assertion(
        predicate=EdgeType.PROGRAMME_APPROVED_BY,
        subject=EntityRef(
            node_type=NodeType.PROGRAMME,
            key="programme:example-1",
            display_name="Example programme",
        ),
        object=EntityRef(
            node_type=NodeType.ORGANIZATION,
            key="529900EXAMPLE00000001",
            display_name="Example Bank",
        ),
        source_id=source_id,
        source_doc_id=str(version_id),
        barrier_side=BarrierSide.PUBLIC,
        policy_version="source-policy-v1",
        snippet_offset=(start, start + len(quote)),
        extractor_version="extract-v2",
        confidence=0.99,
        valid_from=NOW,
        recorded_at=NOW,
        properties={"programme": "example-1", "status": "approved"},
    )
    sink = LedgerAssertionAdmissionSink(
        ledger,
        documents,
        policy=policy,
        correlation_id=event.correlation_id,
    )

    admitted_id = await sink.write(assertion)
    await sink.write(assertion)

    assert UUID(admitted_id) in ledger._assertions  # noqa: SLF001
    accepted = ledger._assertions[UUID(admitted_id)]  # noqa: SLF001
    assert accepted.evidence_span_ids
    assert accepted.qualifiers["status"] == "approved"
    events = await ledger.pending_events()
    assert sum(item.event_type == "assertion.accepted.v1" for item in events) == 1
    projection = next(
        item.payload["projection"] for item in events if item.event_type == "assertion.accepted.v1"
    )
    assert Assertion.model_validate(projection) == assertion
