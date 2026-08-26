"""Persistence contract and deterministic reference repository for entities."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Protocol, TypeVar, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from fi_intel.entities.models import (
    EntityIdentifierRecord,
    EntityMentionContext,
    EntityNameRecord,
    EntityRecord,
    EntityReferenceBundle,
    EntityRelationshipRecord,
    EntityResolution,
    EntityResolutionCandidate,
    EntityScoreContribution,
)
from fi_intel.entities.normalization import normalize_entity_name
from fi_intel.ledger.models import EntityLinkDecision, EntityLinkStatus


class EntityRepositoryConflictError(RuntimeError):
    """A deterministic entity-intelligence ID was reused with new content."""


class EntityRepositoryInvariantError(RuntimeError):
    """Reference, resolution, or link lifecycle invariants were violated."""


@runtime_checkable
class EntityRepository(Protocol):
    async def register_bundle(self, bundle: EntityReferenceBundle) -> None: ...

    async def matching_bundles(
        self,
        mention: EntityMentionContext,
        as_of: datetime,
    ) -> tuple[EntityReferenceBundle, ...]: ...

    async def append_resolution(self, resolution: EntityResolution) -> None: ...

    async def get_resolution(self, resolution_id: UUID) -> EntityResolution | None: ...

    async def append_link_decision(self, decision: EntityLinkDecision) -> None: ...

    async def active_link_for_mention(
        self,
        mention_id: UUID,
        as_of: datetime | None = None,
    ) -> EntityLinkDecision | None: ...

    async def link_history(self, mention_id: UUID) -> tuple[EntityLinkDecision, ...]: ...

    async def close(self) -> None: ...


ModelT = TypeVar("ModelT", bound=BaseModel)


def _put_immutable(target: dict[UUID, ModelT], key: UUID, value: ModelT) -> None:
    existing = target.get(key)
    if existing is not None and existing != value:
        raise EntityRepositoryConflictError(
            f"immutable entity-intelligence ID {key} has conflicting content"
        )
    target.setdefault(key, value)


def _active(recorded_at: datetime, start: datetime, end: datetime | None, at: datetime) -> bool:
    return recorded_at <= at and start <= at and (end is None or end > at)


class InMemoryEntityRepository:
    """Executable repository contract used by deterministic tests and demos."""

    def __init__(self) -> None:
        self._entities: dict[UUID, EntityRecord] = {}
        self._identifiers: dict[UUID, EntityIdentifierRecord] = {}
        self._names: dict[UUID, EntityNameRecord] = {}
        self._relationships: dict[UUID, EntityRelationshipRecord] = {}
        self._resolutions: dict[UUID, EntityResolution] = {}
        self._candidates: dict[UUID, EntityResolutionCandidate] = {}
        self._link_decisions: dict[UUID, EntityLinkDecision] = {}

    async def register_bundle(self, bundle: EntityReferenceBundle) -> None:
        _put_immutable(self._entities, bundle.entity.entity_id, bundle.entity)
        known_entities = {*self._entities, bundle.entity.entity_id}
        for relationship in bundle.relationships:
            if (
                relationship.subject_entity_id not in known_entities
                or relationship.object_entity_id not in known_entities
            ):
                raise EntityRepositoryInvariantError(
                    "entity relationship references an unregistered entity"
                )
            self._validate_relationship_types(relationship)
        for identifier in bundle.identifiers:
            _put_immutable(self._identifiers, identifier.identifier_id, identifier)
        for name in bundle.names:
            if name.supersedes_name_id is not None:
                prior_name = self._names.get(name.supersedes_name_id)
                if prior_name is None or prior_name.entity_id != name.entity_id:
                    raise EntityRepositoryInvariantError(
                        "superseded entity name is absent or belongs to another entity"
                    )
            _put_immutable(self._names, name.name_id, name)
        for relationship in bundle.relationships:
            if relationship.supersedes_relationship_id is not None:
                prior_relationship = self._relationships.get(
                    relationship.supersedes_relationship_id
                )
                if prior_relationship is None:
                    raise EntityRepositoryInvariantError("superseded entity relationship is absent")
            _put_immutable(
                self._relationships,
                relationship.relationship_id,
                relationship,
            )

    async def matching_bundles(
        self,
        mention: EntityMentionContext,
        as_of: datetime,
    ) -> tuple[EntityReferenceBundle, ...]:
        identifier_keys = {item.normalized().match_key for item in mention.identifiers}
        name_key = normalize_entity_name(mention.surface)
        matched_entities = {
            item.entity_id
            for item in self._identifiers.values()
            if _active(item.recorded_at, item.effective_from, item.effective_to, as_of)
            and (item.scheme.value, item.scope, item.normalized_value) in identifier_keys
        }
        matched_entities.update(
            item.entity_id
            for item in self._names.values()
            if _active(item.recorded_at, item.effective_from, item.effective_to, as_of)
            and item.normalized_name == name_key
        )
        return tuple(
            self._bundle(entity_id, as_of) for entity_id in sorted(matched_entities, key=str)
        )

    async def append_resolution(self, resolution: EntityResolution) -> None:
        if any(item.resolution_id != resolution.resolution_id for item in resolution.candidates):
            raise EntityRepositoryInvariantError("candidate belongs to another resolution")
        for candidate in resolution.candidates:
            if candidate.entity_id not in self._entities:
                raise EntityRepositoryInvariantError("candidate references an unknown entity")
            _put_immutable(self._candidates, candidate.candidate_id, candidate)
        _put_immutable(self._resolutions, resolution.resolution_id, resolution)

    async def get_resolution(self, resolution_id: UUID) -> EntityResolution | None:
        return self._resolutions.get(resolution_id)

    async def append_link_decision(  # noqa: C901
        self, decision: EntityLinkDecision
    ) -> None:
        existing = self._link_decisions.get(decision.decision_id)
        if existing is not None:
            if existing != decision:
                raise EntityRepositoryConflictError(
                    "entity-link decision ID has conflicting content"
                )
            return
        history = list(await self.link_history(decision.mention_id))
        if history and decision.decided_at <= history[-1].decided_at:
            raise EntityRepositoryInvariantError("entity-link decision time must increase")
        active = _active_link(history)
        if decision.status is EntityLinkStatus.LINKED:
            candidate = (
                self._candidates.get(decision.resolution_candidate_id)
                if decision.resolution_candidate_id is not None
                else None
            )
            if candidate is None:
                raise EntityRepositoryInvariantError(
                    "linked decision requires a persisted resolution candidate"
                )
            if (
                candidate.mention_id != decision.mention_id
                or candidate.entity_id != decision.entity_id
            ):
                raise EntityRepositoryInvariantError(
                    "entity link differs from its resolution candidate"
                )
            if candidate.blocked_reasons:
                raise EntityRepositoryInvariantError("a blocked candidate cannot be linked")
            if active is not None and decision.supersedes_entity_link_id != active.entity_link_id:
                raise EntityRepositoryInvariantError(
                    "replacement link must explicitly supersede the active link"
                )
            if active is None and decision.supersedes_entity_link_id is not None:
                raise EntityRepositoryInvariantError("superseded link is not active")
        elif decision.status is EntityLinkStatus.INVALIDATED:
            if active is None or decision.invalidates_entity_link_id != active.entity_link_id:
                raise EntityRepositoryInvariantError("invalidated link is not active")
        elif decision.resolution_candidate_id is not None:
            candidate = self._candidates.get(decision.resolution_candidate_id)
            if candidate is None or candidate.mention_id != decision.mention_id:
                raise EntityRepositoryInvariantError(
                    "review decision references an unknown resolution candidate"
                )
        self._link_decisions[decision.decision_id] = decision

    async def active_link_for_mention(
        self,
        mention_id: UUID,
        as_of: datetime | None = None,
    ) -> EntityLinkDecision | None:
        history = list(await self.link_history(mention_id))
        if as_of is not None:
            history = [item for item in history if item.decided_at <= as_of]
        return _active_link(history)

    async def link_history(self, mention_id: UUID) -> tuple[EntityLinkDecision, ...]:
        return tuple(
            sorted(
                (item for item in self._link_decisions.values() if item.mention_id == mention_id),
                key=lambda item: (item.decided_at, str(item.decision_id)),
            )
        )

    async def close(self) -> None:
        return None

    def _bundle(self, entity_id: UUID, as_of: datetime) -> EntityReferenceBundle:
        return EntityReferenceBundle(
            entity=self._entities[entity_id],
            identifiers=tuple(
                sorted(
                    (
                        item
                        for item in self._identifiers.values()
                        if item.entity_id == entity_id
                        and _active(
                            item.recorded_at,
                            item.effective_from,
                            item.effective_to,
                            as_of,
                        )
                    ),
                    key=lambda item: str(item.identifier_id),
                )
            ),
            names=tuple(
                sorted(
                    (
                        item
                        for item in self._names.values()
                        if item.entity_id == entity_id
                        and _active(
                            item.recorded_at,
                            item.effective_from,
                            item.effective_to,
                            as_of,
                        )
                    ),
                    key=lambda item: str(item.name_id),
                )
            ),
            relationships=tuple(
                sorted(
                    (
                        item
                        for item in self._relationships.values()
                        if entity_id in {item.subject_entity_id, item.object_entity_id}
                        and _active(
                            item.recorded_at,
                            item.effective_from,
                            item.effective_to,
                            as_of,
                        )
                    ),
                    key=lambda item: str(item.relationship_id),
                )
            ),
        )

    def _validate_relationship_types(self, relationship: EntityRelationshipRecord) -> None:
        subject = self._entities[relationship.subject_entity_id]
        object_ = self._entities[relationship.object_entity_id]
        if relationship.kind.value == "parent_of" and (
            subject.entity_type.value != "organization"
            or object_.entity_type.value != "organization"
        ):
            raise EntityRepositoryInvariantError("parent relationships require organizations")
        if relationship.kind.value == "issuer_of" and (
            subject.entity_type.value != "organization" or object_.entity_type.value != "instrument"
        ):
            raise EntityRepositoryInvariantError(
                "issuer relationships require organization -> instrument"
            )
        if relationship.kind.value == "successor_of" and (
            subject.entity_type is not object_.entity_type
        ):
            raise EntityRepositoryInvariantError("successor relationships require equal types")


class PostgresEntityRepository:
    """PostgreSQL repository targeting migration ``0008_entity_intelligence.sql``."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def register_bundle(self, bundle: EntityReferenceBundle) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO entity_identity (
                    entity_id, entity_type, canonical_name, created_at, policy_id
                ) VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT (entity_id) DO NOTHING
                """,
                bundle.entity.entity_id,
                bundle.entity.entity_type.value,
                bundle.entity.canonical_name,
                bundle.entity.created_at,
                bundle.entity.policy_id,
            )
            await conn.execute(
                """
                INSERT INTO entity_profile_v2 (entity_id, jurisdiction, sector)
                VALUES ($1,$2,$3) ON CONFLICT (entity_id) DO NOTHING
                """,
                bundle.entity.entity_id,
                bundle.entity.jurisdiction,
                bundle.entity.sector,
            )
            for identifier in bundle.identifiers:
                await conn.execute(
                    """
                    INSERT INTO entity_identifier_v2 (
                        identifier_id, entity_id, scheme, value, normalized_value,
                        scope, effective_from, effective_to, recorded_at, source_id,
                        source_record_id, policy_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (identifier_id) DO NOTHING
                    """,
                    identifier.identifier_id,
                    identifier.entity_id,
                    identifier.scheme.value,
                    identifier.value,
                    identifier.normalized_value,
                    identifier.scope,
                    identifier.effective_from,
                    identifier.effective_to,
                    identifier.recorded_at,
                    identifier.source_id,
                    identifier.source_record_id,
                    identifier.policy_id,
                )
            for name in bundle.names:
                await conn.execute(
                    """
                    INSERT INTO entity_name_v2 (
                        name_id, entity_id, kind, name, normalized_name, language,
                        effective_from, effective_to, recorded_at, source_id,
                        source_record_id, policy_id, supersedes_name_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    ON CONFLICT (name_id) DO NOTHING
                    """,
                    name.name_id,
                    name.entity_id,
                    name.kind.value,
                    name.name,
                    name.normalized_name,
                    name.language,
                    name.effective_from,
                    name.effective_to,
                    name.recorded_at,
                    name.source_id,
                    name.source_record_id,
                    name.policy_id,
                    name.supersedes_name_id,
                )
            for relationship in bundle.relationships:
                await conn.execute(
                    """
                    INSERT INTO entity_relationship_v2 (
                        relationship_id, kind, subject_entity_id, object_entity_id,
                        effective_from, effective_to, recorded_at, source_id,
                        source_record_id, policy_id, supersedes_relationship_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    ON CONFLICT (relationship_id) DO NOTHING
                    """,
                    relationship.relationship_id,
                    relationship.kind.value,
                    relationship.subject_entity_id,
                    relationship.object_entity_id,
                    relationship.effective_from,
                    relationship.effective_to,
                    relationship.recorded_at,
                    relationship.source_id,
                    relationship.source_record_id,
                    relationship.policy_id,
                    relationship.supersedes_relationship_id,
                )

    async def matching_bundles(
        self,
        mention: EntityMentionContext,
        as_of: datetime,
    ) -> tuple[EntityReferenceBundle, ...]:
        pool = await self._get_pool()
        identifiers = [item.normalized() for item in mention.identifiers]
        async with pool.acquire() as conn:
            entity_ids: set[UUID] = set()
            for item in identifiers:
                rows = await conn.fetch(
                    """
                    SELECT entity_id FROM entity_identifier_v2
                    WHERE scheme = $1 AND normalized_value = $2 AND scope = $3
                      AND recorded_at <= $4 AND effective_from <= $4
                      AND (effective_to IS NULL OR effective_to > $4)
                    """,
                    item.scheme.value,
                    item.value,
                    item.scope,
                    as_of,
                )
                entity_ids.update(row["entity_id"] for row in rows)
            rows = await conn.fetch(
                """
                SELECT entity_id FROM entity_name_v2
                WHERE normalized_name = $1 AND recorded_at <= $2
                  AND effective_from <= $2
                  AND (effective_to IS NULL OR effective_to > $2)
                """,
                normalize_entity_name(mention.surface),
                as_of,
            )
            entity_ids.update(row["entity_id"] for row in rows)
            return tuple(
                [
                    await self._load_bundle(conn, entity_id, as_of)
                    for entity_id in sorted(entity_ids, key=str)
                ]
            )

    async def append_resolution(self, resolution: EntityResolution) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO entity_resolution_v2 (
                    resolution_id, mention_id, mention_context, disposition,
                    recommended_candidate_id, score, margin, reason,
                    resolver_version, policy_version, resolved_at, policy_id
                ) VALUES ($1,$2,$3::jsonb,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (resolution_id) DO NOTHING
                """,
                resolution.resolution_id,
                resolution.mention.mention_id,
                _json(resolution.mention),
                resolution.disposition.value,
                resolution.recommended_candidate_id,
                resolution.score,
                resolution.margin,
                resolution.reason,
                resolution.resolver_version,
                resolution.policy_version,
                resolution.resolved_at,
                resolution.mention.policy_id,
            )
            for item in resolution.candidates:
                await conn.execute(
                    """
                    INSERT INTO entity_resolution_candidate_v2 (
                        candidate_id, resolution_id, mention_id, entity_id, score,
                        contributions, matched_identifier_ids, matched_name_ids,
                        blocked_reasons, resolver_version, policy_version,
                        generated_at, policy_id
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,$11,$12,$13
                    ) ON CONFLICT (candidate_id) DO NOTHING
                    """,
                    item.candidate_id,
                    item.resolution_id,
                    item.mention_id,
                    item.entity_id,
                    item.score,
                    json.dumps(
                        [value.model_dump(mode="json") for value in item.contributions],
                        sort_keys=True,
                    ),
                    list(item.matched_identifier_ids),
                    list(item.matched_name_ids),
                    list(item.blocked_reasons),
                    item.resolver_version,
                    item.policy_version,
                    item.generated_at,
                    item.policy_id,
                )

    async def get_resolution(self, resolution_id: UUID) -> EntityResolution | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM entity_resolution_v2 WHERE resolution_id = $1",
                resolution_id,
            )
            if row is None:
                return None
            candidate_rows = await conn.fetch(
                """
                SELECT * FROM entity_resolution_candidate_v2
                WHERE resolution_id = $1 ORDER BY score DESC, entity_id
                """,
                resolution_id,
            )
        mention = EntityMentionContext.model_validate(row["mention_context"])
        candidates = tuple(
            EntityResolutionCandidate(
                candidate_id=item["candidate_id"],
                resolution_id=item["resolution_id"],
                mention_id=item["mention_id"],
                entity_id=item["entity_id"],
                score=item["score"],
                contributions=tuple(
                    EntityScoreContribution.model_validate(value) for value in item["contributions"]
                ),
                matched_identifier_ids=tuple(item["matched_identifier_ids"]),
                matched_name_ids=tuple(item["matched_name_ids"]),
                blocked_reasons=tuple(item["blocked_reasons"]),
                resolver_version=item["resolver_version"],
                policy_version=item["policy_version"],
                generated_at=item["generated_at"],
                policy_id=item["policy_id"],
            )
            for item in candidate_rows
        )
        return EntityResolution(
            resolution_id=row["resolution_id"],
            mention=mention,
            disposition=row["disposition"],
            candidates=candidates,
            recommended_candidate_id=row["recommended_candidate_id"],
            score=row["score"],
            margin=row["margin"],
            reason=row["reason"],
            resolver_version=row["resolver_version"],
            policy_version=row["policy_version"],
            resolved_at=row["resolved_at"],
        )

    async def append_link_decision(self, decision: EntityLinkDecision) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO entity_link_decision (
                    decision_id, mention_id, status, entity_link_id, entity_id,
                    resolution_candidate_id, supersedes_entity_link_id,
                    invalidates_entity_link_id, candidate_entity_ids, confidence,
                    resolver_version, reason, decided_at, decided_by, policy_id
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

    async def active_link_for_mention(
        self,
        mention_id: UUID,
        as_of: datetime | None = None,
    ) -> EntityLinkDecision | None:
        history = list(await self.link_history(mention_id))
        if as_of is not None:
            history = [item for item in history if item.decided_at <= as_of]
        return _active_link(history)

    async def link_history(self, mention_id: UUID) -> tuple[EntityLinkDecision, ...]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM entity_link_decision
                WHERE mention_id = $1 ORDER BY decided_at, decision_id
                """,
                mention_id,
            )
        return tuple(_link_decision_from_row(row) for row in rows)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @staticmethod
    async def _load_bundle(
        conn: asyncpg.Connection,
        entity_id: UUID,
        as_of: datetime,
    ) -> EntityReferenceBundle:
        row = await conn.fetchrow(
            """
            SELECT e.*, p.jurisdiction, p.sector
            FROM entity_identity e JOIN entity_profile_v2 p USING (entity_id)
            WHERE entity_id = $1
            """,
            entity_id,
        )
        if row is None:
            raise EntityRepositoryInvariantError("matched entity has no v2 profile")
        identifier_rows = await conn.fetch(
            """
            SELECT * FROM entity_identifier_v2 WHERE entity_id = $1
              AND recorded_at <= $2 AND effective_from <= $2
              AND (effective_to IS NULL OR effective_to > $2)
            ORDER BY identifier_id
            """,
            entity_id,
            as_of,
        )
        name_rows = await conn.fetch(
            """
            SELECT * FROM entity_name_v2 WHERE entity_id = $1
              AND recorded_at <= $2 AND effective_from <= $2
              AND (effective_to IS NULL OR effective_to > $2)
            ORDER BY name_id
            """,
            entity_id,
            as_of,
        )
        relationship_rows = await conn.fetch(
            """
            SELECT * FROM entity_relationship_v2
            WHERE (subject_entity_id = $1 OR object_entity_id = $1)
              AND recorded_at <= $2 AND effective_from <= $2
              AND (effective_to IS NULL OR effective_to > $2)
            ORDER BY relationship_id
            """,
            entity_id,
            as_of,
        )
        return EntityReferenceBundle(
            entity=EntityRecord(
                entity_id=row["entity_id"],
                entity_type=row["entity_type"],
                canonical_name=row["canonical_name"],
                jurisdiction=row["jurisdiction"],
                sector=row["sector"],
                created_at=row["created_at"],
                policy_id=row["policy_id"],
            ),
            identifiers=tuple(
                EntityIdentifierRecord.model_validate(dict(item)) for item in identifier_rows
            ),
            names=tuple(EntityNameRecord.model_validate(dict(item)) for item in name_rows),
            relationships=tuple(
                EntityRelationshipRecord.model_validate(dict(item)) for item in relationship_rows
            ),
        )


def _active_link(history: Iterable[EntityLinkDecision]) -> EntityLinkDecision | None:
    active: EntityLinkDecision | None = None
    for decision in history:
        if decision.status is EntityLinkStatus.LINKED:
            if active is not None and decision.supersedes_entity_link_id != active.entity_link_id:
                continue
            active = decision
        elif (
            decision.status is EntityLinkStatus.INVALIDATED
            and active is not None
            and decision.invalidates_entity_link_id == active.entity_link_id
        ):
            active = None
    return active


def _json(value: BaseModel) -> str:
    return json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _link_decision_from_row(row: asyncpg.Record) -> EntityLinkDecision:
    return EntityLinkDecision(
        decision_id=row["decision_id"],
        mention_id=row["mention_id"],
        status=row["status"],
        entity_link_id=row["entity_link_id"],
        entity_id=row["entity_id"],
        resolution_candidate_id=row["resolution_candidate_id"],
        supersedes_entity_link_id=row["supersedes_entity_link_id"],
        invalidates_entity_link_id=row["invalidates_entity_link_id"],
        candidate_entity_ids=tuple(row["candidate_entity_ids"]),
        confidence=row["confidence"],
        resolver_version=row["resolver_version"],
        reason=row["reason"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        policy_id=row["policy_id"],
    )


__all__ = [
    "EntityRepository",
    "EntityRepositoryConflictError",
    "EntityRepositoryInvariantError",
    "InMemoryEntityRepository",
    "PostgresEntityRepository",
]
