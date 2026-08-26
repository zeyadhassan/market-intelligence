"""Immutable domain contracts for the evidence-ledger architecture.

The contracts deliberately keep model output separate from accepted facts:
extractors create :class:`ClaimCandidate` objects, deterministic or reviewed
decisions create :class:`IntelligenceAssertion` objects, and only assertions
may feed graph projections or signal detection.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from fi_intel.sources.canonical import BarrierSide, DocumentClass

SHA256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

_LEDGER_NAMESPACE = UUID("bd25c21c-7f06-52b8-9648-95bf7a7382c8")


def _stable_uuid(kind: str, *parts: object) -> UUID:
    value = "\x1f".join((kind, *(str(part) for part in parts)))
    return uuid5(_LEDGER_NAMESPACE, value)


def raw_asset_id(source_id: str, external_id: str, source_revision: str) -> UUID:
    """Return the idempotency key for one immutable source revision."""
    return _stable_uuid("raw-asset", source_id, external_id, source_revision)


def document_identity_id(source_id: str, external_id: str) -> UUID:
    """Return the stable identity shared by all versions of one source item."""
    return _stable_uuid("document", source_id, external_id)


def document_version_id(document_id: UUID, source_revision: str, content_hash: str) -> UUID:
    """Return a stable document-version ID without collapsing corrections."""
    return _stable_uuid("document-version", document_id, source_revision, content_hash)


def signal_identity_id(
    pattern_id: str,
    pattern_version: str,
    subject_entity_id: UUID,
    scope_key: str = "",
) -> UUID:
    """Return a signal ID stable across evaluation dates and occurrences."""
    return _stable_uuid("signal", pattern_id, pattern_version, subject_entity_id, scope_key)


def outbox_event_id(event_type: str, aggregate_id: UUID, aggregate_version: int) -> UUID:
    """Return the deterministic identity required for idempotent replay."""
    return _stable_uuid("outbox", event_type, aggregate_id, aggregate_version)


def entity_link_identity_id(
    mention_id: UUID,
    entity_id: UUID,
    resolver_version: str,
    resolution_candidate_id: UUID | None = None,
) -> UUID:
    """Return the stable persisted identity of an approved mention/entity link."""
    return _stable_uuid(
        "entity-link",
        mention_id,
        entity_id,
        resolver_version,
        resolution_candidate_id or "",
    )


class LedgerModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AccessPolicy(LedgerModel):
    """Effective audience policy attached to every evidence derivative."""

    policy_id: UUID
    barrier_side: BarrierSide
    allowed_entitlement_groups: frozenset[str]
    created_at: AwareDatetime

    @property
    def semantic_key(self) -> SHA256:
        audience = "\x1f".join(sorted(self.allowed_entitlement_groups))
        payload = f"{self.barrier_side.value}\x1e{audience}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PolicyLineage(LedgerModel):
    """One edge explaining which input policy constrained a derived policy."""

    derived_policy_id: UUID
    input_policy_id: UUID
    reason: str = Field(min_length=1)
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def _not_self_referential(self) -> Self:
        if self.derived_policy_id == self.input_policy_id:
            raise ValueError("policy lineage cannot refer to itself")
        return self


def derive_access_policy(
    inputs: tuple[AccessPolicy, ...],
    *,
    created_at: datetime,
    reason: str,
) -> tuple[AccessPolicy, tuple[PolicyLineage, ...]]:
    """Join policies without permitting a derivative to widen access.

    An audience must be allowed by every input. One private input makes the
    derivative private. Empty audience intersection is valid and means that
    no current principal may retrieve the derivative.
    """
    if not inputs:
        raise ValueError("at least one input policy is required")
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    if not reason:
        raise ValueError("policy lineage reason is required")

    allowed = set(inputs[0].allowed_entitlement_groups)
    for policy in inputs[1:]:
        allowed.intersection_update(policy.allowed_entitlement_groups)
    barrier = (
        BarrierSide.PRIVATE
        if any(policy.barrier_side is BarrierSide.PRIVATE for policy in inputs)
        else BarrierSide.PUBLIC
    )
    audience = frozenset(allowed)
    semantic_payload = f"{barrier.value}|{'|'.join(sorted(audience))}"
    policy = AccessPolicy(
        policy_id=_stable_uuid("access-policy", semantic_payload),
        barrier_side=barrier,
        allowed_entitlement_groups=audience,
        created_at=created_at,
    )
    lineage = tuple(
        PolicyLineage(
            derived_policy_id=policy.policy_id,
            input_policy_id=input_policy.policy_id,
            reason=reason,
            recorded_at=created_at,
        )
        for input_policy in inputs
        if input_policy.policy_id != policy.policy_id
    )
    return policy, lineage


class RawAsset(LedgerModel):
    """Immutable bytes received from a source before normalization."""

    raw_asset_id: UUID
    source_id: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    object_uri: str = Field(min_length=1)
    content_hash: SHA256
    media_type: str = Field(min_length=1)
    fetched_at: AwareDatetime
    policy_id: UUID
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class DocumentIdentity(LedgerModel):
    """Stable logical identity for all revisions of one source item."""

    document_id: UUID
    source_id: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    created_at: AwareDatetime


class DocumentVersion(LedgerModel):
    """Immutable normalized representation of one source revision."""

    document_version_id: UUID
    document_id: UUID
    raw_asset_id: UUID
    version_number: int = Field(ge=1)
    source_revision: str = Field(min_length=1)
    normalized_object_uri: str = Field(min_length=1)
    normalized_text_hash: SHA256
    title: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=8)
    document_class: DocumentClass
    published_at: AwareDatetime
    recorded_at: AwareDatetime
    parser_version: str = Field(min_length=1)
    policy_id: UUID
    supersedes_version_id: UUID | None = None

    @model_validator(mode="after")
    def _valid_time_and_lineage(self) -> Self:
        if self.recorded_at < self.published_at:
            raise ValueError("recorded_at precedes published_at")
        if self.supersedes_version_id == self.document_version_id:
            raise ValueError("a document version cannot supersede itself")
        if self.version_number == 1 and self.supersedes_version_id is not None:
            raise ValueError("the first document version cannot supersede another version")
        if self.version_number > 1 and self.supersedes_version_id is None:
            raise ValueError("later document versions must name the superseded version")
        return self


class EntityIdentity(LedgerModel):
    """Internal identity; external identifiers are attributes, never keys."""

    entity_id: UUID
    entity_type: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    created_at: AwareDatetime
    policy_id: UUID


class MentionKind(StrEnum):
    ORGANIZATION = "organization"
    PERSON = "person"
    INSTRUMENT = "instrument"
    JURISDICTION = "jurisdiction"
    IDENTIFIER = "identifier"
    OTHER = "other"


class Mention(LedgerModel):
    mention_id: UUID
    document_version_id: UUID
    kind: MentionKind
    surface: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    extractor_bundle_version: str = Field(min_length=1)
    recorded_at: AwareDatetime
    policy_id: UUID

    @model_validator(mode="after")
    def _valid_span(self) -> Self:
        if self.char_end <= self.char_start:
            raise ValueError("mention span is empty or reversed")
        return self


class EntityLinkStatus(StrEnum):
    LINKED = "linked"
    REVIEW_REQUIRED = "review_required"
    ABSTAINED = "abstained"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


class EntityLinkDecision(LedgerModel):
    decision_id: UUID
    mention_id: UUID
    status: EntityLinkStatus
    entity_link_id: UUID | None = None
    entity_id: UUID | None = None
    resolution_candidate_id: UUID | None = None
    supersedes_entity_link_id: UUID | None = None
    invalidates_entity_link_id: UUID | None = None
    candidate_entity_ids: tuple[UUID, ...] = ()
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    resolver_version: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    decided_at: AwareDatetime
    decided_by: str = Field(min_length=1)
    policy_id: UUID

    @model_validator(mode="after")
    def _link_is_explicit(self) -> Self:  # noqa: C901
        if self.status is EntityLinkStatus.LINKED:
            if self.entity_id is None or self.confidence is None:
                raise ValueError("a linked decision requires entity_id and confidence")
            if self.entity_link_id is None:
                object.__setattr__(
                    self,
                    "entity_link_id",
                    entity_link_identity_id(
                        self.mention_id,
                        self.entity_id,
                        self.resolver_version,
                        self.resolution_candidate_id,
                    ),
                )
            if self.invalidates_entity_link_id is not None:
                raise ValueError("a linked decision cannot invalidate another link")
            if self.supersedes_entity_link_id == self.entity_link_id:
                raise ValueError("an entity link cannot supersede itself")
            if self.candidate_entity_ids and self.entity_id not in self.candidate_entity_ids:
                raise ValueError("the linked entity must appear in the candidate set")
        elif self.status is EntityLinkStatus.INVALIDATED:
            if self.invalidates_entity_link_id is None:
                raise ValueError("an invalidation must identify the invalidated entity link")
            if any(
                value is not None
                for value in (
                    self.entity_link_id,
                    self.entity_id,
                    self.resolution_candidate_id,
                    self.supersedes_entity_link_id,
                    self.confidence,
                )
            ):
                raise ValueError("an invalidation cannot create or select an entity link")
        elif any(
            value is not None
            for value in (
                self.entity_link_id,
                self.entity_id,
                self.supersedes_entity_link_id,
                self.invalidates_entity_link_id,
            )
        ):
            raise ValueError("only linked or invalidated decisions may reference a link")
        if len(set(self.candidate_entity_ids)) != len(self.candidate_entity_ids):
            raise ValueError("candidate_entity_ids contains duplicates")
        return self


class EvidenceSpan(LedgerModel):
    evidence_span_id: UUID
    document_version_id: UUID
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    quote_hash: SHA256
    page_number: int | None = Field(default=None, ge=1)
    section: str | None = None
    recorded_at: AwareDatetime
    policy_id: UUID

    @model_validator(mode="after")
    def _valid_and_bound(self) -> Self:
        if self.char_end <= self.char_start:
            raise ValueError("evidence span is empty or reversed")
        expected = hashlib.sha256(self.quote.encode("utf-8")).hexdigest()
        if self.quote_hash != expected:
            raise ValueError("quote_hash does not match quote")
        return self


class ClaimObjectKind(StrEnum):
    ENTITY_MENTION = "entity_mention"
    TEXT = "text"
    NUMBER = "number"
    MONEY = "money"
    DATE = "date"
    BOOLEAN = "boolean"


ScalarClaimValue = str | int | float | bool | Decimal | date | datetime


class ClaimObject(LedgerModel):
    """Extractor object; entity references remain mention IDs until resolved."""

    kind: ClaimObjectKind
    entity_mention_id: UUID | None = None
    value: ScalarClaimValue | dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def _one_representation(self) -> Self:
        if self.kind is ClaimObjectKind.ENTITY_MENTION:
            if self.entity_mention_id is None or self.value is not None:
                raise ValueError("entity objects require only entity_mention_id")
        elif self.entity_mention_id is not None or self.value is None:
            raise ValueError("literal objects require only value")
        return self


class ClaimCandidate(LedgerModel):
    candidate_id: UUID
    document_version_id: UUID
    subject_mention_id: UUID
    predicate: str = Field(min_length=1)
    object: ClaimObject
    qualifiers: dict[str, JsonValue] = Field(default_factory=dict)
    event_time: AwareDatetime | None = None
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    evidence_span_ids: tuple[UUID, ...] = Field(min_length=1)
    extractor_bundle_version: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    recorded_at: AwareDatetime
    policy_id: UUID

    @model_validator(mode="after")
    def _valid_interval_and_evidence(self) -> Self:
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_to <= self.valid_from:
                raise ValueError("valid_to must be later than valid_from")
        if len(set(self.evidence_span_ids)) != len(self.evidence_span_ids):
            raise ValueError("evidence_span_ids contains duplicates")
        return self


class AssertionObject(LedgerModel):
    """Accepted object; entity references use canonical internal IDs."""

    kind: ClaimObjectKind
    entity_id: UUID | None = None
    value: ScalarClaimValue | dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def _one_representation(self) -> Self:
        if self.kind is ClaimObjectKind.ENTITY_MENTION:
            if self.entity_id is None or self.value is not None:
                raise ValueError("entity assertions require only entity_id")
        elif self.entity_id is not None or self.value is None:
            raise ValueError("literal assertions require only value")
        return self


class IntelligenceAssertion(LedgerModel):
    assertion_id: UUID
    candidate_id: UUID
    subject_entity_id: UUID
    subject_entity_link_id: UUID | None = None
    predicate: str = Field(min_length=1)
    object: AssertionObject
    object_entity_link_id: UUID | None = None
    qualifiers: dict[str, JsonValue] = Field(default_factory=dict)
    event_time: AwareDatetime | None = None
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    recorded_at: AwareDatetime
    evidence_span_ids: tuple[UUID, ...] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    ontology_version: str = Field(min_length=1)
    policy_id: UUID
    supersedes_assertion_id: UUID | None = None

    @model_validator(mode="after")
    def _valid_interval_and_lineage(self) -> Self:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        if self.supersedes_assertion_id == self.assertion_id:
            raise ValueError("an assertion cannot supersede itself")
        if len(set(self.evidence_span_ids)) != len(self.evidence_span_ids):
            raise ValueError("evidence_span_ids contains duplicates")
        return self


class ClaimDecisionType(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVIEW_REQUIRED = "review_required"


class ClaimDecision(LedgerModel):
    decision_id: UUID
    candidate_id: UUID
    decision: ClaimDecisionType
    assertion_id: UUID | None = None
    reasons: tuple[str, ...] = Field(min_length=1)
    validator_bundle_version: str = Field(min_length=1)
    decided_at: AwareDatetime
    decided_by: str = Field(min_length=1)
    policy_id: UUID

    @model_validator(mode="after")
    def _accepted_creates_assertion(self) -> Self:
        if self.decision is ClaimDecisionType.ACCEPTED:
            if self.assertion_id is None:
                raise ValueError("an accepted decision requires assertion_id")
        elif self.assertion_id is not None:
            raise ValueError("only accepted decisions may create an assertion")
        if any(not reason.strip() for reason in self.reasons):
            raise ValueError("decision reasons cannot be blank")
        return self


class SignalStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REVIEWED = "reviewed"
    PUBLISHED = "published"
    SUPPRESSED = "suppressed"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


class SignalIdentity(LedgerModel):
    signal_id: UUID
    pattern_id: str = Field(min_length=1)
    pattern_version: str = Field(min_length=1)
    subject_entity_id: UUID
    scope_key: str = ""
    created_at: AwareDatetime
    policy_id: UUID


class SignalTransition(LedgerModel):
    transition_id: UUID
    signal_id: UUID
    from_status: SignalStatus | None
    to_status: SignalStatus
    occurred_at: AwareDatetime
    as_of: AwareDatetime
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    contributing_assertion_ids: tuple[UUID, ...] = ()
    reason: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    policy_id: UUID

    @model_validator(mode="after")
    def _not_noop(self) -> Self:
        if self.from_status == self.to_status:
            raise ValueError("signal transition must change status")
        if len(set(self.contributing_assertion_ids)) != len(self.contributing_assertion_ids):
            raise ValueError("contributing_assertion_ids contains duplicates")
        return self


class OutboxEvent(LedgerModel):
    event_id: UUID
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]*\.v[1-9][0-9]*$")
    aggregate_type: str = Field(min_length=1)
    aggregate_id: UUID
    aggregate_version: int = Field(ge=1)
    occurred_at: AwareDatetime
    correlation_id: UUID
    causation_id: UUID | None = None
    policy_id: UUID
    payload: dict[str, JsonValue]


_ALLOWED_SIGNAL_TRANSITIONS: dict[SignalStatus | None, frozenset[SignalStatus]] = {
    None: frozenset({SignalStatus.CANDIDATE}),
    SignalStatus.CANDIDATE: frozenset(
        {SignalStatus.CONFIRMED, SignalStatus.SUPPRESSED, SignalStatus.EXPIRED}
    ),
    SignalStatus.CONFIRMED: frozenset(
        {
            SignalStatus.REVIEWED,
            SignalStatus.SUPPRESSED,
            SignalStatus.EXPIRED,
            SignalStatus.WITHDRAWN,
        }
    ),
    SignalStatus.REVIEWED: frozenset(
        {SignalStatus.PUBLISHED, SignalStatus.SUPPRESSED, SignalStatus.WITHDRAWN}
    ),
    SignalStatus.PUBLISHED: frozenset({SignalStatus.WITHDRAWN}),
    SignalStatus.SUPPRESSED: frozenset({SignalStatus.CANDIDATE}),
    SignalStatus.EXPIRED: frozenset({SignalStatus.CANDIDATE}),
    SignalStatus.WITHDRAWN: frozenset({SignalStatus.CANDIDATE}),
}


def is_allowed_signal_transition(previous: SignalStatus | None, requested: SignalStatus) -> bool:
    """Return whether a lifecycle transition is valid."""
    return requested in _ALLOWED_SIGNAL_TRANSITIONS[previous]
