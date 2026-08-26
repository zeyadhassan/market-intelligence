"""Document persistence.

The store is a protocol with two implementations:

- PostgresDocumentStore — production, asyncpg, idempotent upserts.
- InMemoryDocumentStore — tests. Unit tests stay service-free; the
  Postgres implementation is exercised by the same contract test when
  FI_INTEL_TEST_PG_DSN is set.

Cursor persistence is atomic with the document batch that produced it:
``commit_batch`` writes documents, duplicate links, and the cursor in one
transaction. A crash between batches can therefore never leave the cursor
ahead of the data (gap) or behind it (duplicate work).
"""

import json
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

import asyncpg
from pydantic import BaseModel, ConfigDict

from fi_intel.ingest.dedupe import DuplicateVerdict
from fi_intel.sources.base import FetchCursor
from fi_intel.sources.canonical import CanonicalDocument


class SourceStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    document_count: int
    duplicate_count: int
    cursor_position: str | None
    cursor_updated_at: datetime | None


@runtime_checkable
class DocumentStore(Protocol):
    """Persistence contract for the ingestion pipeline."""

    async def commit_batch(
        self,
        docs: list[CanonicalDocument],
        duplicates: list[DuplicateVerdict],
        cursor: FetchCursor,
    ) -> None:
        """Atomically persist a batch and the cursor that resumes after it.

        Must be idempotent: committing the same batch twice changes nothing.
        """
        ...

    async def load_cursor(self, source_id: str) -> FetchCursor | None: ...

    async def load_documents(self, source_id: str) -> list[CanonicalDocument]:
        """All persisted canonical documents for a source."""
        ...

    async def load_recent_documents(
        self, source_id: str, *, window_days: int
    ) -> list[CanonicalDocument]:
        """Documents in a bounded window ending at the source's latest document."""
        ...

    async def load_document_hashes(self, source_id: str) -> set[str]:
        """All exact-dedupe hashes without document bodies or shingle sets."""
        ...

    async def load_document(self, source_id: str, doc_id: str) -> CanonicalDocument | None:
        """Load one citation target without scanning the source corpus."""
        ...

    async def status(self) -> list[SourceStatus]: ...

    async def close(self) -> None: ...


class InMemoryDocumentStore:
    """Reference implementation used by unit tests."""

    def __init__(self) -> None:
        self._docs: dict[tuple[str, str], CanonicalDocument] = {}
        self._dupes: dict[tuple[str, str], DuplicateVerdict] = {}
        self._cursors: dict[str, FetchCursor] = {}

    async def commit_batch(
        self,
        docs: list[CanonicalDocument],
        duplicates: list[DuplicateVerdict],
        cursor: FetchCursor,
    ) -> None:
        for doc in docs:
            self._docs.setdefault((doc.source_id, doc.doc_id), doc)
        for verdict in duplicates:
            self._dupes.setdefault((verdict.doc.source_id, verdict.doc.doc_id), verdict)
        self._cursors[cursor.source_id] = cursor

    async def load_cursor(self, source_id: str) -> FetchCursor | None:
        return self._cursors.get(source_id)

    async def load_documents(self, source_id: str) -> list[CanonicalDocument]:
        return [d for (sid, _), d in self._docs.items() if sid == source_id]

    async def load_recent_documents(
        self, source_id: str, *, window_days: int
    ) -> list[CanonicalDocument]:
        if window_days < 1:
            raise ValueError("window_days must be >= 1")
        documents = await self.load_documents(source_id)
        if not documents:
            return []
        latest = max(document.published_at for document in documents)
        earliest = latest - timedelta(days=window_days)
        return [document for document in documents if document.published_at >= earliest]

    async def load_document_hashes(self, source_id: str) -> set[str]:
        return {
            document.content_hash()
            for (stored_source_id, _), document in self._docs.items()
            if stored_source_id == source_id
        }

    async def load_document(self, source_id: str, doc_id: str) -> CanonicalDocument | None:
        return self._docs.get((source_id, doc_id))

    async def status(self) -> list[SourceStatus]:
        source_ids = {sid for sid, _ in self._docs} | set(self._cursors)
        return [
            SourceStatus(
                source_id=sid,
                document_count=sum(1 for s, _ in self._docs if s == sid),
                duplicate_count=sum(1 for s, _ in self._dupes if s == sid),
                cursor_position=(self._cursors[sid].position if sid in self._cursors else None),
                cursor_updated_at=(self._cursors[sid].updated_at if sid in self._cursors else None),
            )
            for sid in sorted(source_ids)
        ]

    async def close(self) -> None:
        return None


_UPSERT_DOCUMENT = """
INSERT INTO document (
    source_id, doc_id, content_hash, title, body, language, document_class,
    barrier_side, published_at, recorded_at, url, mentioned_names, identifiers, metadata
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14::jsonb)
ON CONFLICT (source_id, doc_id) DO NOTHING
"""

# The duplicate row itself is inserted without an FK to `document` (the
# duplicate is classified, not persisted); only the canonical side is
# enforced. See deploy/init.sql for the rationale.
_INSERT_DUPLICATE = """
INSERT INTO document_duplicate (
    source_id, doc_id, canonical_source_id, canonical_doc_id, similarity, detector
) VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (source_id, doc_id) DO NOTHING
"""

_UPSERT_CURSOR = """
INSERT INTO ingest_cursor (source_id, position, updated_at)
VALUES ($1, $2, $3)
ON CONFLICT (source_id) DO UPDATE SET position = EXCLUDED.position,
                                      updated_at = EXCLUDED.updated_at
"""


class PostgresDocumentStore:
    """asyncpg-backed store. All writes idempotent; batch + cursor atomic."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def commit_batch(
        self,
        docs: list[CanonicalDocument],
        duplicates: list[DuplicateVerdict],
        cursor: FetchCursor,
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for doc in docs:
                await conn.execute(
                    _UPSERT_DOCUMENT,
                    doc.source_id,
                    doc.doc_id,
                    doc.content_hash(),
                    doc.title,
                    doc.body,
                    doc.language,
                    str(doc.document_class),
                    str(doc.barrier_side),
                    doc.published_at,
                    doc.recorded_at,
                    doc.url,
                    list(doc.mentioned_names),
                    json.dumps(doc.identifiers),
                    json.dumps(doc.metadata),
                )
            for verdict in duplicates:
                await conn.execute(
                    _INSERT_DUPLICATE,
                    verdict.doc.source_id,
                    verdict.doc.doc_id,
                    verdict.canonical.source_id,
                    verdict.canonical.doc_id,
                    verdict.similarity,
                    "shingle_jaccard",
                )
            await conn.execute(_UPSERT_CURSOR, cursor.source_id, cursor.position, cursor.updated_at)

    async def load_cursor(self, source_id: str) -> FetchCursor | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT position, updated_at FROM ingest_cursor WHERE source_id = $1",
            source_id,
        )
        if row is None:
            return None
        return FetchCursor(
            source_id=source_id, position=row["position"], updated_at=row["updated_at"]
        )

    async def load_documents(self, source_id: str) -> list[CanonicalDocument]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM document WHERE source_id = $1 ORDER BY published_at",
            source_id,
        )
        return [self._document_from_row(row) for row in rows]

    async def load_recent_documents(
        self, source_id: str, *, window_days: int
    ) -> list[CanonicalDocument]:
        if window_days < 1:
            raise ValueError("window_days must be >= 1")
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT * FROM document
            WHERE source_id = $1
              AND published_at >= (
                  SELECT max(published_at) - make_interval(days => $2)
                  FROM document WHERE source_id = $1
              )
            ORDER BY published_at
            """,
            source_id,
            window_days,
        )
        return [self._document_from_row(row) for row in rows]

    async def load_document_hashes(self, source_id: str) -> set[str]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT content_hash FROM document WHERE source_id = $1",
            source_id,
        )
        return {str(row["content_hash"]) for row in rows}

    async def load_document(self, source_id: str, doc_id: str) -> CanonicalDocument | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM document WHERE source_id = $1 AND doc_id = $2",
            source_id,
            doc_id,
        )
        return None if row is None else self._document_from_row(row)

    @staticmethod
    def _document_from_row(row: asyncpg.Record) -> CanonicalDocument:
        return CanonicalDocument(
            doc_id=row["doc_id"],
            source_id=row["source_id"],
            published_at=row["published_at"],
            recorded_at=row["recorded_at"],
            title=row["title"],
            body=row["body"],
            language=row["language"],
            document_class=row["document_class"],
            barrier_side=row["barrier_side"],
            mentioned_names=tuple(row["mentioned_names"]),
            identifiers=json.loads(row["identifiers"]),
            url=row["url"],
            metadata=json.loads(row["metadata"]),
        )

    async def status(self) -> list[SourceStatus]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT s.source_id,
                   COALESCE(d.n, 0) AS document_count,
                   COALESCE(dup.n, 0) AS duplicate_count,
                   c.position AS cursor_position,
                   c.updated_at AS cursor_updated_at
            FROM source_registry s
            LEFT JOIN (
                SELECT source_id, COUNT(*) AS n FROM document GROUP BY source_id
            ) d USING (source_id)
            LEFT JOIN (
                SELECT source_id, COUNT(*) AS n FROM document_duplicate GROUP BY source_id
            ) dup USING (source_id)
            LEFT JOIN ingest_cursor c USING (source_id)
            ORDER BY s.source_id
            """
        )
        return [
            SourceStatus(
                source_id=row["source_id"],
                document_count=row["document_count"],
                duplicate_count=row["duplicate_count"],
                cursor_position=row["cursor_position"],
                cursor_updated_at=row["cursor_updated_at"],
            )
            for row in rows
        ]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
