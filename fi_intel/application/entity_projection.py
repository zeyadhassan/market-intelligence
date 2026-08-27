"""Idempotent projection of admitted GLEIF references into entity-v2."""

from __future__ import annotations

from datetime import datetime

import asyncpg

from fi_intel.entities.identifiers import IdentifierScheme, IdentifierValidationError
from fi_intel.entities.models import (
    EntityIdentifierRecord,
    EntityNameKind,
    EntityNameRecord,
    EntityRecord,
    EntityReferenceBundle,
    EntityType,
    IdentifierInput,
    entity_identifier_id,
    entity_name_id,
)
from fi_intel.entities.normalization import normalize_entity_name
from fi_intel.entities.repository import PostgresEntityRepository
from fi_intel.ledger.models import AccessPolicy, entity_identity_id


class EntityReferenceProjection:
    """Bridge the admitted reference table into the governed entity-v2 plane."""

    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None
        self._repository = PostgresEntityRepository(dsn, pool=pool)

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=2)
        return self._pool

    async def synchronize(
        self,
        policy: AccessPolicy,
        entity_leis: frozenset[str] | None = None,
    ) -> int:
        pool = await self._get_pool()
        if entity_leis is None:
            rows = await pool.fetch(
                """
                SELECT lei, canonical_name, jurisdiction, sector, created_at
                FROM entity WHERE lei IS NOT NULL ORDER BY lei
                """
            )
        else:
            rows = await pool.fetch(
                """
                SELECT lei, canonical_name, jurisdiction, sector, created_at
                FROM entity WHERE lei = ANY($1::text[]) ORDER BY lei
                """,
                sorted(entity_leis),
            )
        projected = 0
        for row in rows:
            try:
                identifier = IdentifierInput(
                    scheme=IdentifierScheme.LEI, value=str(row["lei"])
                ).normalized()
            except IdentifierValidationError:
                continue
            entity_id = entity_identity_id("Organization", identifier.value)
            effective_from: datetime = row["created_at"]
            source_record_id = f"GLEIF-{identifier.value}"
            name = str(row["canonical_name"])
            bundle = EntityReferenceBundle(
                entity=EntityRecord(
                    entity_id=entity_id,
                    entity_type=EntityType.ORGANIZATION,
                    canonical_name=name,
                    jurisdiction=(str(row["jurisdiction"])[:2] if row["jurisdiction"] else None),
                    sector=str(row["sector"]) if row["sector"] else None,
                    created_at=effective_from,
                    policy_id=policy.policy_id,
                ),
                identifiers=(
                    EntityIdentifierRecord(
                        identifier_id=entity_identifier_id(
                            entity_id,
                            identifier,
                            effective_from,
                            "gleif",
                            source_record_id,
                        ),
                        entity_id=entity_id,
                        scheme=IdentifierScheme.LEI,
                        value=identifier.value,
                        normalized_value=identifier.value,
                        effective_from=effective_from,
                        recorded_at=effective_from,
                        source_id="gleif",
                        source_record_id=source_record_id,
                        policy_id=policy.policy_id,
                    ),
                ),
                names=(
                    EntityNameRecord(
                        name_id=entity_name_id(
                            entity_id,
                            EntityNameKind.LEGAL,
                            normalize_entity_name(name),
                            "en",
                            effective_from,
                            "gleif",
                            source_record_id,
                        ),
                        entity_id=entity_id,
                        kind=EntityNameKind.LEGAL,
                        name=name,
                        normalized_name=normalize_entity_name(name),
                        language="en",
                        effective_from=effective_from,
                        recorded_at=effective_from,
                        source_id="gleif",
                        source_record_id=source_record_id,
                        policy_id=policy.policy_id,
                    ),
                ),
            )
            await self._repository.register_bundle(bundle)
            projected += 1
        return projected

    async def close(self) -> None:
        await self._repository.close()
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


__all__ = ["EntityReferenceProjection"]
