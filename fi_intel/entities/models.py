"""Immutable contracts for governed entity reference and resolution data."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from fi_intel.entities.identifiers import (
    IdentifierScheme,
    NormalizedIdentifier,
    normalize_identifier,
)
from fi_intel.entities.normalization import normalize_entity_name

_ENTITY_NAMESPACE = UUID("39e2c4df-f1cc-5d8d-bb32-f7c0640883be")


def _stable_uuid(kind: str, *parts: object) -> UUID:
    payload = "\x1f".join((kind, *(str(part) for part in parts)))
    return uuid5(_ENTITY_NAMESPACE, payload)


class EntityModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EntityType(StrEnum):
    ORGANIZATION = "organization"
    INSTRUMENT = "instrument"


class EntityNameKind(StrEnum):
    LEGAL = "legal"
    ALIAS = "alias"


class EntityRelationshipKind(StrEnum):
    PARENT_OF = "parent_of"
    ISSUER_OF = "issuer_of"
    SUCCESSOR_OF = "successor_of"


class ResolutionDisposition(StrEnum):
    AUTO_LINK = "auto_link"
    REVIEW_REQUIRED = "review_required"
    ABSTAINED = "abstained"


class IdentifierInput(EntityModel):
    scheme: IdentifierScheme
    value: str = Field(min_length=1)
    venue: str | None = None
    namespace: str | None = None

    def normalized(self) -> NormalizedIdentifier:
        return normalize_identifier(
            self.scheme,
            self.value,
            venue=self.venue,
            namespace=self.namespace,
        )


class EntityRecord(EntityModel):
    entity_id: UUID
    entity_type: EntityType
    canonical_name: str = Field(min_length=1)
    jurisdiction: str | None = None
    sector: str | None = None
    created_at: AwareDatetime
    policy_id: UUID

    @field_validator("canonical_name")
    @classmethod
    def _safe_name(cls, value: str) -> str:
        normalize_entity_name(value)
        return value.strip()

    @field_validator("jurisdiction")
    @classmethod
    def _jurisdiction(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha():
            raise ValueError("jurisdiction must be an ISO alpha-2 code")
        return normalized

    @field_validator("sector")
    @classmethod
    def _sector(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("sector cannot be blank")
        return normalized


class EntityIdentifierRecord(EntityModel):
    identifier_id: UUID
    entity_id: UUID
    scheme: IdentifierScheme
    value: str = Field(min_length=1)
    normalized_value: str = Field(min_length=1)
    scope: str = ""
    effective_from: AwareDatetime
    effective_to: AwareDatetime | None = None
    recorded_at: AwareDatetime
    source_id: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    policy_id: UUID

    @model_validator(mode="after")
    def _normalized_and_valid(self) -> Self:
        kwargs: dict[str, str] = {}
        if self.scheme is IdentifierScheme.TICKER:
            kwargs["venue"] = self.scope
        elif self.scheme is IdentifierScheme.INTERNAL:
            kwargs["namespace"] = self.scope
        elif self.scope:
            raise ValueError(f"{self.scheme.value} cannot have identifier scope")
        normalized = normalize_identifier(self.scheme, self.value, **kwargs)
        if normalized.value != self.normalized_value or normalized.scope != self.scope:
            raise ValueError("identifier normalization does not match stored values")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("identifier effective_to must follow effective_from")
        if self.recorded_at < self.effective_from:
            raise ValueError("identifier recorded_at precedes effective_from")
        expected = entity_identifier_id(
            self.entity_id,
            normalized,
            self.effective_from,
            self.source_id,
            self.source_record_id,
        )
        if self.identifier_id != expected:
            raise ValueError("identifier_id is not the deterministic record identity")
        return self


class EntityNameRecord(EntityModel):
    name_id: UUID
    entity_id: UUID
    kind: EntityNameKind
    name: str = Field(min_length=1)
    normalized_name: str = Field(min_length=1)
    language: str = Field(pattern=r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")
    effective_from: AwareDatetime
    effective_to: AwareDatetime | None = None
    recorded_at: AwareDatetime
    source_id: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    policy_id: UUID
    supersedes_name_id: UUID | None = None

    @model_validator(mode="after")
    def _normalized_and_versioned(self) -> Self:
        if normalize_entity_name(self.name) != self.normalized_name:
            raise ValueError("name normalization does not match stored value")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("name effective_to must follow effective_from")
        if self.recorded_at < self.effective_from:
            raise ValueError("name recorded_at precedes effective_from")
        if self.supersedes_name_id == self.name_id:
            raise ValueError("an entity name cannot supersede itself")
        expected = entity_name_id(
            self.entity_id,
            self.kind,
            self.normalized_name,
            self.language,
            self.effective_from,
            self.source_id,
            self.source_record_id,
        )
        if self.name_id != expected:
            raise ValueError("name_id is not the deterministic record identity")
        return self


class EntityRelationshipRecord(EntityModel):
    relationship_id: UUID
    kind: EntityRelationshipKind
    subject_entity_id: UUID
    object_entity_id: UUID
    effective_from: AwareDatetime
    effective_to: AwareDatetime | None = None
    recorded_at: AwareDatetime
    source_id: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    policy_id: UUID
    supersedes_relationship_id: UUID | None = None

    @model_validator(mode="after")
    def _valid_relationship(self) -> Self:
        if self.subject_entity_id == self.object_entity_id:
            raise ValueError("an entity relationship cannot be self-referential")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("relationship effective_to must follow effective_from")
        if self.recorded_at < self.effective_from:
            raise ValueError("relationship recorded_at precedes effective_from")
        if self.supersedes_relationship_id == self.relationship_id:
            raise ValueError("an entity relationship cannot supersede itself")
        expected = entity_relationship_id(
            self.kind,
            self.subject_entity_id,
            self.object_entity_id,
            self.effective_from,
            self.source_id,
            self.source_record_id,
        )
        if self.relationship_id != expected:
            raise ValueError("relationship_id is not the deterministic record identity")
        return self


class EntityReferenceBundle(EntityModel):
    entity: EntityRecord
    identifiers: tuple[EntityIdentifierRecord, ...] = ()
    names: tuple[EntityNameRecord, ...] = ()
    relationships: tuple[EntityRelationshipRecord, ...] = ()

    @model_validator(mode="after")
    def _owned_records(self) -> Self:
        if any(item.entity_id != self.entity.entity_id for item in self.identifiers):
            raise ValueError("identifier belongs to another entity")
        if any(item.entity_id != self.entity.entity_id for item in self.names):
            raise ValueError("name belongs to another entity")
        if any(
            relationship.subject_entity_id != self.entity.entity_id
            and relationship.object_entity_id != self.entity.entity_id
            for relationship in self.relationships
        ):
            raise ValueError("relationship does not involve the bundled entity")
        if len({item.identifier_id for item in self.identifiers}) != len(self.identifiers):
            raise ValueError("bundle contains duplicate identifier records")
        if len({item.name_id for item in self.names}) != len(self.names):
            raise ValueError("bundle contains duplicate name records")
        return self


class EntityMentionContext(EntityModel):
    mention_id: UUID
    entity_type: EntityType
    surface: str = Field(min_length=1)
    identifiers: tuple[IdentifierInput, ...] = ()
    jurisdiction: str | None = None
    sector: str | None = None
    parent_entity_id: UUID | None = None
    issuer_entity_id: UUID | None = None
    policy_id: UUID

    @field_validator("surface")
    @classmethod
    def _surface(cls, value: str) -> str:
        normalize_entity_name(value)
        return value.strip()

    @field_validator("jurisdiction")
    @classmethod
    def _mention_jurisdiction(cls, value: str | None) -> str | None:
        return EntityRecord._jurisdiction(value)

    @field_validator("sector")
    @classmethod
    def _mention_sector(cls, value: str | None) -> str | None:
        return EntityRecord._sector(value)

    @model_validator(mode="after")
    def _valid_context(self) -> Self:
        normalized = [item.normalized().match_key for item in self.identifiers]
        if len(set(normalized)) != len(normalized):
            raise ValueError("mention contains duplicate identifiers")
        if self.entity_type is EntityType.ORGANIZATION and self.issuer_entity_id is not None:
            raise ValueError("organization mentions cannot specify an issuer")
        if self.entity_type is EntityType.INSTRUMENT and self.parent_entity_id is not None:
            raise ValueError("instrument mentions cannot specify a parent organization")
        return self


class ResolutionPolicy(EntityModel):
    version: str = "entity-resolution-policy-v2"
    auto_link_threshold: float = Field(default=0.99, ge=0.0, le=1.0)
    minimum_margin: float = Field(default=0.10, ge=0.0, le=1.0)
    review_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    strong_identifier_schemes: frozenset[IdentifierScheme] = frozenset(
        {
            IdentifierScheme.LEI,
            IdentifierScheme.BIC,
            IdentifierScheme.CIK,
            IdentifierScheme.ISIN,
            IdentifierScheme.INTERNAL,
        }
    )

    @model_validator(mode="after")
    def _ordered_thresholds(self) -> Self:
        if not self.review_threshold < self.auto_link_threshold:
            raise ValueError("review threshold must be below auto-link threshold")
        return self


class EntityScoreContribution(EntityModel):
    component: str = Field(min_length=1)
    value: float
    explanation: str = Field(min_length=1)


class EntityResolutionCandidate(EntityModel):
    candidate_id: UUID
    resolution_id: UUID
    mention_id: UUID
    entity_id: UUID
    score: float = Field(ge=0.0, le=1.0)
    contributions: tuple[EntityScoreContribution, ...]
    matched_identifier_ids: tuple[UUID, ...] = ()
    matched_name_ids: tuple[UUID, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    resolver_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    generated_at: AwareDatetime
    policy_id: UUID

    @model_validator(mode="after")
    def _deterministic_and_consistent(self) -> Self:
        expected = entity_resolution_candidate_id(self.resolution_id, self.entity_id)
        if self.candidate_id != expected:
            raise ValueError("candidate_id is not the deterministic resolution identity")
        if len(set(self.matched_identifier_ids)) != len(self.matched_identifier_ids):
            raise ValueError("matched_identifier_ids contains duplicates")
        if len(set(self.matched_name_ids)) != len(self.matched_name_ids):
            raise ValueError("matched_name_ids contains duplicates")
        if self.blocked_reasons and self.score != 0.0:
            raise ValueError("blocked candidates must have a zero score")
        return self


class EntityResolution(EntityModel):
    resolution_id: UUID
    mention: EntityMentionContext
    disposition: ResolutionDisposition
    candidates: tuple[EntityResolutionCandidate, ...]
    recommended_candidate_id: UUID | None = None
    score: float = Field(ge=0.0, le=1.0)
    margin: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    resolver_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    resolved_at: AwareDatetime

    @model_validator(mode="after")
    def _recommendation_is_explicit(self) -> Self:
        candidate_ids = {item.candidate_id for item in self.candidates}
        if self.disposition is ResolutionDisposition.ABSTAINED:
            if self.recommended_candidate_id is not None:
                raise ValueError("abstained resolutions cannot recommend a candidate")
        elif self.recommended_candidate_id not in candidate_ids:
            raise ValueError("recommended candidate is absent from the candidate set")
        expected = entity_resolution_id(
            self.mention,
            self.resolver_version,
            self.policy_version,
            self.resolved_at,
        )
        if self.resolution_id != expected:
            raise ValueError("resolution_id is not the deterministic mention/context identity")
        return self


def entity_identifier_id(
    entity_id: UUID,
    identifier: NormalizedIdentifier,
    effective_from: datetime,
    source_id: str,
    source_record_id: str,
) -> UUID:
    return _stable_uuid(
        "identifier",
        entity_id,
        *identifier.match_key,
        effective_from.isoformat(),
        source_id,
        source_record_id,
    )


def entity_name_id(
    entity_id: UUID,
    kind: EntityNameKind,
    normalized_name: str,
    language: str,
    effective_from: datetime,
    source_id: str,
    source_record_id: str,
) -> UUID:
    return _stable_uuid(
        "name",
        entity_id,
        kind,
        normalized_name,
        language,
        effective_from.isoformat(),
        source_id,
        source_record_id,
    )


def entity_relationship_id(
    kind: EntityRelationshipKind,
    subject_entity_id: UUID,
    object_entity_id: UUID,
    effective_from: datetime,
    source_id: str,
    source_record_id: str,
) -> UUID:
    return _stable_uuid(
        "relationship",
        kind,
        subject_entity_id,
        object_entity_id,
        effective_from.isoformat(),
        source_id,
        source_record_id,
    )


def entity_resolution_id(
    mention: EntityMentionContext,
    resolver_version: str,
    policy_version: str,
    as_of: datetime,
) -> UUID:
    identifiers = [item.normalized().match_key for item in mention.identifiers]
    payload = json.dumps(
        {
            "mention_id": str(mention.mention_id),
            "entity_type": mention.entity_type.value,
            "surface": normalize_entity_name(mention.surface),
            "identifiers": sorted(identifiers),
            "jurisdiction": mention.jurisdiction,
            "sector": mention.sector,
            "parent_entity_id": str(mention.parent_entity_id or ""),
            "issuer_entity_id": str(mention.issuer_entity_id or ""),
            "policy_id": str(mention.policy_id),
            "resolver_version": resolver_version,
            "policy_version": policy_version,
            "as_of": as_of.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _stable_uuid("resolution", payload)


def entity_resolution_candidate_id(resolution_id: UUID, entity_id: UUID) -> UUID:
    return _stable_uuid("resolution-candidate", resolution_id, entity_id)


def entity_link_id(
    mention_id: UUID,
    entity_id: UUID,
    resolution_candidate_id: UUID,
) -> UUID:
    return _stable_uuid("entity-link", mention_id, entity_id, resolution_candidate_id)


def entity_link_decision_id(
    entity_link_id_value: UUID | None,
    mention_id: UUID,
    status: str,
    decided_at: datetime,
    decided_by: str,
    reason: str,
) -> UUID:
    return _stable_uuid(
        "entity-link-decision",
        entity_link_id_value or "",
        mention_id,
        status,
        decided_at.isoformat(),
        decided_by,
        reason,
    )


__all__ = [
    "EntityIdentifierRecord",
    "EntityMentionContext",
    "EntityModel",
    "EntityNameKind",
    "EntityNameRecord",
    "EntityRecord",
    "EntityReferenceBundle",
    "EntityRelationshipKind",
    "EntityRelationshipRecord",
    "EntityResolution",
    "EntityResolutionCandidate",
    "EntityScoreContribution",
    "EntityType",
    "IdentifierInput",
    "ResolutionDisposition",
    "ResolutionPolicy",
    "entity_identifier_id",
    "entity_link_decision_id",
    "entity_link_id",
    "entity_name_id",
    "entity_relationship_id",
    "entity_resolution_candidate_id",
    "entity_resolution_id",
]
