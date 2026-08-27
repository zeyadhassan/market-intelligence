"""Governed entity-v2 entry contract for graph investigation."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from fi_intel.entities.identifiers import IdentifierScheme
from fi_intel.entities.models import (
    EntityMentionContext,
    EntityType,
    IdentifierInput,
    ResolutionDisposition,
)
from fi_intel.entities.normalization import normalize_entity_name
from fi_intel.entities.repository import EntityRepository
from fi_intel.entities.service import EntityResolutionService
from fi_intel.retrieval.entitlement import Principal


class GraphEntryStatus(StrEnum):
    RESOLVED = "resolved"
    REVIEW_REQUIRED = "review_required"
    ABSTAINED = "abstained"


class GraphEntityReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    display_name: str = Field(min_length=1)
    entity_type: EntityType = EntityType.ORGANIZATION
    identifier_scheme: IdentifierScheme | None = None
    identifier_value: str | None = None
    identifier_scope: str | None = None
    jurisdiction: str | None = None


class GraphEntryRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    principal: Principal
    as_of: AwareDatetime
    reference: GraphEntityReference
    signal_id: str = Field(min_length=1)
    allowed_relation_families: frozenset[str] = frozenset()


class GraphEntryAlternative(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_id: UUID
    canonical_name: str
    score: float = Field(ge=0.0, le=1.0)
    blocked_reasons: tuple[str, ...] = ()


class GraphEntryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: GraphEntryStatus
    canonical_entity_id: UUID | None = None
    canonical_name: str | None = None
    lei: str | None = None
    matched_identifier_or_name: str
    resolver_version: str
    resolution_score: float = Field(ge=0.0, le=1.0)
    ambiguity_margin: float = Field(ge=0.0, le=1.0)
    alternatives: tuple[GraphEntryAlternative, ...] = ()
    authorization_scope: str
    current_state_snapshot: dict[str, str] = Field(default_factory=dict)
    reason: str


@runtime_checkable
class GraphEntryResolverPort(Protocol):
    async def resolve(self, request: GraphEntryRequest) -> GraphEntryResult: ...

    async def close(self) -> None: ...


class GraphEntryResolver:
    """Resolve an entry conservatively through governed entity-v2 records."""

    def __init__(
        self,
        service: EntityResolutionService,
        repository: EntityRepository,
        *,
        policy_id: UUID,
    ) -> None:
        self._service = service
        self._repository = repository
        self._policy_id = policy_id

    async def resolve(self, request: GraphEntryRequest) -> GraphEntryResult:
        reference = request.reference
        identifiers: tuple[IdentifierInput, ...] = ()
        if reference.identifier_scheme is not None and reference.identifier_value is not None:
            kwargs: dict[str, object] = {
                "scheme": reference.identifier_scheme,
                "value": reference.identifier_value,
            }
            if reference.identifier_scheme is IdentifierScheme.TICKER:
                kwargs["venue"] = reference.identifier_scope
            elif reference.identifier_scheme is IdentifierScheme.INTERNAL:
                kwargs["namespace"] = reference.identifier_scope
            identifiers = (IdentifierInput.model_validate(kwargs),)
        digest = hashlib.sha256(
            (
                f"{request.signal_id}|{reference.entity_type}|{reference.display_name}|"
                f"{reference.identifier_scheme}|{reference.identifier_value}|{request.as_of.isoformat()}"
            ).encode()
        ).digest()[:16]
        mention = EntityMentionContext(
            mention_id=UUID(bytes=digest),
            entity_type=reference.entity_type,
            surface=reference.display_name,
            identifiers=identifiers,
            jurisdiction=reference.jurisdiction,
            policy_id=self._policy_id,
        )
        resolution = await self._service.resolve(mention, as_of=request.as_of)
        bundles = await self._repository.matching_bundles(mention, request.as_of)
        by_entity = {bundle.entity.entity_id: bundle for bundle in bundles}
        alternatives = tuple(
            GraphEntryAlternative(
                entity_id=candidate.entity_id,
                canonical_name=by_entity[candidate.entity_id].entity.canonical_name,
                score=candidate.score,
                blocked_reasons=candidate.blocked_reasons,
            )
            for candidate in resolution.candidates
            if candidate.entity_id in by_entity
        )
        scope = hashlib.sha256(
            (
                f"{request.principal.principal_id}|{request.principal.entitlement_group}|"
                f"{request.principal.side}|{','.join(sorted(request.allowed_relation_families))}"
            ).encode()
        ).hexdigest()
        selected = next(
            (
                candidate
                for candidate in resolution.candidates
                if candidate.candidate_id == resolution.recommended_candidate_id
            ),
            None,
        )
        if resolution.disposition is not ResolutionDisposition.AUTO_LINK or selected is None:
            status = (
                GraphEntryStatus.REVIEW_REQUIRED
                if resolution.disposition is ResolutionDisposition.REVIEW_REQUIRED
                else GraphEntryStatus.ABSTAINED
            )
            return GraphEntryResult(
                status=status,
                matched_identifier_or_name=reference.identifier_value or reference.display_name,
                resolver_version=resolution.resolver_version,
                resolution_score=resolution.score,
                ambiguity_margin=resolution.margin,
                alternatives=alternatives,
                authorization_scope=scope,
                reason=resolution.reason,
            )
        bundle = by_entity[selected.entity_id]
        lei = next(
            (
                item.normalized_value
                for item in bundle.identifiers
                if item.scheme is IdentifierScheme.LEI
            ),
            None,
        )
        return GraphEntryResult(
            status=GraphEntryStatus.RESOLVED,
            canonical_entity_id=bundle.entity.entity_id,
            canonical_name=bundle.entity.canonical_name,
            lei=lei,
            matched_identifier_or_name=reference.identifier_value or reference.display_name,
            resolver_version=resolution.resolver_version,
            resolution_score=resolution.score,
            ambiguity_margin=resolution.margin,
            alternatives=alternatives,
            authorization_scope=scope,
            current_state_snapshot={
                "entity_type": bundle.entity.entity_type.value,
                "jurisdiction": bundle.entity.jurisdiction or "",
                "sector": bundle.entity.sector or "",
            },
            reason=resolution.reason,
        )

    async def close(self) -> None:
        return None


class PostgresGraphEntryResolver:
    """Read-only governed identifier/name entry over active entity-v2 records."""

    def __init__(
        self,
        dsn: str,
        *,
        resolver_version: str = "entity-entry-v2",
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self._dsn = dsn
        self._resolver_version = resolver_version
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    @staticmethod
    def _scope(request: GraphEntryRequest) -> str:
        return hashlib.sha256(
            (
                f"{request.principal.principal_id}|"
                f"{request.principal.entitlement_group}|{request.principal.side}|"
                f"{','.join(sorted(request.allowed_relation_families))}"
            ).encode()
        ).hexdigest()

    async def resolve(self, request: GraphEntryRequest) -> GraphEntryResult:
        reference = request.reference
        pool = await self._get_pool()
        if reference.identifier_scheme is not None and reference.identifier_value is not None:
            normalized = IdentifierInput(
                scheme=reference.identifier_scheme,
                value=reference.identifier_value,
                **(
                    {"venue": reference.identifier_scope}
                    if reference.identifier_scheme is IdentifierScheme.TICKER
                    else (
                        {"namespace": reference.identifier_scope}
                        if reference.identifier_scheme is IdentifierScheme.INTERNAL
                        else {}
                    )
                ),
            ).normalized()
            rows = await pool.fetch(
                """
                SELECT identity.entity_id, identity.canonical_name,
                       profile.jurisdiction, profile.sector, identifier.normalized_value
                FROM entity_identifier_v2 identifier
                JOIN entity_identity identity USING (entity_id)
                JOIN access_policy policy ON policy.policy_id = identity.policy_id
                LEFT JOIN entity_profile_v2 profile USING (entity_id)
                WHERE identifier.scheme = $1 AND identifier.normalized_value = $2
                  AND identifier.scope = $3
                  AND identifier.recorded_at <= $4 AND identifier.effective_from <= $4
                  AND (identifier.effective_to IS NULL OR identifier.effective_to > $4)
                  AND $5 = ANY(policy.allowed_entitlement_groups)
                  AND (policy.barrier_side = 'public' OR $6 = 'private')
                ORDER BY identity.entity_id
                """,
                normalized.scheme.value,
                normalized.value,
                normalized.scope,
                request.as_of,
                request.principal.entitlement_group,
                request.principal.side.value,
            )
            matched = reference.identifier_value
        else:
            rows = await pool.fetch(
                """
                SELECT identity.entity_id, identity.canonical_name,
                       profile.jurisdiction, profile.sector, lei.normalized_value
                FROM entity_name_v2 name
                JOIN entity_identity identity USING (entity_id)
                JOIN access_policy policy ON policy.policy_id = identity.policy_id
                LEFT JOIN entity_profile_v2 profile USING (entity_id)
                LEFT JOIN entity_identifier_v2 lei
                  ON lei.entity_id = identity.entity_id AND lei.scheme = 'lei'
                 AND lei.recorded_at <= $2 AND lei.effective_from <= $2
                 AND (lei.effective_to IS NULL OR lei.effective_to > $2)
                WHERE name.normalized_name = $1
                  AND name.recorded_at <= $2 AND name.effective_from <= $2
                  AND (name.effective_to IS NULL OR name.effective_to > $2)
                  AND $3 = ANY(policy.allowed_entitlement_groups)
                  AND (policy.barrier_side = 'public' OR $4 = 'private')
                ORDER BY identity.entity_id
                """,
                normalize_entity_name(reference.display_name),
                request.as_of,
                request.principal.entitlement_group,
                request.principal.side.value,
            )
            matched = reference.display_name
        alternatives = tuple(
            GraphEntryAlternative(
                entity_id=row["entity_id"],
                canonical_name=row["canonical_name"],
                score=1.0,
            )
            for row in rows
        )
        if len(rows) != 1:
            return GraphEntryResult(
                status=(GraphEntryStatus.REVIEW_REQUIRED if rows else GraphEntryStatus.ABSTAINED),
                matched_identifier_or_name=matched,
                resolver_version=self._resolver_version,
                resolution_score=1.0 if rows else 0.0,
                ambiguity_margin=0.0,
                alternatives=alternatives,
                authorization_scope=self._scope(request),
                reason=(
                    "multiple active governed identities matched"
                    if rows
                    else "no active authorized governed identity matched"
                ),
            )
        row = rows[0]
        return GraphEntryResult(
            status=GraphEntryStatus.RESOLVED,
            canonical_entity_id=row["entity_id"],
            canonical_name=row["canonical_name"],
            lei=row["normalized_value"],
            matched_identifier_or_name=matched,
            resolver_version=self._resolver_version,
            resolution_score=1.0,
            ambiguity_margin=1.0,
            alternatives=alternatives,
            authorization_scope=self._scope(request),
            current_state_snapshot={
                "entity_type": reference.entity_type.value,
                "jurisdiction": row["jurisdiction"] or "",
                "sector": row["sector"] or "",
            },
            reason="unique active governed identifier/name match",
        )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


__all__ = [
    "GraphEntityReference",
    "GraphEntryAlternative",
    "GraphEntryRequest",
    "GraphEntryResolver",
    "GraphEntryResolverPort",
    "GraphEntryResult",
    "GraphEntryStatus",
    "PostgresGraphEntryResolver",
]
