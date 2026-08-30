"""Ledger-first admission and rebuildable graph projection for assertions."""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID, uuid5

from pydantic import JsonValue

from fi_intel.ingest.store import DocumentStore
from fi_intel.ledger.models import (
    AccessPolicy,
    AssertionObject,
    ClaimCandidate,
    ClaimDecision,
    ClaimDecisionType,
    ClaimObject,
    ClaimObjectKind,
    EntityIdentity,
    EntityLinkDecision,
    EntityLinkStatus,
    EvidenceSpan,
    IntelligenceAssertion,
    Mention,
    MentionKind,
    OutboxEvent,
    entity_identity_id,
    outbox_event_id,
)
from fi_intel.ledger.repository import IntelligenceLedger
from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import NodeType
from fi_intel.sources.canonical import document_text

_ADMISSION_NAMESPACE = UUID("65b56a5d-fb53-5799-a98a-5fd1c087492d")


def _id(kind: str, *parts: object) -> UUID:
    return uuid5(_ADMISSION_NAMESPACE, "\x1f".join((kind, *(str(part) for part in parts))))


def _event(
    event_type: str,
    aggregate_id: UUID,
    *,
    occurred_at: datetime,
    correlation_id: UUID,
    policy_id: UUID,
    payload: dict[str, JsonValue],
) -> OutboxEvent:
    return OutboxEvent(
        event_id=outbox_event_id(event_type, aggregate_id, 1),
        event_type=event_type,
        aggregate_type=event_type.rsplit(".", maxsplit=2)[0],
        aggregate_id=aggregate_id,
        aggregate_version=1,
        occurred_at=occurred_at,
        correlation_id=correlation_id,
        policy_id=policy_id,
        payload=payload,
    )


def _mention_kind(node_type: NodeType) -> MentionKind:
    return {
        NodeType.ORGANIZATION: MentionKind.ORGANIZATION,
        NodeType.PERSON: MentionKind.PERSON,
        NodeType.INSTRUMENT: MentionKind.INSTRUMENT,
    }.get(node_type, MentionKind.OTHER)


def _entity(ref: EntityRef, policy: AccessPolicy) -> EntityIdentity:
    return EntityIdentity(
        entity_id=entity_identity_id(str(ref.node_type), ref.key),
        entity_type=str(ref.node_type),
        # The governed key is stable across documents. Analyst-facing names
        # live in entity-v2, where name provenance and supersession exist.
        canonical_name=ref.key,
        created_at=policy.created_at,
        policy_id=policy.policy_id,
    )


class LedgerAssertionAdmissionSink:
    """Commit evidence, identity decisions, and accepted facts before projection."""

    def __init__(
        self,
        ledger: IntelligenceLedger,
        documents: DocumentStore,
        *,
        policy: AccessPolicy,
        correlation_id: UUID,
    ) -> None:
        self._ledger = ledger
        self._documents = documents
        self._policy = policy
        self._correlation_id = correlation_id

    async def write(self, assertion: Assertion) -> str:  # noqa: PLR0915
        try:
            document_version_id = UUID(assertion.source_doc_id)
            assertion_id = UUID(assertion.assertion_id())
        except ValueError as exc:
            raise ValueError("canonical assertion lacks ledger UUID lineage") from exc
        version = await self._ledger.document_version(document_version_id)
        if version is None:
            raise ValueError("canonical assertion references an unknown document version")
        if version.policy_id != self._policy.policy_id:
            raise ValueError("assertion policy differs from its ledger document")
        document = await self._documents.load_document(assertion.source_id, assertion.source_doc_id)
        if document is None:
            raise ValueError("canonical assertion document projection is unavailable")
        source_text = document_text(document)
        start, end = assertion.snippet_offset
        if start < 0 or end <= start or end > len(source_text):
            raise ValueError("assertion evidence offset is outside the canonical document")
        quote = source_text[start:end]

        await self._ledger.register_policy(self._policy)
        subject_entity = _entity(assertion.subject, self._policy)
        object_entity = _entity(assertion.object, self._policy)
        for entity in (subject_entity, object_entity):
            await self._ledger.register_entity(
                entity,
                _event(
                    "entity.registered.v1",
                    entity.entity_id,
                    occurred_at=self._policy.created_at,
                    correlation_id=self._correlation_id,
                    policy_id=self._policy.policy_id,
                    payload={"entity_id": str(entity.entity_id)},
                ),
            )

        subject_mention_id = _id("mention", assertion_id, "subject")
        object_mention_id = _id("mention", assertion_id, "object")
        mentions = (
            Mention(
                mention_id=subject_mention_id,
                document_version_id=document_version_id,
                kind=_mention_kind(assertion.subject.node_type),
                surface=assertion.subject.display_name,
                char_start=start,
                char_end=end,
                extractor_bundle_version=assertion.extractor_version,
                recorded_at=assertion.recorded_at,
                policy_id=self._policy.policy_id,
            ),
            Mention(
                mention_id=object_mention_id,
                document_version_id=document_version_id,
                kind=_mention_kind(assertion.object.node_type),
                surface=assertion.object.display_name,
                char_start=start,
                char_end=end,
                extractor_bundle_version=assertion.extractor_version,
                recorded_at=assertion.recorded_at,
                policy_id=self._policy.policy_id,
            ),
        )
        evidence_span_id = _id("evidence", assertion_id)
        span = EvidenceSpan(
            evidence_span_id=evidence_span_id,
            document_version_id=document_version_id,
            char_start=start,
            char_end=end,
            quote=quote,
            quote_hash=hashlib.sha256(quote.encode()).hexdigest(),
            recorded_at=assertion.recorded_at,
            policy_id=self._policy.policy_id,
        )
        candidate_id = _id("candidate", assertion_id)
        candidate = ClaimCandidate(
            candidate_id=candidate_id,
            document_version_id=document_version_id,
            subject_mention_id=subject_mention_id,
            predicate=str(assertion.predicate),
            object=ClaimObject(
                kind=ClaimObjectKind.ENTITY_MENTION,
                entity_mention_id=object_mention_id,
            ),
            qualifiers={
                **assertion.properties,
                **({"state_key": assertion.state_key()} if assertion.state_key() else {}),
            },
            event_time=assertion.valid_from,
            valid_from=assertion.valid_from,
            valid_to=assertion.valid_to,
            evidence_span_ids=(evidence_span_id,),
            extractor_bundle_version=assertion.extractor_version,
            confidence=assertion.confidence,
            recorded_at=assertion.recorded_at,
            policy_id=self._policy.policy_id,
        )
        await self._ledger.commit_extraction(
            mentions,
            (span,),
            (candidate,),
            _event(
                "extraction.completed.v1",
                document_version_id,
                occurred_at=assertion.recorded_at,
                correlation_id=self._correlation_id,
                policy_id=self._policy.policy_id,
                payload={"document_version_id": str(document_version_id)},
            ),
        )

        subject_link = EntityLinkDecision(
            decision_id=_id("link-decision", subject_mention_id, subject_entity.entity_id),
            mention_id=subject_mention_id,
            status=EntityLinkStatus.LINKED,
            entity_id=subject_entity.entity_id,
            candidate_entity_ids=(subject_entity.entity_id,),
            confidence=1.0,
            resolver_version="governed-extraction-endpoint-v1",
            reason="validated governed endpoint key",
            decided_at=assertion.recorded_at,
            decided_by="canonical-extraction-admission",
            policy_id=self._policy.policy_id,
        )
        object_link = EntityLinkDecision(
            decision_id=_id("link-decision", object_mention_id, object_entity.entity_id),
            mention_id=object_mention_id,
            status=EntityLinkStatus.LINKED,
            entity_id=object_entity.entity_id,
            candidate_entity_ids=(object_entity.entity_id,),
            confidence=1.0,
            resolver_version="governed-extraction-endpoint-v1",
            reason="validated governed endpoint key",
            decided_at=assertion.recorded_at,
            decided_by="canonical-extraction-admission",
            policy_id=self._policy.policy_id,
        )
        await self._ledger.commit_entity_links(
            (subject_link, object_link),
            _event(
                "entity.links-decided.v1",
                candidate_id,
                occurred_at=assertion.recorded_at,
                correlation_id=self._correlation_id,
                policy_id=self._policy.policy_id,
                payload={"candidate_id": str(candidate_id)},
            ),
        )

        accepted = IntelligenceAssertion(
            assertion_id=assertion_id,
            candidate_id=candidate_id,
            subject_entity_id=subject_entity.entity_id,
            subject_entity_link_id=subject_link.entity_link_id,
            predicate=str(assertion.predicate),
            object=AssertionObject(
                kind=ClaimObjectKind.ENTITY_MENTION,
                entity_id=object_entity.entity_id,
            ),
            object_entity_link_id=object_link.entity_link_id,
            qualifiers=candidate.qualifiers,
            event_time=assertion.valid_from,
            valid_from=assertion.valid_from,
            valid_to=assertion.valid_to,
            recorded_at=assertion.recorded_at,
            evidence_span_ids=(evidence_span_id,),
            confidence=assertion.confidence,
            ontology_version="fi-ontology-v2",
            policy_id=self._policy.policy_id,
        )
        decision = ClaimDecision(
            decision_id=_id("claim-decision", assertion_id),
            candidate_id=candidate_id,
            decision=ClaimDecisionType.ACCEPTED,
            assertion_id=assertion_id,
            reasons=("closed vocabulary, offsets, fields, and entity endpoints validated",),
            validator_bundle_version="canonical-field-admission-v2",
            decided_at=assertion.recorded_at,
            decided_by="canonical-extraction-admission",
            policy_id=self._policy.policy_id,
        )
        await self._ledger.commit_claim_decision(
            decision,
            accepted,
            _event(
                "assertion.accepted.v1",
                candidate_id,
                occurred_at=assertion.recorded_at,
                correlation_id=self._correlation_id,
                policy_id=self._policy.policy_id,
                payload={"projection": assertion.model_dump(mode="json")},
            ),
        )
        return str(assertion_id)


__all__ = ["LedgerAssertionAdmissionSink"]
