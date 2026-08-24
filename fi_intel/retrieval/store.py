"""Corpus stores: candidate generation with entitlement applied at the source.

PostgresCorpusStore executes ENTITLEMENT_SQL verbatim — filtering happens
in the database, in the same query that fetches candidates. There is no
method on this class that returns unfiltered documents.

InMemoryCorpusStore is the test double. Its filtering is a faithful port
of the same predicate (see entitlement.py); the parity test keeps them
honest once a live database is available.
"""

import json
from datetime import UTC, datetime

import asyncpg

from fi_intel.retrieval.chunking import Chunk, Embedder, chunk_document
from fi_intel.retrieval.entitlement import (
    AS_OF_SQL,
    ENTITLEMENT_SQL,
    Principal,
    Side,
    grants_for,
)
from fi_intel.sources.canonical import CanonicalDocument, DocumentClass


class InMemoryCorpusStore:
    """Test double. Filter logic mirrors ENTITLEMENT_SQL exactly."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._docs: list[CanonicalDocument] = []
        # (entitlement_group, source_id) grant rows, mirroring entitlement_grant.
        self._grants: set[tuple[str, str]] = set()
        self._licensed: dict[str, bool] = {}

    def register_source(self, source_id: str, licensed: bool = True) -> None:
        self._licensed[source_id] = licensed

    def grant(self, entitlement_group: str, source_id: str) -> None:
        self._grants.add((entitlement_group, source_id))

    def add_documents(self, docs: list[CanonicalDocument]) -> None:
        self._docs.extend(docs)

    async def candidates(
        self, principal: Principal, as_of: datetime | None
    ) -> list[tuple[CanonicalDocument, list[Chunk], list[list[float]]]]:
        allowed_sources = grants_for(principal.entitlement_group, self._grants)
        out = []
        for doc in self._docs:
            # --- port of ENTITLEMENT_SQL: keep in lockstep ---
            if doc.source_id not in allowed_sources:
                continue
            if not self._licensed.get(doc.source_id, False):
                continue
            if doc.barrier_side != "public" and principal.side != Side.PRIVATE:
                continue
            # --- port of AS_OF_SQL ---
            if as_of is not None and doc.recorded_at > as_of:
                continue
            chunks = chunk_document(doc)
            embeddings = [self._embedder.embed(c.text) for c in chunks]
            out.append((doc, chunks, embeddings))
        return out


# The {as_of} slot is filled with the constant AS_OF_SQL fragment or ""
# — never with user input. All user-supplied values bind as parameters.
_CANDIDATE_SQL = f"""
SELECT d.* FROM document d
{ENTITLEMENT_SQL}
{{as_of}}
ORDER BY d.published_at
"""  # noqa: S608

_CHUNKS_SQL = """
SELECT chunk_index, char_start, char_end, text, embedding
FROM document_chunk
WHERE source_id = $1 AND doc_id = $2
ORDER BY chunk_index
"""


def _parse_embedding(value: object) -> list[float]:
    """asyncpg returns vector columns as '[0.1,0.2,...]' strings unless the
    pgvector codec is registered; parse defensively."""
    if isinstance(value, str):
        return [float(x) for x in value.strip("[]").split(",") if x]
    if isinstance(value, (list, tuple)):
        return [float(x) for x in value]
    msg = f"unexpected embedding value type: {type(value).__name__}"
    raise TypeError(msg)


class PostgresCorpusStore:
    """Production store. Entitlement and as-of are SQL predicates."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def candidates(
        self, principal: Principal, as_of: datetime | None
    ) -> list[tuple[CanonicalDocument, list[Chunk], list[list[float]]]]:
        pool = await self._get_pool()
        sql = _CANDIDATE_SQL.format(as_of=AS_OF_SQL if as_of is not None else "")
        # asyncpg uses $n placeholders; the entitlement fragment uses named
        # placeholders for readability — translate here, once.
        sql = sql.replace("%(group)s", "$1").replace("%(side)s", "$2")
        args: list[object] = [principal.entitlement_group, str(principal.side)]
        if as_of is not None:
            sql = sql.replace("%(as_of)s", "$3")
            args.append(as_of)

        rows = await pool.fetch(sql, *args)
        out = []
        for row in rows:
            doc = CanonicalDocument(
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
            )
            chunk_rows = await pool.fetch(_CHUNKS_SQL, doc.source_id, doc.doc_id)
            chunks = [
                Chunk(
                    source_id=doc.source_id,
                    doc_id=doc.doc_id,
                    chunk_index=r["chunk_index"],
                    char_start=r["char_start"],
                    char_end=r["char_end"],
                    text=r["text"],
                )
                for r in chunk_rows
            ]
            embeddings = [_parse_embedding(r["embedding"]) for r in chunk_rows]
            out.append((doc, chunks, embeddings))
        return out

    async def index_chunks(self, embedder: Embedder) -> int:
        """Populate document_chunk for documents that have no chunks yet.

        Returns the number of chunks written. Chunking/embedding is a
        pipeline concern; keeping it here lets the CLI index on demand
        without a separate ingestion stage for the demo path.
        """
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT d.source_id, d.doc_id, d.title, d.body
            FROM document d
            WHERE NOT EXISTS (
                SELECT 1 FROM document_chunk c
                WHERE c.source_id = d.source_id AND c.doc_id = d.doc_id
            )
            """
        )
        written = 0
        async with pool.acquire() as conn, conn.transaction():
            for row in rows:
                doc = CanonicalDocument(
                    doc_id=row["doc_id"],
                    source_id=row["source_id"],
                    published_at=datetime.now(tz=UTC),
                    recorded_at=datetime.now(tz=UTC),
                    title=row["title"],
                    body=row["body"],
                    document_class=DocumentClass.NEWS_WIRE,
                )
                for chunk in chunk_document(doc):
                    embedding = embedder.embed(chunk.text)
                    await conn.execute(
                        """
                        INSERT INTO document_chunk
                            (source_id, doc_id, chunk_index, char_start, char_end, text, embedding)
                        VALUES ($1, $2, $3, $4, $5, $6, $7::vector)
                        ON CONFLICT (source_id, doc_id, chunk_index) DO NOTHING
                        """,
                        chunk.source_id,
                        chunk.doc_id,
                        chunk.chunk_index,
                        chunk.char_start,
                        chunk.char_end,
                        chunk.text,
                        str(embedding),
                    )
                    written += 1
        return written

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
