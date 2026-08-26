"""Entitlement-safe corpus stores for local and indexed retrieval.

The in-memory store intentionally retains the deterministic exhaustive path
used by tests and backtests. The Postgres store exposes only bounded indexed
candidate generation; it never loads the entitled corpus into Python.
"""

import json
from datetime import UTC, datetime
from typing import Any

import asyncpg

from fi_intel.ingest.resolve import DocumentEntityLink
from fi_intel.retrieval.chunking import (
    CHUNKER_VERSION,
    EMBEDDING_DIM,
    Chunk,
    Embedder,
    chunk_document,
)
from fi_intel.retrieval.corpus import MAX_INDEXED_CANDIDATES, IndexedCandidate
from fi_intel.retrieval.entitlement import Principal, Side, grants_for
from fi_intel.sources.canonical import CanonicalDocument, DocumentClass


class InMemoryCorpusStore:
    """Test double. Filter logic mirrors the production SQL predicates."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._docs: list[CanonicalDocument] = []
        self._grants: set[tuple[str, str]] = set()
        self._licensed: dict[str, bool] = {}
        self._entity_links: list[DocumentEntityLink] = []

    def register_source(self, source_id: str, licensed: bool = True) -> None:
        self._licensed[source_id] = licensed

    def grant(self, entitlement_group: str, source_id: str) -> None:
        self._grants.add((entitlement_group, source_id))

    def add_documents(self, docs: list[CanonicalDocument]) -> None:
        self._docs.extend(docs)

    def add_entity_links(self, links: list[DocumentEntityLink]) -> None:
        self._entity_links.extend(links)

    async def candidates(
        self,
        principal: Principal,
        as_of: datetime | None,
        entity_lei: str | None = None,
        source_ids: set[str] | None = None,
    ) -> list[tuple[CanonicalDocument, list[Chunk], list[list[float]]]]:
        allowed_sources = grants_for(principal.entitlement_group, self._grants)
        out = []
        for doc in self._docs:
            if doc.source_id not in allowed_sources:
                continue
            if not self._licensed.get(doc.source_id, False):
                continue
            if doc.barrier_side != "public" and principal.side != Side.PRIVATE:
                continue
            if as_of is not None and doc.recorded_at > as_of:
                continue
            if source_ids is not None and doc.source_id not in source_ids:
                continue
            if entity_lei is not None and not any(
                link.source_id == doc.source_id
                and link.doc_id == doc.doc_id
                and link.lei == entity_lei
                and (as_of is None or link.recorded_at <= as_of)
                for link in self._entity_links
            ):
                continue
            chunks = chunk_document(doc)
            embeddings = await self._embedder.embed_batch(
                [chunk.text for chunk in chunks], kind="document"
            )
            out.append((doc, chunks, embeddings))
        return out

    async def resolve_document(
        self,
        principal: Principal,
        source_id: str,
        doc_id: str,
        as_of: datetime | None,
    ) -> CanonicalDocument | None:
        allowed_sources = grants_for(principal.entitlement_group, self._grants)
        for doc in self._docs:
            if doc.source_id != source_id or doc.doc_id != doc_id:
                continue
            if source_id not in allowed_sources or not self._licensed.get(source_id, False):
                return None
            if doc.barrier_side != "public" and principal.side != Side.PRIVATE:
                return None
            if as_of is not None and doc.recorded_at > as_of:
                return None
            return doc
        return None


INDEX_STATE_NAME = "document_chunk"
INDEX_BUILD_LOCK_ID = 750_521_801_934_774_611


class RetrievalIndexError(RuntimeError):
    """Base class for an unavailable or incompatible retrieval index."""


class RetrievalIndexNotReadyError(RetrievalIndexError):
    """The retrieval index has not completed a versioned build."""


class RetrievalIndexVersionError(RetrievalIndexError):
    """The configured embedder is incompatible with the active index."""


_INDEX_STATE_SQL = """
SELECT embed_model_version, embedding_dim, chunker_version, status
FROM retrieval_index_state
WHERE index_name = $1
"""


# Both retrieval legs start from this authorization-filtered relation. The
# NOT MATERIALIZED directive lets Postgres push each leg into its GIN/HNSW
# access path instead of materializing every entitled chunk first.
INDEXED_CANDIDATE_SQL = """
WITH eligible AS NOT MATERIALIZED (
    SELECT c.chunk_id, c.search_vector, c.embedding
    FROM document_chunk c
    JOIN document d
      ON d.source_id = c.source_id AND d.doc_id = c.doc_id
    JOIN source_registry sr ON sr.source_id = d.source_id
    JOIN entitlement_grant eg
      ON eg.source_id = d.source_id AND eg.entitlement_group = $1
    WHERE sr.licensed
      AND (d.barrier_side = 'public' OR $2::text = 'private')
      AND ($3::timestamptz IS NULL OR d.recorded_at <= $3::timestamptz)
      AND (
          $4::text IS NULL OR EXISTS (
              SELECT 1 FROM document_entity_link del
              WHERE del.source_id = d.source_id
                AND del.doc_id = d.doc_id
                AND del.lei = $4::text
                AND ($3::timestamptz IS NULL OR del.recorded_at <= $3::timestamptz)
          )
      )
      AND ($5::text[] IS NULL OR d.source_id = ANY($5::text[]))
      AND c.embed_model_version = $8::text
),
lexical_nearest AS (
    SELECT e.chunk_id,
           ts_rank_cd(
               e.search_vector,
               websearch_to_tsquery('simple', $6::text)
           ) AS lexical_score
    FROM eligible e
    WHERE $10::text IN ('hybrid', 'bm25')
      AND e.search_vector @@ websearch_to_tsquery('simple', $6::text)
    ORDER BY lexical_score DESC, e.chunk_id
    LIMIT $9::int
),
lexical_ranked AS (
    SELECT chunk_id,
           rank() OVER (ORDER BY lexical_score DESC)::int AS bm25_rank,
           lexical_score / max(lexical_score) OVER () AS bm25_score
    FROM lexical_nearest
    WHERE lexical_score > 0.0
),
vector_nearest AS (
    SELECT e.chunk_id,
           1.0 - (e.embedding <=> $7::vector) AS vector_score
    FROM eligible e
    WHERE $10::text IN ('hybrid', 'vector')
      AND e.embedding IS NOT NULL
    ORDER BY e.embedding <=> $7::vector
    LIMIT $9::int
),
vector_candidates AS (
    SELECT chunk_id, vector_score
    FROM vector_nearest
    WHERE vector_score > 0.0
),
vector_ranked AS (
    SELECT chunk_id,
           rank() OVER (ORDER BY vector_score DESC)::int AS vector_rank,
           vector_score
    FROM vector_candidates
),
candidate_ids AS (
    SELECT coalesce(lexical_ranked.chunk_id, vector_ranked.chunk_id) AS chunk_id,
           lexical_ranked.bm25_rank,
           vector_ranked.vector_rank,
           lexical_ranked.bm25_score,
           vector_ranked.vector_score
    FROM lexical_ranked
    FULL OUTER JOIN vector_ranked USING (chunk_id)
)
SELECT d.doc_id, d.source_id, d.published_at, d.recorded_at,
       d.title, d.body, d.language, d.document_class, d.barrier_side,
       d.mentioned_names, d.identifiers, d.url,
       c.chunk_index, c.char_start, c.char_end, c.text,
       candidate_ids.bm25_rank, candidate_ids.vector_rank,
       candidate_ids.bm25_score, candidate_ids.vector_score
FROM candidate_ids
JOIN document_chunk c ON c.chunk_id = candidate_ids.chunk_id
JOIN document d ON d.source_id = c.source_id AND d.doc_id = c.doc_id
ORDER BY least(
             coalesce(candidate_ids.bm25_rank, 2147483647),
             coalesce(candidate_ids.vector_rank, 2147483647)
         ),
         c.chunk_id
"""


def _json_object(value: object) -> dict[str, str]:
    if isinstance(value, str):
        decoded = json.loads(value)
        return {str(key): str(item) for key, item in decoded.items()}
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    msg = f"unexpected JSON object type: {type(value).__name__}"
    raise TypeError(msg)


def _row_to_document(row: Any) -> CanonicalDocument:
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
        mentioned_names=tuple(row["mentioned_names"] or ()),
        identifiers=_json_object(row["identifiers"]),
        url=row["url"],
    )


def _row_to_indexed_candidate(row: Any) -> IndexedCandidate:
    doc = _row_to_document(row)
    return IndexedCandidate(
        doc=doc,
        chunk=Chunk(
            source_id=doc.source_id,
            doc_id=doc.doc_id,
            chunk_index=row["chunk_index"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            text=row["text"],
        ),
        bm25_rank=int(row["bm25_rank"]) if row["bm25_rank"] is not None else None,
        vector_rank=(int(row["vector_rank"]) if row["vector_rank"] is not None else None),
        bm25_score=(float(row["bm25_score"]) if row["bm25_score"] is not None else None),
        vector_score=(
            float(row["vector_score"]) if row["vector_score"] is not None else None
        ),
    )


class PostgresCorpusStore:
    """Production store using bounded GIN/HNSW candidate generation."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    @staticmethod
    def _validate_index_state(
        row: Any,
        embed_model_version: str,
        embedding_dim: int,
    ) -> None:
        if row is None:
            msg = "retrieval index has no completed version; run `fi-intel index run`"
            raise RetrievalIndexNotReadyError(msg)
        if row["status"] != "ready":
            msg = f"retrieval index is {row['status']!r}, not ready"
            raise RetrievalIndexNotReadyError(msg)
        actual = (
            str(row["embed_model_version"]),
            int(row["embedding_dim"]),
            str(row["chunker_version"]),
        )
        expected = (embed_model_version, embedding_dim, CHUNKER_VERSION)
        if actual != expected:
            msg = (
                "retrieval index version mismatch: "
                f"active={actual!r}, configured={expected!r}; run `fi-intel index reembed`"
            )
            raise RetrievalIndexVersionError(msg)

    async def indexed_candidates(
        self,
        query: str,
        query_embedding: list[float],
        *,
        embed_model_version: str,
        embedding_dim: int,
        principal: Principal,
        as_of: datetime | None,
        entity_lei: str | None,
        source_ids: set[str] | None,
        mode: str,
        candidate_limit: int,
    ) -> list[IndexedCandidate]:
        if mode not in {"hybrid", "bm25", "vector"}:
            msg = f"unknown search mode {mode!r}"
            raise ValueError(msg)
        if not 1 <= candidate_limit <= MAX_INDEXED_CANDIDATES:
            msg = f"candidate_limit must be in [1, {MAX_INDEXED_CANDIDATES}]"
            raise ValueError(msg)
        if len(query_embedding) != embedding_dim:
            msg = (
                f"query embedding dimension {len(query_embedding)} does not match "
                f"configured dimension {embedding_dim}"
            )
            raise RetrievalIndexVersionError(msg)

        pool = await self._get_pool()
        async with (
            pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            state = await conn.fetchrow(_INDEX_STATE_SQL, INDEX_STATE_NAME)
            self._validate_index_state(state, embed_model_version, embedding_dim)
            rows = await conn.fetch(
                INDEXED_CANDIDATE_SQL,
                principal.entitlement_group,
                principal.side.value,
                as_of,
                entity_lei,
                sorted(source_ids) if source_ids is not None else None,
                query,
                str(query_embedding),
                embed_model_version,
                candidate_limit,
                mode,
            )
        return [_row_to_indexed_candidate(row) for row in rows]

    async def resolve_document(
        self,
        principal: Principal,
        source_id: str,
        doc_id: str,
        as_of: datetime | None,
    ) -> CanonicalDocument | None:
        """Resolve one document after licence, grant, barrier, and as-of checks."""
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT d.doc_id, d.source_id, d.published_at, d.recorded_at,
                   d.title, d.body, d.language, d.document_class,
                   d.barrier_side, d.mentioned_names, d.identifiers, d.url
            FROM document d
            JOIN source_registry sr ON sr.source_id = d.source_id
            JOIN entitlement_grant eg
              ON eg.source_id = d.source_id AND eg.entitlement_group = $1
            WHERE d.source_id = $2 AND d.doc_id = $3
              AND sr.licensed
              AND (d.barrier_side = 'public' OR $4::text = 'private')
              AND ($5::timestamptz IS NULL OR d.recorded_at <= $5::timestamptz)
            """,
            principal.entitlement_group,
            source_id,
            doc_id,
            principal.side.value,
            as_of,
        )
        return _row_to_document(row) if row is not None else None

    async def _validate_build_request(
        self,
        conn: asyncpg.Connection,
        embedder: Embedder,
        force: bool,
    ) -> None:
        if embedder.dim != EMBEDDING_DIM:
            msg = (
                f"embedder dimension {embedder.dim} is incompatible with schema "
                f"vector({EMBEDDING_DIM})"
            )
            raise RetrievalIndexVersionError(msg)

        state = await conn.fetchrow(_INDEX_STATE_SQL, INDEX_STATE_NAME)
        if not force:
            if state is not None:
                self._validate_index_state(state, embedder.model_version, embedder.dim)
            elif await conn.fetchval("SELECT EXISTS (SELECT 1 FROM document_chunk)"):
                msg = (
                    "unversioned legacy chunks exist; run `fi-intel index reembed` "
                    "to establish a compatible index version"
                )
                raise RetrievalIndexNotReadyError(msg)

    @staticmethod
    async def _write_document_chunks(
        conn: asyncpg.Connection,
        row: Any,
        embedder: Embedder,
    ) -> int:
        now = datetime.now(tz=UTC)
        doc = CanonicalDocument(
            doc_id=row["doc_id"],
            source_id=row["source_id"],
            published_at=now,
            recorded_at=now,
            title=row["title"],
            body=row["body"],
            document_class=DocumentClass.NEWS_WIRE,
        )
        chunks = chunk_document(doc)
        if not chunks:
            return 0
        embeddings = await embedder.embed_batch([chunk.text for chunk in chunks], kind="document")
        if len(embeddings) != len(chunks):
            msg = f"embedder returned {len(embeddings)} vectors for {len(chunks)} chunks"
            raise RetrievalIndexVersionError(msg)

        written = 0
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            if len(embedding) != embedder.dim:
                msg = (
                    f"document embedding dimension {len(embedding)} does not "
                    f"match configured dimension {embedder.dim}"
                )
                raise RetrievalIndexVersionError(msg)
            status = await conn.execute(
                """
                INSERT INTO document_chunk
                    (source_id, doc_id, chunk_index, char_start, char_end,
                     text, embedding, embed_model_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7::vector, $8)
                ON CONFLICT (source_id, doc_id, chunk_index) DO NOTHING
                """,
                chunk.source_id,
                chunk.doc_id,
                chunk.chunk_index,
                chunk.char_start,
                chunk.char_end,
                chunk.text,
                str(embedding),
                embedder.model_version,
            )
            if status.endswith(" 1"):
                written += 1
        return written

    async def index_chunks(self, embedder: Embedder, *, force: bool = False) -> int:
        """Build a version-homogeneous chunk index.

        Incremental indexing is allowed only when the active model, vector
        dimension, and chunker version exactly match. A forced rebuild deletes
        and replaces chunks in the same transaction, so an embedding failure
        cannot leave the production index empty.
        """
        pool = await self._get_pool()
        written = 0
        async with pool.acquire() as conn, conn.transaction():
            # Version validation and all writes are protected by the same
            # transaction-scoped lock. Concurrent builders cannot validate
            # against one version and then commit rows for another.
            await conn.execute("SELECT pg_advisory_xact_lock($1)", INDEX_BUILD_LOCK_ID)
            await self._validate_build_request(conn, embedder, force)
            rows = await conn.fetch(
                """
                SELECT d.source_id, d.doc_id, d.title, d.body
                FROM document d
                WHERE $1 OR NOT EXISTS (
                    SELECT 1 FROM document_chunk c
                    WHERE c.source_id = d.source_id AND c.doc_id = d.doc_id
                )
                ORDER BY d.source_id, d.doc_id
                """,
                force,
            )
            if force:
                await conn.execute("DELETE FROM document_chunk")
            for row in rows:
                written += await self._write_document_chunks(conn, row, embedder)
            await conn.execute(
                """
                INSERT INTO retrieval_index_state
                    (index_name, embed_model_version, embedding_dim,
                     chunker_version, status, indexed_at)
                VALUES ($1, $2, $3, $4, 'ready', now())
                ON CONFLICT (index_name) DO UPDATE SET
                    embed_model_version = EXCLUDED.embed_model_version,
                    embedding_dim = EXCLUDED.embedding_dim,
                    chunker_version = EXCLUDED.chunker_version,
                    status = EXCLUDED.status,
                    indexed_at = EXCLUDED.indexed_at
                """,
                INDEX_STATE_NAME,
                embedder.model_version,
                embedder.dim,
                CHUNKER_VERSION,
            )
        return written

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
