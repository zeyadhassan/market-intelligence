"""Postgres-backed ResolutionStore.

Entities are keyed by LEI when known. Resolutions are append-only rows;
the schema requires a resolver and a bounded score.
"""

import asyncpg

from fi_intel.ingest.resolve import (
    DocumentEntityLink,
    QueuedMention,
    ReferenceEntity,
    Resolution,
    ResolverName,
)
from fi_intel.sources.canonical import CanonicalDocument


class PostgresResolutionStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def load_reference(self, docs: list[CanonicalDocument]) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for doc in docs:
                lei = doc.identifiers.get("lei")
                if lei is None:
                    msg = f"reference doc {doc.doc_id!r} carries no LEI"
                    raise ValueError(msg)
                await conn.execute(
                    """
                    INSERT INTO entity (lei, canonical_name, jurisdiction, sector)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (lei) DO NOTHING
                    """,
                    lei,
                    doc.metadata["legal_name"],
                    doc.metadata["jurisdiction"],
                    doc.metadata["sector"],
                )
                parent_lei = doc.metadata.get("parent_lei") or None
                if parent_lei is not None:
                    await conn.execute(
                        """
                        INSERT INTO entity_parent (child_lei, parent_lei)
                        VALUES ($1, $2)
                        ON CONFLICT (child_lei) DO NOTHING
                        """,
                        lei,
                        parent_lei,
                    )

    async def reference_entities(self) -> list[ReferenceEntity]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT e.lei, e.canonical_name, e.jurisdiction, e.sector,
                   p.parent_lei
            FROM entity e
            LEFT JOIN entity_parent p ON p.child_lei = e.lei
            WHERE e.lei IS NOT NULL
            """
        )
        return [
            ReferenceEntity(
                lei=row["lei"],
                legal_name=row["canonical_name"],
                jurisdiction=row["jurisdiction"],
                sector=row["sector"],
                parent_lei=row["parent_lei"],
            )
            for row in rows
        ]

    async def record_resolution(self, resolution: Resolution) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT entity_id FROM entity WHERE lei = $1", resolution.lei)
            if row is None:
                msg = f"resolution targets unknown LEI {resolution.lei!r}"
                raise ValueError(msg)
            resolution_id = await conn.fetchval(
                """
                INSERT INTO entity_resolution
                    (entity_id, source_id, doc_id, mention_text, resolver, score, resolved_at)
                SELECT $1, $2, $3, $4, $5, $6, $7
                WHERE NOT EXISTS (
                    SELECT 1 FROM entity_resolution
                    WHERE source_id = $2 AND doc_id = $3
                      AND lower(mention_text) = lower($4)
                )
                RETURNING resolution_id
                """,
                row["entity_id"],
                resolution.source_id,
                resolution.doc_id,
                resolution.mention_text,
                str(resolution.resolver),
                resolution.score,
                resolution.recorded_at,
            )
            if resolution_id is None:
                resolution_id = await conn.fetchval(
                    """
                    SELECT resolution_id FROM entity_resolution
                    WHERE source_id = $1 AND doc_id = $2
                      AND lower(mention_text) = lower($3)
                    ORDER BY resolution_id DESC LIMIT 1
                    """,
                    resolution.source_id,
                    resolution.doc_id,
                    resolution.mention_text,
                )
            await conn.execute(
                """
                INSERT INTO document_entity_link
                    (resolution_id, source_id, doc_id, lei, resolver, score, recorded_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (resolution_id) DO NOTHING
                """,
                resolution_id,
                resolution.source_id,
                resolution.doc_id,
                resolution.lei,
                str(resolution.resolver),
                resolution.score,
                resolution.recorded_at,
            )

    async def resolution_for(
        self, source_id: str, doc_id: str, mention_text: str
    ) -> Resolution | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT r.source_id, r.doc_id, r.mention_text, e.lei,
                   r.resolver, r.score, r.resolved_at
            FROM entity_resolution r
            JOIN entity e USING (entity_id)
            WHERE r.source_id = $1 AND r.doc_id = $2
              AND lower(r.mention_text) = lower($3)
            ORDER BY r.resolution_id DESC
            LIMIT 1
            """,
            source_id,
            doc_id,
            mention_text,
        )
        if row is None:
            return None
        return Resolution(
            source_id=row["source_id"],
            doc_id=row["doc_id"],
            mention_text=row["mention_text"],
            lei=row["lei"],
            resolver=ResolverName(row["resolver"]),
            score=row["score"],
            recorded_at=row["resolved_at"],
        )

    async def record_queued(self, queued: QueuedMention) -> None:
        pool = await self._get_pool()
        candidate_id = None
        if queued.candidate_lei is not None:
            row = await pool.fetchrow(
                "SELECT entity_id FROM entity WHERE lei = $1", queued.candidate_lei
            )
            candidate_id = row["entity_id"] if row else None
        await pool.execute(
            """
            INSERT INTO resolution_queue
                (source_id, doc_id, mention_text, candidate_entity_id, best_score, reason)
            SELECT $1, $2, $3, $4, $5, $6
            WHERE NOT EXISTS (
                SELECT 1 FROM resolution_queue
                WHERE source_id = $1 AND doc_id = $2
                  AND lower(mention_text) = lower($3)
                  AND status = 'pending'
            )
            """,
            queued.source_id,
            queued.doc_id,
            queued.mention_text,
            candidate_id,
            queued.best_score,
            queued.reason,
        )

    async def resolutions(self) -> list[Resolution]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT r.source_id, r.doc_id, r.mention_text, e.lei,
                   r.resolver, r.score, r.resolved_at
            FROM entity_resolution r
            JOIN entity e USING (entity_id)
            ORDER BY r.resolution_id
            """
        )
        return [
            Resolution(
                source_id=row["source_id"],
                doc_id=row["doc_id"],
                mention_text=row["mention_text"],
                lei=row["lei"],
                resolver=ResolverName(row["resolver"]),
                score=row["score"],
                recorded_at=row["resolved_at"],
            )
            for row in rows
        ]

    async def document_entity_links(self) -> list[DocumentEntityLink]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT source_id, doc_id, lei, resolver, score, recorded_at
            FROM document_entity_link
            ORDER BY document_entity_link_id
            """
        )
        return [
            DocumentEntityLink(
                source_id=row["source_id"],
                doc_id=row["doc_id"],
                lei=row["lei"],
                resolver=ResolverName(row["resolver"]),
                score=row["score"],
                recorded_at=row["recorded_at"],
            )
            for row in rows
        ]

    async def queue(self) -> list[QueuedMention]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT q.source_id, q.doc_id, q.mention_text, e.lei AS candidate_lei,
                   q.best_score, q.reason
            FROM resolution_queue q
            LEFT JOIN entity e ON e.entity_id = q.candidate_entity_id
            WHERE q.status = 'pending'
            ORDER BY q.queue_id
            """
        )
        return [
            QueuedMention(
                source_id=row["source_id"],
                doc_id=row["doc_id"],
                mention_text=row["mention_text"],
                candidate_lei=row["candidate_lei"],
                best_score=row["best_score"],
                reason=row["reason"],
            )
            for row in rows
        ]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
