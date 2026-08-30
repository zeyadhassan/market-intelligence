"""Contract tests for the versioned evidence-ledger foundation."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from fi_intel.ledger import (
    AccessPolicy,
    AssertionObject,
    ClaimCandidate,
    ClaimDecision,
    ClaimDecisionType,
    ClaimObject,
    ClaimObjectKind,
    DocumentIdentity,
    DocumentVersion,
    EntityIdentity,
    EntityLinkDecision,
    EntityLinkStatus,
    EvidenceSpan,
    InMemoryIntelligenceLedger,
    IntelligenceAssertion,
    LedgerConflictError,
    LedgerInvariantError,
    Mention,
    MentionKind,
    OutboxEvent,
    RawAsset,
    SignalIdentity,
    SignalStatus,
    SignalTransition,
    derive_access_policy,
    document_identity_id,
    document_version_id,
    raw_asset_id,
    signal_identity_id,
)
from fi_intel.sources.canonical import BarrierSide, DocumentClass

NOW = datetime(2025, 5, 3, 12, tzinfo=UTC)
CONTENT_HASH = hashlib.sha256(b"source bytes").hexdigest()
TEXT_HASH = hashlib.sha256(b"Example Bank raised capital.").hexdigest()


def _policy(side: BarrierSide = BarrierSide.PUBLIC) -> AccessPolicy:
    return AccessPolicy(
        policy_id=uuid4(),
        barrier_side=side,
        allowed_entitlement_groups=frozenset({"fi_public", "fi_private"}),
        created_at=NOW,
    )


def _event(
    aggregate_id: UUID,
    policy_id: UUID,
    event_type: str,
    *,
    version: int = 1,
) -> OutboxEvent:
    return OutboxEvent(
        event_id=uuid4(),
        event_type=event_type,
        aggregate_type=event_type.split(".", maxsplit=1)[0],
        aggregate_id=aggregate_id,
        aggregate_version=version,
        occurred_at=NOW,
        correlation_id=uuid4(),
        policy_id=policy_id,
        payload={"aggregate_id": str(aggregate_id)},
    )


def _document_records(
    policy_id: UUID,
    *,
    revision: str = "1",
    version_number: int = 1,
    supersedes: UUID | None = None,
) -> tuple[RawAsset, DocumentIdentity, DocumentVersion]:
    source_id = "contract_source"
    external_id = "external-42"
    document_id = document_identity_id(source_id, external_id)
    asset_id = raw_asset_id(source_id, external_id, revision)
    content_hash = hashlib.sha256(f"source bytes {revision}".encode()).hexdigest()
    text_hash = hashlib.sha256(f"normalized {revision}".encode()).hexdigest()
    return (
        RawAsset(
            raw_asset_id=asset_id,
            source_id=source_id,
            external_id=external_id,
            source_revision=revision,
            object_uri=f"s3://raw/{asset_id}",
            content_hash=content_hash,
            media_type="text/html",
            fetched_at=NOW,
            policy_id=policy_id,
        ),
        DocumentIdentity(
            document_id=document_id,
            source_id=source_id,
            external_id=external_id,
            created_at=NOW,
        ),
        DocumentVersion(
            document_version_id=document_version_id(document_id, revision, text_hash),
            document_id=document_id,
            raw_asset_id=asset_id,
            version_number=version_number,
            source_revision=revision,
            normalized_object_uri=f"s3://normalized/{asset_id}",
            normalized_text_hash=text_hash,
            title="Example Bank update",
            language="en",
            document_class=DocumentClass.FILING,
            published_at=NOW,
            recorded_at=NOW,
            parser_version="parser-v1",
            policy_id=policy_id,
            supersedes_version_id=supersedes,
        ),
    )


def test_stable_ids_preserve_document_versions_and_signal_identity() -> None:
    document_id = document_identity_id("wire", "story-1")
    assert document_id == document_identity_id("wire", "story-1")
    assert document_id != document_identity_id("wire", "story-2")

    version_one = document_version_id(document_id, "1", "a" * 64)
    correction = document_version_id(document_id, "2", "b" * 64)
    assert version_one != correction

    entity_id = uuid4()
    signal_id = signal_identity_id("liquidity-pressure", "2", entity_id)
    assert signal_id == signal_identity_id("liquidity-pressure", "2", entity_id)
    assert signal_id != signal_identity_id("liquidity-pressure", "3", entity_id)


async def test_policy_derivation_is_private_and_never_widens_audience() -> None:
    public = AccessPolicy(
        policy_id=uuid4(),
        barrier_side=BarrierSide.PUBLIC,
        allowed_entitlement_groups=frozenset({"public", "private"}),
        created_at=NOW,
    )
    private = AccessPolicy(
        policy_id=uuid4(),
        barrier_side=BarrierSide.PRIVATE,
        allowed_entitlement_groups=frozenset({"private", "legal"}),
        created_at=NOW,
    )
    derived, lineage = derive_access_policy(
        (public, private), created_at=NOW, reason="research evidence bundle"
    )
    assert derived.barrier_side is BarrierSide.PRIVATE
    assert derived.allowed_entitlement_groups == frozenset({"private"})
    assert {edge.input_policy_id for edge in lineage} == {
        public.policy_id,
        private.policy_id,
    }

    ledger = InMemoryIntelligenceLedger()
    await ledger.register_policy(public)
    await ledger.register_policy(private)
    await ledger.register_policy(derived, lineage)

    raw, document, version = _document_records(private.policy_id)
    widened_version = version.model_copy(update={"policy_id": public.policy_id})
    event = _event(
        widened_version.document_version_id,
        public.policy_id,
        "document.versioned.v1",
    )
    with pytest.raises(LedgerInvariantError, match="widens|private barrier"):
        await ledger.commit_document_version(raw, document, widened_version, event)
    assert await ledger.pending_events() == []


async def test_document_corrections_are_contiguous_atomic_and_idempotent() -> None:
    ledger = InMemoryIntelligenceLedger()
    policy = _policy()
    await ledger.register_policy(policy)

    raw_one, document, version_one = _document_records(policy.policy_id)
    event_one = _event(
        version_one.document_version_id,
        policy.policy_id,
        "document.versioned.v1",
    )
    await ledger.commit_document_version(raw_one, document, version_one, event_one)
    await ledger.commit_document_version(raw_one, document, version_one, event_one)
    assert await ledger.pending_events() == [event_one]

    raw_three, _, version_three = _document_records(
        policy.policy_id,
        revision="3",
        version_number=3,
        supersedes=version_one.document_version_id,
    )
    event_three = _event(
        version_three.document_version_id,
        policy.policy_id,
        "document.versioned.v1",
        version=3,
    )
    with pytest.raises(LedgerInvariantError, match="contiguous"):
        await ledger.commit_document_version(raw_three, document, version_three, event_three)
    assert event_three not in await ledger.pending_events()

    raw_two, _, version_two = _document_records(
        policy.policy_id,
        revision="2",
        version_number=2,
        supersedes=version_one.document_version_id,
    )
    event_two = _event(
        version_two.document_version_id,
        policy.policy_id,
        "document.versioned.v1",
        version=2,
    )
    await ledger.commit_document_version(raw_two, document, version_two, event_two)
    assert {item.event_id for item in await ledger.pending_events()} == {
        event_one.event_id,
        event_two.event_id,
    }

    conflicting_raw = raw_two.model_copy(update={"media_type": "application/pdf"})
    with pytest.raises(LedgerConflictError, match="immutable ID"):
        await ledger.commit_document_version(conflicting_raw, document, version_two, event_two)


def test_evidence_span_hash_is_bound_to_the_exact_quote() -> None:
    with pytest.raises(ValidationError, match="quote_hash does not match quote"):
        EvidenceSpan(
            evidence_span_id=uuid4(),
            document_version_id=uuid4(),
            char_start=0,
            char_end=12,
            quote="actual quote",
            quote_hash="0" * 64,
            recorded_at=NOW,
            policy_id=uuid4(),
        )


async def test_claim_to_signal_flow_has_provenance_and_guarded_lifecycle() -> None:
    ledger = InMemoryIntelligenceLedger()
    policy = _policy()
    await ledger.register_policy(policy)

    raw, document, version = _document_records(policy.policy_id)
    await ledger.commit_document_version(
        raw,
        document,
        version,
        _event(version.document_version_id, policy.policy_id, "document.versioned.v1"),
    )

    entity = EntityIdentity(
        entity_id=uuid4(),
        entity_type="organization",
        canonical_name="Example Bank",
        created_at=NOW,
        policy_id=policy.policy_id,
    )
    await ledger.register_entity(
        entity,
        _event(entity.entity_id, policy.policy_id, "entity.registered.v1"),
    )

    mention = Mention(
        mention_id=uuid4(),
        document_version_id=version.document_version_id,
        kind=MentionKind.ORGANIZATION,
        surface="Example Bank",
        char_start=0,
        char_end=12,
        extractor_bundle_version="extractor-v2",
        recorded_at=NOW,
        policy_id=policy.policy_id,
    )
    quote = "Example Bank raised capital."
    span = EvidenceSpan(
        evidence_span_id=uuid4(),
        document_version_id=version.document_version_id,
        char_start=0,
        char_end=len(quote),
        quote=quote,
        quote_hash=hashlib.sha256(quote.encode()).hexdigest(),
        recorded_at=NOW,
        policy_id=policy.policy_id,
    )
    candidate = ClaimCandidate(
        candidate_id=uuid4(),
        document_version_id=version.document_version_id,
        subject_mention_id=mention.mention_id,
        predicate="raised_capital",
        object=ClaimObject(kind=ClaimObjectKind.MONEY, value={"amount": 500, "ccy": "USD"}),
        qualifiers={"capital_type": "at1"},
        event_time=NOW,
        valid_from=NOW,
        evidence_span_ids=(span.evidence_span_id,),
        extractor_bundle_version="extractor-v2",
        confidence=0.94,
        recorded_at=NOW,
        policy_id=policy.policy_id,
    )
    await ledger.commit_extraction(
        (mention,),
        (span,),
        (candidate,),
        _event(version.document_version_id, policy.policy_id, "extraction.completed.v1"),
    )

    link = EntityLinkDecision(
        decision_id=uuid4(),
        mention_id=mention.mention_id,
        status=EntityLinkStatus.LINKED,
        entity_id=entity.entity_id,
        candidate_entity_ids=(entity.entity_id,),
        confidence=0.999,
        resolver_version="resolver-v2",
        reason="exact registered identifier",
        decided_at=NOW,
        decided_by="resolver",
        policy_id=policy.policy_id,
    )
    await ledger.commit_entity_links(
        (link,),
        _event(mention.mention_id, policy.policy_id, "entity.links-decided.v1"),
    )

    assertion = IntelligenceAssertion(
        assertion_id=uuid4(),
        candidate_id=candidate.candidate_id,
        subject_entity_id=entity.entity_id,
        subject_entity_link_id=link.entity_link_id,
        predicate=candidate.predicate,
        object=AssertionObject(
            kind=ClaimObjectKind.MONEY,
            value={"amount": 500, "ccy": "USD"},
        ),
        qualifiers=candidate.qualifiers,
        event_time=NOW,
        valid_from=NOW,
        recorded_at=NOW,
        evidence_span_ids=candidate.evidence_span_ids,
        confidence=0.93,
        ontology_version="fi-ontology-v2",
        policy_id=policy.policy_id,
    )
    decision = ClaimDecision(
        decision_id=uuid4(),
        candidate_id=candidate.candidate_id,
        decision=ClaimDecisionType.ACCEPTED,
        assertion_id=assertion.assertion_id,
        reasons=("schema and evidence validation passed",),
        validator_bundle_version="validator-v2",
        decided_at=NOW,
        decided_by="validator",
        policy_id=policy.policy_id,
    )
    await ledger.commit_claim_decision(
        decision,
        assertion,
        _event(candidate.candidate_id, policy.policy_id, "claim.decided.v1"),
    )

    signal_id = signal_identity_id("capital-raise", "2", entity.entity_id, scope_key="capital")
    signal = SignalIdentity(
        signal_id=signal_id,
        pattern_id="capital-raise",
        pattern_version="2",
        subject_entity_id=entity.entity_id,
        scope_key="capital",
        created_at=NOW,
        policy_id=policy.policy_id,
    )
    candidate_transition = SignalTransition(
        transition_id=uuid4(),
        signal_id=signal_id,
        from_status=None,
        to_status=SignalStatus.CANDIDATE,
        occurred_at=NOW,
        as_of=NOW,
        score=0.88,
        contributing_assertion_ids=(assertion.assertion_id,),
        reason="pattern conditions met",
        actor="pattern-engine",
        policy_id=policy.policy_id,
    )
    candidate_event = _event(signal_id, policy.policy_id, "signal.transitioned.v1")
    await ledger.commit_signal_transition(
        signal,
        candidate_transition,
        candidate_event,
    )
    await ledger.commit_signal_transition(signal, candidate_transition, candidate_event)
    assert await ledger.signal_history(signal_id) == [candidate_transition]

    invalid_event = _event(
        signal_id,
        policy.policy_id,
        "signal.transitioned.v1",
        version=2,
    )
    invalid_transition = SignalTransition(
        transition_id=uuid4(),
        signal_id=signal_id,
        from_status=SignalStatus.CANDIDATE,
        to_status=SignalStatus.PUBLISHED,
        occurred_at=NOW,
        as_of=NOW,
        contributing_assertion_ids=(assertion.assertion_id,),
        reason="attempted lifecycle bypass",
        actor="test",
        policy_id=policy.policy_id,
    )
    with pytest.raises(LedgerInvariantError, match="not allowed"):
        await ledger.commit_signal_transition(signal, invalid_transition, invalid_event)
    assert await ledger.signal_history(signal_id) == [candidate_transition]
    assert invalid_event not in await ledger.pending_events()

    confirmed = SignalTransition(
        transition_id=uuid4(),
        signal_id=signal_id,
        from_status=SignalStatus.CANDIDATE,
        to_status=SignalStatus.CONFIRMED,
        occurred_at=NOW.replace(minute=1),
        as_of=NOW,
        score=0.9,
        contributing_assertion_ids=(assertion.assertion_id,),
        reason="deterministic confirmation passed",
        actor="pattern-engine",
        policy_id=policy.policy_id,
    )
    await ledger.commit_signal_transition(
        signal,
        confirmed,
        _event(
            signal_id,
            policy.policy_id,
            "signal.transitioned.v1",
            version=2,
        ),
    )
    assert [item.to_status for item in await ledger.signal_history(signal_id)] == [
        SignalStatus.CANDIDATE,
        SignalStatus.CONFIRMED,
    ]
