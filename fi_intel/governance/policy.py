"""Trusted graph-access policy resolution.

Corpus retrieval enforces grants in Postgres. Graph reads use the same
source grants, resolved once into an immutable context, and additionally
enforce every assertion's barrier classification in Cypher.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from fi_intel.retrieval.entitlement import Principal, Side


class GraphAccessContext(BaseModel):
    """Verified caller plus the source allowlist produced by policy."""

    model_config = ConfigDict(frozen=True)

    principal: Principal
    allowed_source_ids: frozenset[str]
    policy_version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    require_audit: bool = True


def trusted_test_access(
    *source_ids: str,
    side: Side = Side.PUBLIC,
    entitlement_group: str = "test",
    principal_id: str = "test.graph",
    require_audit: bool = False,
) -> GraphAccessContext:
    """Build an explicit test-only policy context.

    Production code must resolve grants from PostgresEntitlementResolver;
    keeping this helper visibly test-named prevents a permissive default from
    becoming an accidental authorization bypass.
    """

    return GraphAccessContext(
        principal=Principal(
            principal_id=principal_id,
            entitlement_group=entitlement_group,
            side=side,
        ),
        allowed_source_ids=frozenset(source_ids),
        policy_version="trusted-test-v1",
        run_id="test-graph-run",
        require_audit=require_audit,
    )


@runtime_checkable
class EntitlementResolver(Protocol):
    async def resolve(self, principal: Principal, run_id: str) -> GraphAccessContext: ...

    async def close(self) -> None: ...


class PostgresEntitlementResolver:
    """Resolve a verified principal's current licensed-source grants."""

    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=2)
        return self._pool

    async def resolve(self, principal: Principal, run_id: str) -> GraphAccessContext:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT eg.source_id
            FROM entitlement_grant eg
            JOIN source_registry sr ON sr.source_id = eg.source_id
            WHERE eg.entitlement_group = $1
              AND sr.licensed
              AND (sr.barrier_side = 'public' OR $2 = 'private')
            ORDER BY eg.source_id
            """,
            principal.entitlement_group,
            str(principal.side),
        )
        return GraphAccessContext(
            principal=principal,
            allowed_source_ids=frozenset(row["source_id"] for row in rows),
            policy_version="postgres-entitlement-v1",
            run_id=run_id,
            require_audit=True,
        )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None
