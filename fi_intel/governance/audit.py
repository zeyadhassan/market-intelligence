"""Access audit for document retrievals.

Each record identifies the caller, entitlement group, returned documents,
and run.

The audit writer is fail-closed: if the audit write fails, the retrieval
fails. An unaudited retrieval is worse than a failed one.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

import asyncpg
from pydantic import BaseModel, ConfigDict


class AccessEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    principal: str
    entitlement_group: str
    source_id: str | None = None
    doc_id: str | None = None
    operation: str = "retrieval"
    result_count: int = 1
    accessed_at: datetime


@runtime_checkable
class AuditLog(Protocol):
    async def record(self, events: list[AccessEvent]) -> None: ...


class InMemoryAuditLog:
    def __init__(self) -> None:
        self.events: list[AccessEvent] = []

    async def record(self, events: list[AccessEvent]) -> None:
        self.events.extend(events)


class PostgresAuditLog:
    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def record(self, events: list[AccessEvent]) -> None:
        if not events:
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO access_log
                    (run_id, principal, entitlement_group, source_id, doc_id,
                     operation, result_count, accessed_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                [
                    (
                        e.run_id,
                        e.principal,
                        e.entitlement_group,
                        e.source_id,
                        e.doc_id,
                        e.operation,
                        e.result_count,
                        e.accessed_at,
                    )
                    for e in events
                ],
            )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None
