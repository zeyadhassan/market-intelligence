"""Transactional repository contract for the evidence ledger.

Every externally visible state change is committed with an outbox event. The
in-memory implementation is the executable contract used by unit tests. The
PostgreSQL implementation targets ``deploy/migrations/0002_intelligence_ledger.sql``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Protocol, TypeVar, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from fi_intel.ledger.models import (
    AccessPolicy,
    ClaimCandidate,
    ClaimDecision,
    ClaimDecisionType,
    DocumentIdentity,
    DocumentVersion,
    EntityIdentity,
    EntityLinkDecision,
    EntityLinkStatus,
    EvidenceSpan,
    IntelligenceAssertion,
    Mention,
    MentionKind,
    OutboxEvent,
    PolicyLineage,
    RawAsset,
    SignalIdentity,
    SignalStatus,
    SignalTransition,
    is_allowed_signal_transition,
    signal_identity_id,
)


class LedgerConflictError(RuntimeError):
    """A stable ID was reused for different immutable content."""


class LedgerInvariantError(RuntimeError):
    """A repository-level provenance, policy, or lifecycle invariant failed."""


@runtime_checkable
class IntelligenceLedger(Protocol):
    async def register_policy(
        self, policy: AccessPolicy, lineage: tuple[PolicyLineage, ...] = ()
    ) -> None: ...

    async def register_entity(self, entity: EntityIdentity, event: OutboxEvent) -> None: ...

    async def commit_raw_asset(self, raw_asset: RawAsset, event: OutboxEvent) -> None: ...

    async def commit_document_version(
        self,
        raw_asset: RawAsset,
        document: DocumentIdentity,
        version: DocumentVersion,
        event: OutboxEvent,
    ) -> None: ...

    async def commit_extraction(
        self,
        mentions: tuple[Mention, ...],
        evidence_spans: tuple[EvidenceSpan, ...],
        candidates: tuple[ClaimCandidate, ...],
        event: OutboxEvent,
    ) -> None: ...

    async def commit_entity_links(
        self, decisions: tuple[EntityLinkDecision, ...], event: OutboxEvent
    ) -> None: ...

    async def commit_claim_decision(
        self,
        decision: ClaimDecision,
        assertion: IntelligenceAssertion | None,
        event: OutboxEvent,
    ) -> None: ...

    async def commit_signal_transition(
        self,
        signal: SignalIdentity,
        transition: SignalTransition,
        event: OutboxEvent,
    ) -> None: ...

    async def pending_events(self, limit: int = 100) -> list[OutboxEvent]: ...

    async def mark_event_published(self, event_id: UUID, published_at: datetime) -> None: ...

    async def signal_history(self, signal_id: UUID) -> list[SignalTransition]: ...

    async def document_head(self, document_id: UUID) -> DocumentVersion | None: ...

    async def document_version(self, document_version_id: UUID) -> DocumentVersion | None: ...

    async def document_identity(self, document_id: UUID) -> DocumentIdentity | None: ...

    async def raw_asset(self, raw_asset_id: UUID) -> RawAsset | None: ...

    async def close(self) -> None: ...


ModelT = TypeVar("ModelT", bound=BaseModel)


def _check_idempotent(existing: dict[UUID, ModelT], values: Iterable[tuple[UUID, ModelT]]) -> None:
    for key, value in values:
        previous = existing.get(key)
        if previous is not None and previous != value:
            raise LedgerConflictError(f"immutable ID {key} was reused with different content")


class InMemoryIntelligenceLedger:
    """Atomic reference implementation of :class:`IntelligenceLedger`."""

    def __init__(self) -> None:
        self._policies: dict[UUID, AccessPolicy] = {}
        self._policy_lineage: dict[tuple[UUID, UUID], PolicyLineage] = {}
        self._raw_assets: dict[UUID, RawAsset] = {}
        self._documents: dict[UUID, DocumentIdentity] = {}
        self._document_versions: dict[UUID, DocumentVersion] = {}
        self._current_document_versions: dict[UUID, UUID] = {}
        self._entities: dict[UUID, EntityIdentity] = {}
        self._mentions: dict[UUID, Mention] = {}
        self._entity_links: dict[UUID, EntityLinkDecision] = {}
        self._evidence_spans: dict[UUID, EvidenceSpan] = {}
        self._claim_candidates: dict[UUID, ClaimCandidate] = {}
        self._claim_decisions: dict[UUID, ClaimDecision] = {}
        self._assertions: dict[UUID, IntelligenceAssertion] = {}
        self._signals: dict[UUID, SignalIdentity] = {}
        self._signal_transitions: dict[UUID, SignalTransition] = {}
        self._outbox: dict[UUID, OutboxEvent] = {}
        self._published_events: dict[UUID, datetime] = {}

    async def register_policy(
        self, policy: AccessPolicy, lineage: tuple[PolicyLineage, ...] = ()
    ) -> None:
        _check_idempotent(self._policies, ((policy.policy_id, policy),))
        lineage_values = [
            ((edge.derived_policy_id, edge.input_policy_id), edge) for edge in lineage
        ]
        for edge in lineage:
            if edge.derived_policy_id != policy.policy_id:
                raise LedgerInvariantError("lineage does not describe the registered policy")
            if edge.input_policy_id not in self._policies:
                raise LedgerInvariantError("lineage input policy is not registered")
        for key, edge in lineage_values:
            previous = self._policy_lineage.get(key)
            if previous is not None and previous != edge:
                raise LedgerConflictError("policy lineage edge has conflicting content")
        self._policies.setdefault(policy.policy_id, policy)
        for key, edge in lineage_values:
            self._policy_lineage.setdefault(key, edge)

    async def register_entity(self, entity: EntityIdentity, event: OutboxEvent) -> None:
        self._require_policy(entity.policy_id)
        self._require_event(event, entity.entity_id, entity.policy_id)
        _check_idempotent(self._entities, ((entity.entity_id, entity),))
        self._check_event_idempotent(event)
        self._entities.setdefault(entity.entity_id, entity)
        self._outbox.setdefault(event.event_id, event)

    async def commit_raw_asset(self, raw_asset: RawAsset, event: OutboxEvent) -> None:
        self._require_policy(raw_asset.policy_id)
        self._require_event(event, raw_asset.raw_asset_id, raw_asset.policy_id)
        _check_idempotent(self._raw_assets, ((raw_asset.raw_asset_id, raw_asset),))
        self._check_event_idempotent(event)
        self._raw_assets.setdefault(raw_asset.raw_asset_id, raw_asset)
        self._outbox.setdefault(event.event_id, event)

    async def commit_document_version(
        self,
        raw_asset: RawAsset,
        document: DocumentIdentity,
        version: DocumentVersion,
        event: OutboxEvent,
    ) -> None:
        self._require_policy(raw_asset.policy_id)
        self._require_policy_not_wider(version.policy_id, (raw_asset.policy_id,))
        if raw_asset.source_id != document.source_id:
            raise LedgerInvariantError("raw asset and document source differ")
        if raw_asset.external_id != document.external_id:
            raise LedgerInvariantError("raw asset and document external ID differ")
        if version.document_id != document.document_id:
            raise LedgerInvariantError("document version points at another document")
        if version.raw_asset_id != raw_asset.raw_asset_id:
            raise LedgerInvariantError("document version points at another raw asset")
        self._require_event(event, version.document_version_id, version.policy_id)
        _check_idempotent(self._raw_assets, ((raw_asset.raw_asset_id, raw_asset),))
        _check_idempotent(self._documents, ((document.document_id, document),))
        _check_idempotent(
            self._document_versions,
            ((version.document_version_id, version),),
        )
        self._check_event_idempotent(event)

        current_id = self._current_document_versions.get(document.document_id)
        if current_id is not None and current_id != version.document_version_id:
            current = self._document_versions[current_id]
            if version.version_number != current.version_number + 1:
                raise LedgerInvariantError("document version numbers must be contiguous")
            if version.supersedes_version_id != current.document_version_id:
                raise LedgerInvariantError("new version must supersede the current version")
        elif current_id is None and version.version_number != 1:
            raise LedgerInvariantError("a new document must start at version 1")

        self._raw_assets.setdefault(raw_asset.raw_asset_id, raw_asset)
        self._documents.setdefault(document.document_id, document)
        self._document_versions.setdefault(version.document_version_id, version)
        self._current_document_versions[document.document_id] = version.document_version_id
        self._outbox.setdefault(event.event_id, event)

    async def commit_extraction(
        self,
        mentions: tuple[Mention, ...],
        evidence_spans: tuple[EvidenceSpan, ...],
        candidates: tuple[ClaimCandidate, ...],
        event: OutboxEvent,
    ) -> None:
        self._validate_extraction_batch(mentions, evidence_spans, candidates, event)
        _check_idempotent(self._mentions, ((item.mention_id, item) for item in mentions))
        _check_idempotent(
            self._evidence_spans,
            ((item.evidence_span_id, item) for item in evidence_spans),
        )
        _check_idempotent(
            self._claim_candidates,
            ((item.candidate_id, item) for item in candidates),
        )
        self._check_event_idempotent(event)

        self._mentions.update({item.mention_id: item for item in mentions})
        self._evidence_spans.update({item.evidence_span_id: item for item in evidence_spans})
        self._claim_candidates.update({item.candidate_id: item for item in candidates})
        self._outbox.setdefault(event.event_id, event)

    async def commit_entity_links(
        self, decisions: tuple[EntityLinkDecision, ...], event: OutboxEvent
    ) -> None:
        if not decisions:
            raise LedgerInvariantError("an entity-link batch cannot be empty")
        _check_idempotent(
            self._entity_links,
            ((decision.decision_id, decision) for decision in decisions),
        )
        staged = dict(self._entity_links)
        for decision in decisions:
            if decision.decision_id in staged:
                continue
            mention = self._mentions.get(decision.mention_id)
            if mention is None:
                raise LedgerInvariantError("entity-link decision references an unknown mention")
            self._require_policy_not_wider(decision.policy_id, (mention.policy_id,))
            referenced = (*decision.candidate_entity_ids,)
            if decision.entity_id is not None:
                referenced = (*referenced, decision.entity_id)
            if any(entity_id not in self._entities for entity_id in referenced):
                raise LedgerInvariantError("entity-link decision references an unknown entity")
            self._validate_entity_link_lifecycle(decision, staged)
            staged[decision.decision_id] = decision
        self._require_event(event, event.aggregate_id, event.policy_id)
        if any(decision.policy_id != event.policy_id for decision in decisions):
            raise LedgerInvariantError("entity-link batch and event policies differ")
        self._check_event_idempotent(event)
        self._entity_links = staged
        self._outbox.setdefault(event.event_id, event)

    async def commit_claim_decision(
        self,
        decision: ClaimDecision,
        assertion: IntelligenceAssertion | None,
        event: OutboxEvent,
    ) -> None:
        candidate = self._claim_candidates.get(decision.candidate_id)
        if candidate is None:
            raise LedgerInvariantError("claim decision references an unknown candidate")
        self._require_policy_not_wider(decision.policy_id, (candidate.policy_id,))
        self._validate_claim_outcome(candidate, decision, assertion)

        self._require_event(event, decision.candidate_id, decision.policy_id)
        _check_idempotent(
            self._claim_decisions,
            ((decision.decision_id, decision),),
        )
        if assertion is not None:
            _check_idempotent(
                self._assertions,
                ((assertion.assertion_id, assertion),),
            )
        self._check_event_idempotent(event)

        self._claim_decisions.setdefault(decision.decision_id, decision)
        if assertion is not None:
            self._assertions.setdefault(assertion.assertion_id, assertion)
        self._outbox.setdefault(event.event_id, event)

    async def commit_signal_transition(
        self,
        signal: SignalIdentity,
        transition: SignalTransition,
        event: OutboxEvent,
    ) -> None:
        self._validate_signal_identity(signal, transition, event)
        if self._is_idempotent_signal_retry(transition, event):
            return
        history = await self.signal_history(signal.signal_id)
        previous = history[-1].to_status if history else None
        if transition.from_status != previous:
            raise LedgerInvariantError("transition from_status is stale")
        if not is_allowed_signal_transition(previous, transition.to_status):
            raise LedgerInvariantError(
                f"signal transition {previous!s} -> {transition.to_status} is not allowed"
            )
        if history and transition.occurred_at <= history[-1].occurred_at:
            raise LedgerInvariantError("signal transition time must increase")
        if any(
            assertion_id not in self._assertions
            for assertion_id in transition.contributing_assertion_ids
        ):
            raise LedgerInvariantError("signal transition references an unknown assertion")
        input_policies = tuple(
            self._assertions[assertion_id].policy_id
            for assertion_id in transition.contributing_assertion_ids
        )
        if input_policies:
            self._require_policy_not_wider(transition.policy_id, input_policies)
        _check_idempotent(
            self._signal_transitions,
            ((transition.transition_id, transition),),
        )
        self._check_event_idempotent(event)
        self._signals[signal.signal_id] = signal
        self._signal_transitions.setdefault(transition.transition_id, transition)
        self._outbox.setdefault(event.event_id, event)

    async def pending_events(self, limit: int = 100) -> list[OutboxEvent]:
        if limit < 1:
            raise ValueError("limit must be positive")
        events = [
            event
            for event_id, event in self._outbox.items()
            if event_id not in self._published_events
        ]
        events.sort(key=lambda item: (item.occurred_at, str(item.event_id)))
        return events[:limit]

    async def mark_event_published(self, event_id: UUID, published_at: datetime) -> None:
        if published_at.tzinfo is None or published_at.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        event = self._outbox.get(event_id)
        if event is None:
            raise LedgerInvariantError("outbox event is unknown")
        previous = self._published_events.get(event_id)
        if previous is not None and previous != published_at:
            raise LedgerConflictError("outbox event was already published at another time")
        self._published_events[event_id] = published_at

    async def signal_history(self, signal_id: UUID) -> list[SignalTransition]:
        history = [
            transition
            for transition in self._signal_transitions.values()
            if transition.signal_id == signal_id
        ]
        history.sort(key=lambda item: (item.occurred_at, str(item.transition_id)))
        return history

    async def document_head(self, document_id: UUID) -> DocumentVersion | None:
        version_id = self._current_document_versions.get(document_id)
        return self._document_versions.get(version_id) if version_id is not None else None

    async def document_version(self, document_version_id: UUID) -> DocumentVersion | None:
        return self._document_versions.get(document_version_id)

    async def document_identity(self, document_id: UUID) -> DocumentIdentity | None:
        return self._documents.get(document_id)

    async def raw_asset(self, raw_asset_id: UUID) -> RawAsset | None:
        return self._raw_assets.get(raw_asset_id)

    async def close(self) -> None:
        return None

    def _require_policy(self, policy_id: UUID) -> AccessPolicy:
        policy = self._policies.get(policy_id)
        if policy is None:
            raise LedgerInvariantError(f"policy {policy_id} is not registered")
        return policy

    def _validate_extraction_batch(
        self,
        mentions: tuple[Mention, ...],
        evidence_spans: tuple[EvidenceSpan, ...],
        candidates: tuple[ClaimCandidate, ...],
        event: OutboxEvent,
    ) -> UUID:
        if not mentions and not evidence_spans and not candidates:
            raise LedgerInvariantError("an extraction batch cannot be empty")
        version_ids = (
            {item.document_version_id for item in mentions}
            | {item.document_version_id for item in evidence_spans}
            | {item.document_version_id for item in candidates}
        )
        if len(version_ids) != 1:
            raise LedgerInvariantError("an extraction batch must target one document version")
        version_id = next(iter(version_ids))
        version = self._document_versions.get(version_id)
        if version is None:
            raise LedgerInvariantError("document version is not registered")
        mention_map = {**self._mentions, **{item.mention_id: item for item in mentions}}
        span_map = {
            **self._evidence_spans,
            **{item.evidence_span_id: item for item in evidence_spans},
        }
        policy_ids = (
            {item.policy_id for item in mentions}
            | {item.policy_id for item in evidence_spans}
            | {item.policy_id for item in candidates}
        )
        for policy_id in policy_ids:
            self._require_policy_not_wider(policy_id, (version.policy_id,))
        for candidate in candidates:
            self._validate_candidate_references(candidate, version_id, mention_map, span_map)
        self._require_event(event, version_id, event.policy_id)
        self._require_policy_not_wider(event.policy_id, (version.policy_id,))
        return version_id

    @staticmethod
    def _validate_candidate_references(
        candidate: ClaimCandidate,
        version_id: UUID,
        mentions: dict[UUID, Mention],
        spans: dict[UUID, EvidenceSpan],
    ) -> None:
        subject = mentions.get(candidate.subject_mention_id)
        if subject is None or subject.document_version_id != version_id:
            raise LedgerInvariantError("claim subject mention is missing from its document")
        object_mention_id = candidate.object.entity_mention_id
        if object_mention_id is not None:
            object_mention = mentions.get(object_mention_id)
            if object_mention is None or object_mention.document_version_id != version_id:
                raise LedgerInvariantError("claim object mention is missing from its document")
        if any(
            spans.get(span_id) is None or spans[span_id].document_version_id != version_id
            for span_id in candidate.evidence_span_ids
        ):
            raise LedgerInvariantError("claim evidence is missing from its document")

    def _validate_claim_outcome(
        self,
        candidate: ClaimCandidate,
        decision: ClaimDecision,
        assertion: IntelligenceAssertion | None,
    ) -> None:
        if decision.decision is not ClaimDecisionType.ACCEPTED:
            if assertion is not None:
                raise LedgerInvariantError("non-accepted decisions cannot create assertions")
            return
        if assertion is None or decision.assertion_id != assertion.assertion_id:
            raise LedgerInvariantError("accepted decision and assertion do not match")
        self._validate_accepted_assertion(candidate, assertion, decision.decided_at)

    def _validate_accepted_assertion(
        self,
        candidate: ClaimCandidate,
        assertion: IntelligenceAssertion,
        decided_at: datetime,
    ) -> None:
        if assertion.candidate_id != candidate.candidate_id:
            raise LedgerInvariantError("assertion references another candidate")
        if assertion.predicate != candidate.predicate:
            raise LedgerInvariantError("assertion predicate differs from its candidate")
        self._require_policy_not_wider(assertion.policy_id, (candidate.policy_id,))
        subject_mention = self._mentions[candidate.subject_mention_id]
        subject_link_id = self._required_assertion_link_id(
            assertion.ontology_version,
            subject_mention,
            assertion.subject_entity_link_id,
            "subject",
        )
        subject_entity_id = self._resolved_entity_for_mention(
            candidate.subject_mention_id,
            entity_link_id=subject_link_id,
            as_of=decided_at,
        )
        if subject_entity_id != assertion.subject_entity_id:
            raise LedgerInvariantError("assertion subject is not the resolved claim mention")
        object_entity_id = assertion.object.entity_id
        object_mention_id = candidate.object.entity_mention_id
        if object_mention_id is not None:
            object_mention = self._mentions[object_mention_id]
            object_link_id = self._required_assertion_link_id(
                assertion.ontology_version,
                object_mention,
                assertion.object_entity_link_id,
                "object",
            )
            resolved_object_id = self._resolved_entity_for_mention(
                object_mention_id,
                entity_link_id=object_link_id,
                as_of=decided_at,
            )
            if object_entity_id != resolved_object_id:
                raise LedgerInvariantError("assertion object is not the resolved claim mention")
        elif (
            assertion.object.kind != candidate.object.kind
            or assertion.object.value != candidate.object.value
        ):
            raise LedgerInvariantError("assertion object differs from its candidate")
        elif assertion.object_entity_link_id is not None:
            raise LedgerInvariantError("literal assertion objects cannot carry an entity link")
        if set(assertion.evidence_span_ids) != set(candidate.evidence_span_ids):
            raise LedgerInvariantError("assertion evidence differs from its candidate")
        superseded = assertion.supersedes_assertion_id
        if superseded is not None and superseded not in self._assertions:
            raise LedgerInvariantError("superseded assertion is unknown")

    @staticmethod
    def _required_assertion_link_id(
        ontology_version: str,
        mention: Mention,
        entity_link_id: UUID | None,
        role: str,
    ) -> UUID | None:
        requires_v2_link = ontology_version.startswith("fi-ontology-v2") and mention.kind in {
            MentionKind.ORGANIZATION,
            MentionKind.INSTRUMENT,
        }
        if requires_v2_link and entity_link_id is None:
            raise LedgerInvariantError(
                f"v2 {role} organization/instrument claim requires entity_link_id"
            )
        return entity_link_id

    def _resolved_entity_for_mention(
        self,
        mention_id: UUID,
        *,
        entity_link_id: UUID | None = None,
        as_of: datetime | None = None,
    ) -> UUID:
        decisions = [
            decision
            for decision in self._entity_links.values()
            if decision.mention_id == mention_id and (as_of is None or decision.decided_at <= as_of)
        ]
        decisions.sort(key=lambda item: (item.decided_at, str(item.decision_id)))
        if entity_link_id is not None:
            linked = next(
                (
                    decision
                    for decision in reversed(decisions)
                    if decision.status is EntityLinkStatus.LINKED
                    and decision.entity_link_id == entity_link_id
                ),
                None,
            )
            if linked is None or self._link_was_closed(
                entity_link_id, decisions, linked.decided_at
            ):
                raise LedgerInvariantError("claim entity_link_id is not active")
            if linked.entity_id is None:
                raise LedgerInvariantError("linked entity decision is incomplete")
            return linked.entity_id
        if not decisions or decisions[-1].status is not EntityLinkStatus.LINKED:
            raise LedgerInvariantError("claim mention does not have an active entity link")
        entity_id = decisions[-1].entity_id
        if entity_id is None:
            raise LedgerInvariantError("linked entity decision is incomplete")
        return entity_id

    @staticmethod
    def _link_was_closed(
        entity_link_id: UUID,
        decisions: list[EntityLinkDecision],
        activated_at: datetime,
    ) -> bool:
        return any(
            decision.decided_at >= activated_at
            and (
                decision.invalidates_entity_link_id == entity_link_id
                or decision.supersedes_entity_link_id == entity_link_id
            )
            for decision in decisions
        )

    def _validate_entity_link_lifecycle(
        self,
        decision: EntityLinkDecision,
        decisions: dict[UUID, EntityLinkDecision],
    ) -> None:
        history = sorted(
            (item for item in decisions.values() if item.mention_id == decision.mention_id),
            key=lambda item: (item.decided_at, str(item.decision_id)),
        )
        if history and decision.decided_at <= history[-1].decided_at:
            raise LedgerInvariantError("entity-link decision time must increase")
        active = next(
            (
                item
                for item in reversed(history)
                if item.status is EntityLinkStatus.LINKED
                and item.entity_link_id is not None
                and not self._link_was_closed(item.entity_link_id, history, item.decided_at)
            ),
            None,
        )
        if decision.status is EntityLinkStatus.LINKED:
            if active is None and decision.supersedes_entity_link_id is not None:
                raise LedgerInvariantError("superseded entity link is not active")
            if active is not None and decision.supersedes_entity_link_id != active.entity_link_id:
                raise LedgerInvariantError(
                    "a new entity link must explicitly supersede the active link"
                )
        elif decision.status is EntityLinkStatus.INVALIDATED:
            if active is None or decision.invalidates_entity_link_id != active.entity_link_id:
                raise LedgerInvariantError("invalidated entity link is not active")

    def _validate_signal_identity(
        self,
        signal: SignalIdentity,
        transition: SignalTransition,
        event: OutboxEvent,
    ) -> None:
        expected_id = signal_identity_id(
            signal.pattern_id,
            signal.pattern_version,
            signal.subject_entity_id,
            signal.scope_key,
        )
        if signal.signal_id != expected_id:
            raise LedgerInvariantError("signal_id is not the stable pattern/entity identity")
        if signal.subject_entity_id not in self._entities:
            raise LedgerInvariantError("signal subject entity is unknown")
        if transition.signal_id != signal.signal_id:
            raise LedgerInvariantError("transition references another signal")
        if signal.policy_id != transition.policy_id:
            raise LedgerInvariantError("signal and transition policies differ")
        existing_signal = self._signals.get(signal.signal_id)
        if existing_signal is not None:
            stable_fields = {"policy_id"}
            if existing_signal.model_dump(exclude=stable_fields) != signal.model_dump(
                exclude=stable_fields
            ):
                raise LedgerConflictError("stable signal identity has conflicting content")
            self._require_policy_not_wider(signal.policy_id, (existing_signal.policy_id,))
        self._require_event(event, signal.signal_id, transition.policy_id)

    def _is_idempotent_signal_retry(self, transition: SignalTransition, event: OutboxEvent) -> bool:
        existing_transition = self._signal_transitions.get(transition.transition_id)
        if existing_transition is None:
            return False
        if existing_transition != transition:
            raise LedgerConflictError("signal transition ID has conflicting immutable content")
        self._check_event_idempotent(event)
        if event.event_id not in self._outbox:
            raise LedgerConflictError("signal transition is already bound to another outbox event")
        return True

    def _require_policy_not_wider(
        self, output_policy_id: UUID, input_policy_ids: tuple[UUID, ...]
    ) -> None:
        output = self._require_policy(output_policy_id)
        for input_policy_id in input_policy_ids:
            input_policy = self._require_policy(input_policy_id)
            if not output.allowed_entitlement_groups.issubset(
                input_policy.allowed_entitlement_groups
            ):
                raise LedgerInvariantError("derived policy widens the input audience")
            if input_policy.barrier_side.value == "private":
                if output.barrier_side.value != "private":
                    raise LedgerInvariantError("derived policy crosses the private barrier")
            if output_policy_id != input_policy_id:
                edge = (output_policy_id, input_policy_id)
                if edge not in self._policy_lineage:
                    raise LedgerInvariantError("derived policy is missing lineage")

    @staticmethod
    def _require_event(event: OutboxEvent, aggregate_id: UUID, policy_id: UUID) -> None:
        if event.aggregate_id != aggregate_id:
            raise LedgerInvariantError("outbox event aggregate does not match the commit")
        if event.policy_id != policy_id:
            raise LedgerInvariantError("outbox event policy does not match the commit")

    def _check_event_idempotent(self, event: OutboxEvent) -> None:
        _check_idempotent(self._outbox, ((event.event_id, event),))
        event_key = (
            event.aggregate_type,
            event.aggregate_id,
            event.aggregate_version,
            event.event_type,
        )
        for previous in self._outbox.values():
            previous_key = (
                previous.aggregate_type,
                previous.aggregate_id,
                previous.aggregate_version,
                previous.event_type,
            )
            if previous_key == event_key and previous.event_id != event.event_id:
                raise LedgerConflictError(
                    "aggregate version is already bound to another outbox event"
                )


def _json(value: BaseModel | Mapping[str, object]) -> str:
    if isinstance(value, BaseModel):
        serializable = value.model_dump(mode="json")
    else:
        serializable = dict(value)
    return json.dumps(serializable, sort_keys=True, separators=(",", ":"))


class PostgresIntelligenceLedger:
    """PostgreSQL implementation with one transaction per domain commit."""

    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=8)
        return self._pool

    async def register_policy(
        self, policy: AccessPolicy, lineage: tuple[PolicyLineage, ...] = ()
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO access_policy (
                    policy_id, barrier_side, allowed_entitlement_groups,
                    semantic_key, created_at
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (policy_id) DO NOTHING
                """,
                policy.policy_id,
                policy.barrier_side.value,
                sorted(policy.allowed_entitlement_groups),
                policy.semantic_key,
                policy.created_at,
            )
            for edge in lineage:
                await conn.execute(
                    """
                    INSERT INTO policy_lineage (
                        derived_policy_id, input_policy_id, reason, recorded_at
                    ) VALUES ($1, $2, $3, $4)
                    ON CONFLICT (derived_policy_id, input_policy_id) DO NOTHING
                    """,
                    edge.derived_policy_id,
                    edge.input_policy_id,
                    edge.reason,
                    edge.recorded_at,
                )

    async def register_entity(self, entity: EntityIdentity, event: OutboxEvent) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO entity_identity (
                    entity_id, entity_type, canonical_name, created_at, policy_id
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (entity_id) DO NOTHING
                """,
                entity.entity_id,
                entity.entity_type,
                entity.canonical_name,
                entity.created_at,
                entity.policy_id,
            )
            await self._insert_outbox(conn, event)

    async def commit_raw_asset(self, raw_asset: RawAsset, event: OutboxEvent) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._insert_raw_asset(conn, raw_asset)
            await self._insert_outbox(conn, event)

    async def commit_document_version(
        self,
        raw_asset: RawAsset,
        document: DocumentIdentity,
        version: DocumentVersion,
        event: OutboxEvent,
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._insert_raw_asset(conn, raw_asset)
            await conn.execute(
                """
                INSERT INTO document_identity (
                    document_id, source_id, external_id, created_at
                ) VALUES ($1,$2,$3,$4)
                ON CONFLICT (document_id) DO NOTHING
                """,
                document.document_id,
                document.source_id,
                document.external_id,
                document.created_at,
            )
            await conn.execute(
                """
                INSERT INTO document_version (
                    document_version_id, document_id, raw_asset_id, version_number,
                    source_revision, normalized_object_uri, normalized_text_hash,
                    title, language, document_class, published_at, recorded_at,
                    parser_version, policy_id, supersedes_version_id
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                ON CONFLICT (document_version_id) DO NOTHING
                """,
                version.document_version_id,
                version.document_id,
                version.raw_asset_id,
                version.version_number,
                version.source_revision,
                version.normalized_object_uri,
                version.normalized_text_hash,
                version.title,
                version.language,
                version.document_class.value,
                version.published_at,
                version.recorded_at,
                version.parser_version,
                version.policy_id,
                version.supersedes_version_id,
            )
            await conn.execute(
                """
                UPDATE document_identity AS d
                SET current_version_id = $2
                WHERE d.document_id = $1
                  AND (
                    d.current_version_id IS NULL OR
                    (SELECT version_number FROM document_version
                     WHERE document_version_id = d.current_version_id) <= $3
                  )
                """,
                document.document_id,
                version.document_version_id,
                version.version_number,
            )
            await self._insert_outbox(conn, event)

    async def commit_extraction(
        self,
        mentions: tuple[Mention, ...],
        evidence_spans: tuple[EvidenceSpan, ...],
        candidates: tuple[ClaimCandidate, ...],
        event: OutboxEvent,
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for mention in mentions:
                await conn.execute(
                    """
                    INSERT INTO mention (
                        mention_id, document_version_id, kind, surface, char_start,
                        char_end, extractor_bundle_version, recorded_at, policy_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    ON CONFLICT (mention_id) DO NOTHING
                    """,
                    mention.mention_id,
                    mention.document_version_id,
                    mention.kind.value,
                    mention.surface,
                    mention.char_start,
                    mention.char_end,
                    mention.extractor_bundle_version,
                    mention.recorded_at,
                    mention.policy_id,
                )
            for span in evidence_spans:
                await conn.execute(
                    """
                    INSERT INTO evidence_span (
                        evidence_span_id, document_version_id, char_start, char_end,
                        quote, quote_hash, page_number, section, recorded_at, policy_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                    ON CONFLICT (evidence_span_id) DO NOTHING
                    """,
                    span.evidence_span_id,
                    span.document_version_id,
                    span.char_start,
                    span.char_end,
                    span.quote,
                    span.quote_hash,
                    span.page_number,
                    span.section,
                    span.recorded_at,
                    span.policy_id,
                )
            for candidate in candidates:
                await conn.execute(
                    """
                    INSERT INTO claim_candidate (
                        candidate_id, document_version_id, subject_mention_id, predicate,
                        object_json, qualifiers, event_time, valid_from, valid_to,
                        extractor_bundle_version, confidence, recorded_at, policy_id
                    ) VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8,$9,$10,$11,$12,$13)
                    ON CONFLICT (candidate_id) DO NOTHING
                    """,
                    candidate.candidate_id,
                    candidate.document_version_id,
                    candidate.subject_mention_id,
                    candidate.predicate,
                    _json(candidate.object),
                    _json(candidate.qualifiers),
                    candidate.event_time,
                    candidate.valid_from,
                    candidate.valid_to,
                    candidate.extractor_bundle_version,
                    candidate.confidence,
                    candidate.recorded_at,
                    candidate.policy_id,
                )
                for span_id in candidate.evidence_span_ids:
                    await conn.execute(
                        """
                        INSERT INTO claim_candidate_evidence (candidate_id, evidence_span_id)
                        VALUES ($1, $2) ON CONFLICT DO NOTHING
                        """,
                        candidate.candidate_id,
                        span_id,
                    )
            await self._insert_outbox(conn, event)

    async def commit_entity_links(
        self, decisions: tuple[EntityLinkDecision, ...], event: OutboxEvent
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for decision in decisions:
                await conn.execute(
                    """
                    INSERT INTO entity_link_decision (
                        decision_id, mention_id, status, entity_link_id, entity_id,
                        resolution_candidate_id, supersedes_entity_link_id,
                        invalidates_entity_link_id,
                        candidate_entity_ids, confidence, resolver_version, reason,
                        decided_at, decided_by, policy_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                    ON CONFLICT (decision_id) DO NOTHING
                    """,
                    decision.decision_id,
                    decision.mention_id,
                    decision.status.value,
                    decision.entity_link_id,
                    decision.entity_id,
                    decision.resolution_candidate_id,
                    decision.supersedes_entity_link_id,
                    decision.invalidates_entity_link_id,
                    list(decision.candidate_entity_ids),
                    decision.confidence,
                    decision.resolver_version,
                    decision.reason,
                    decision.decided_at,
                    decision.decided_by,
                    decision.policy_id,
                )
            await self._insert_outbox(conn, event)

    async def commit_claim_decision(
        self,
        decision: ClaimDecision,
        assertion: IntelligenceAssertion | None,
        event: OutboxEvent,
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            if assertion is not None:
                await conn.execute(
                    """
                    INSERT INTO knowledge_assertion (
                        assertion_id, candidate_id, subject_entity_id,
                        subject_entity_link_id, predicate, object_json,
                        object_entity_link_id, qualifiers, event_time, valid_from,
                        valid_to, recorded_at, confidence, ontology_version,
                        policy_id, supersedes_assertion_id
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6::jsonb,$7,$8::jsonb,$9,$10,$11,$12,$13,$14,$15,$16
                    )
                    ON CONFLICT (assertion_id) DO NOTHING
                    """,
                    assertion.assertion_id,
                    assertion.candidate_id,
                    assertion.subject_entity_id,
                    assertion.subject_entity_link_id,
                    assertion.predicate,
                    _json(assertion.object),
                    assertion.object_entity_link_id,
                    _json(assertion.qualifiers),
                    assertion.event_time,
                    assertion.valid_from,
                    assertion.valid_to,
                    assertion.recorded_at,
                    assertion.confidence,
                    assertion.ontology_version,
                    assertion.policy_id,
                    assertion.supersedes_assertion_id,
                )
                for span_id in assertion.evidence_span_ids:
                    await conn.execute(
                        """
                        INSERT INTO knowledge_assertion_evidence (
                            assertion_id, evidence_span_id
                        ) VALUES ($1, $2) ON CONFLICT DO NOTHING
                        """,
                        assertion.assertion_id,
                        span_id,
                    )
            await conn.execute(
                """
                INSERT INTO claim_decision (
                    decision_id, candidate_id, decision, assertion_id, reasons,
                    validator_bundle_version, decided_at, decided_by, policy_id
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (decision_id) DO NOTHING
                """,
                decision.decision_id,
                decision.candidate_id,
                decision.decision.value,
                decision.assertion_id,
                list(decision.reasons),
                decision.validator_bundle_version,
                decision.decided_at,
                decision.decided_by,
                decision.policy_id,
            )
            await self._insert_outbox(conn, event)

    async def commit_signal_transition(
        self,
        signal: SignalIdentity,
        transition: SignalTransition,
        event: OutboxEvent,
    ) -> None:
        expected_id = signal_identity_id(
            signal.pattern_id,
            signal.pattern_version,
            signal.subject_entity_id,
            signal.scope_key,
        )
        if signal.signal_id != expected_id:
            raise LedgerInvariantError("signal_id is not the stable pattern/entity identity")
        if signal.policy_id != transition.policy_id:
            raise LedgerInvariantError("signal and transition policies differ")
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._upsert_signal_identity(conn, signal)
            existing_transition = await self._load_transition(conn, transition.transition_id)
            if existing_transition is not None:
                if existing_transition != transition:
                    raise LedgerConflictError(
                        "signal transition ID has conflicting immutable content"
                    )
                await self._insert_outbox(conn, event)
                return
            await self._validate_previous_transition(conn, transition)
            await self._insert_signal_transition(conn, transition)
            await self._insert_outbox(conn, event)

    async def pending_events(self, limit: int = 100) -> list[OutboxEvent]:
        if limit < 1:
            raise ValueError("limit must be positive")
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT * FROM transactional_outbox
            WHERE published_at IS NULL
            ORDER BY occurred_at, event_id
            LIMIT $1
            """,
            limit,
        )
        return [
            OutboxEvent(
                event_id=row["event_id"],
                event_type=row["event_type"],
                aggregate_type=row["aggregate_type"],
                aggregate_id=row["aggregate_id"],
                aggregate_version=row["aggregate_version"],
                occurred_at=row["occurred_at"],
                correlation_id=row["correlation_id"],
                causation_id=row["causation_id"],
                policy_id=row["policy_id"],
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    async def mark_event_published(self, event_id: UUID, published_at: datetime) -> None:
        pool = await self._get_pool()
        result = await pool.execute(
            """
            UPDATE transactional_outbox
            SET published_at = COALESCE(published_at, $2),
                publish_attempts = publish_attempts + 1,
                last_error = NULL,
                lease_owner = NULL,
                lease_expires_at = NULL
            WHERE event_id = $1
            """,
            event_id,
            published_at,
        )
        if result == "UPDATE 0":
            raise LedgerInvariantError("outbox event is unknown")

    async def signal_history(self, signal_id: UUID) -> list[SignalTransition]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT t.*,
                   COALESCE(array_agg(a.assertion_id ORDER BY a.assertion_id)
                            FILTER (WHERE a.assertion_id IS NOT NULL), '{}') AS assertion_ids
            FROM signal_transition t
            LEFT JOIN signal_transition_assertion a USING (transition_id)
            WHERE t.signal_id = $1
            GROUP BY t.transition_id
            ORDER BY t.occurred_at, t.transition_id
            """,
            signal_id,
        )
        return [self._transition_from_row(row) for row in rows]

    async def document_head(self, document_id: UUID) -> DocumentVersion | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT v.* FROM document_identity d
            JOIN document_version v
              ON v.document_version_id = d.current_version_id
            WHERE d.document_id = $1
            """,
            document_id,
        )
        return self._document_version_from_row(row) if row is not None else None

    async def document_version(self, document_version_id: UUID) -> DocumentVersion | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM document_version WHERE document_version_id = $1",
            document_version_id,
        )
        return self._document_version_from_row(row) if row is not None else None

    @staticmethod
    def _document_version_from_row(row: asyncpg.Record) -> DocumentVersion:
        return DocumentVersion(
            document_version_id=row["document_version_id"],
            document_id=row["document_id"],
            raw_asset_id=row["raw_asset_id"],
            version_number=row["version_number"],
            source_revision=row["source_revision"],
            normalized_object_uri=row["normalized_object_uri"],
            normalized_text_hash=row["normalized_text_hash"],
            title=row["title"],
            language=row["language"],
            document_class=row["document_class"],
            published_at=row["published_at"],
            recorded_at=row["recorded_at"],
            parser_version=row["parser_version"],
            policy_id=row["policy_id"],
            supersedes_version_id=row["supersedes_version_id"],
        )

    async def document_identity(self, document_id: UUID) -> DocumentIdentity | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT document_id, source_id, external_id, created_at
            FROM document_identity WHERE document_id = $1
            """,
            document_id,
        )
        if row is None:
            return None
        return DocumentIdentity(
            document_id=row["document_id"],
            source_id=row["source_id"],
            external_id=row["external_id"],
            created_at=row["created_at"],
        )

    async def raw_asset(self, raw_asset_id: UUID) -> RawAsset | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM raw_asset WHERE raw_asset_id = $1", raw_asset_id)
        if row is None:
            return None
        metadata = row["metadata"]
        return RawAsset(
            raw_asset_id=row["raw_asset_id"],
            source_id=row["source_id"],
            external_id=row["external_id"],
            source_revision=row["source_revision"],
            object_uri=row["object_uri"],
            content_hash=row["content_hash"],
            media_type=row["media_type"],
            fetched_at=row["fetched_at"],
            policy_id=row["policy_id"],
            metadata=json.loads(metadata) if isinstance(metadata, str) else dict(metadata),
        )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None

    @staticmethod
    async def _insert_outbox(conn: asyncpg.Connection, event: OutboxEvent) -> None:
        await conn.execute(
            """
            INSERT INTO transactional_outbox (
                event_id, event_type, aggregate_type, aggregate_id,
                aggregate_version, occurred_at, correlation_id, causation_id,
                policy_id, payload, next_attempt_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11)
            ON CONFLICT (event_id) DO NOTHING
            """,
            event.event_id,
            event.event_type,
            event.aggregate_type,
            event.aggregate_id,
            event.aggregate_version,
            event.occurred_at,
            event.correlation_id,
            event.causation_id,
            event.policy_id,
            _json(event.payload),
            event.occurred_at,
        )

    @staticmethod
    async def _insert_raw_asset(conn: asyncpg.Connection, raw_asset: RawAsset) -> None:
        await conn.execute(
            """
            INSERT INTO raw_asset (
                raw_asset_id, source_id, external_id, source_revision,
                object_uri, content_hash, media_type, fetched_at, policy_id, metadata
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
            ON CONFLICT (raw_asset_id) DO NOTHING
            """,
            raw_asset.raw_asset_id,
            raw_asset.source_id,
            raw_asset.external_id,
            raw_asset.source_revision,
            raw_asset.object_uri,
            raw_asset.content_hash,
            raw_asset.media_type,
            raw_asset.fetched_at,
            raw_asset.policy_id,
            _json(raw_asset.metadata),
        )

    @staticmethod
    def _transition_from_row(row: asyncpg.Record) -> SignalTransition:
        return SignalTransition(
            transition_id=row["transition_id"],
            signal_id=row["signal_id"],
            from_status=row["from_status"],
            to_status=row["to_status"],
            occurred_at=row["occurred_at"],
            as_of=row["as_of"],
            score=row["score"],
            contributing_assertion_ids=tuple(row["assertion_ids"]),
            reason=row["reason"],
            actor=row["actor"],
            policy_id=row["policy_id"],
        )

    @staticmethod
    async def _upsert_signal_identity(conn: asyncpg.Connection, signal: SignalIdentity) -> None:
        persisted_signal_id = await conn.fetchval(
            """
            INSERT INTO intelligence_signal (
                signal_id, pattern_id, pattern_version, subject_entity_id,
                scope_key, created_at, policy_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (signal_id) DO UPDATE
            SET policy_id = EXCLUDED.policy_id
            WHERE intelligence_signal.pattern_id = EXCLUDED.pattern_id
              AND intelligence_signal.pattern_version = EXCLUDED.pattern_version
              AND intelligence_signal.subject_entity_id = EXCLUDED.subject_entity_id
              AND intelligence_signal.scope_key = EXCLUDED.scope_key
              AND intelligence_signal.created_at = EXCLUDED.created_at
            RETURNING signal_id
            """,
            signal.signal_id,
            signal.pattern_id,
            signal.pattern_version,
            signal.subject_entity_id,
            signal.scope_key,
            signal.created_at,
            signal.policy_id,
        )
        if persisted_signal_id is None:
            raise LedgerConflictError("stable signal identity has conflicting content")

    @classmethod
    async def _load_transition(
        cls, conn: asyncpg.Connection, transition_id: UUID
    ) -> SignalTransition | None:
        row = await conn.fetchrow(
            """
            SELECT t.*,
                   COALESCE(array_agg(a.assertion_id ORDER BY a.assertion_id)
                            FILTER (WHERE a.assertion_id IS NOT NULL), '{}')
                       AS assertion_ids
            FROM signal_transition t
            LEFT JOIN signal_transition_assertion a USING (transition_id)
            WHERE t.transition_id = $1
            GROUP BY t.transition_id
            """,
            transition_id,
        )
        return cls._transition_from_row(row) if row is not None else None

    @staticmethod
    async def _validate_previous_transition(
        conn: asyncpg.Connection, transition: SignalTransition
    ) -> None:
        previous_row = await conn.fetchrow(
            """
            SELECT to_status, occurred_at FROM signal_transition
            WHERE signal_id = $1
            ORDER BY occurred_at DESC, transition_id DESC
            LIMIT 1 FOR UPDATE
            """,
            transition.signal_id,
        )
        previous = SignalStatus(previous_row["to_status"]) if previous_row is not None else None
        if transition.from_status != previous:
            raise LedgerInvariantError("transition from_status is stale")
        if not is_allowed_signal_transition(previous, transition.to_status):
            raise LedgerInvariantError("signal lifecycle transition is not allowed")
        if previous_row is not None:
            if transition.occurred_at <= previous_row["occurred_at"]:
                raise LedgerInvariantError("signal transition time must increase")

    @staticmethod
    async def _insert_signal_transition(
        conn: asyncpg.Connection, transition: SignalTransition
    ) -> None:
        await conn.execute(
            """
            INSERT INTO signal_transition (
                transition_id, signal_id, from_status, to_status, occurred_at,
                as_of, score, reason, actor, policy_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (transition_id) DO NOTHING
            """,
            transition.transition_id,
            transition.signal_id,
            transition.from_status.value if transition.from_status else None,
            transition.to_status.value,
            transition.occurred_at,
            transition.as_of,
            transition.score,
            transition.reason,
            transition.actor,
            transition.policy_id,
        )
        for assertion_id in transition.contributing_assertion_ids:
            await conn.execute(
                """
                INSERT INTO signal_transition_assertion (
                    transition_id, assertion_id
                ) VALUES ($1, $2) ON CONFLICT DO NOTHING
                """,
                transition.transition_id,
                assertion_id,
            )
