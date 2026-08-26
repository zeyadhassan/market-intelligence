"""Service-free contracts for bounded indexed Postgres retrieval."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import asyncpg
import pytest

from fi_intel.retrieval.chunking import (
    CHUNKER_VERSION,
    EMBEDDING_DIM,
    Chunk,
    HashingEmbedder,
    chunk_document,
)
from fi_intel.retrieval.corpus import CorpusSearch, IndexedCandidate
from fi_intel.retrieval.entitlement import Principal, Side
from fi_intel.retrieval.store import (
    INDEXED_CANDIDATE_SQL,
    PostgresCorpusStore,
    RetrievalIndexNotReadyError,
    RetrievalIndexVersionError,
)
from fi_intel.sources.canonical import CanonicalDocument, DocumentClass

PRINCIPAL = Principal(
    principal_id="query-contract",
    entitlement_group="desk-public",
    side=Side.PUBLIC,
)
AS_OF = datetime(2024, 6, 1, tzinfo=UTC)
PG_DSN = os.environ.get("FI_INTEL_TEST_PG_DSN")


def _state(model: str = "hashing-v1") -> dict[str, object]:
    return {
        "embed_model_version": model,
        "embedding_dim": EMBEDDING_DIM,
        "chunker_version": CHUNKER_VERSION,
        "status": "ready",
    }


def _row() -> dict[str, object]:
    return {
        "doc_id": "doc-1",
        "source_id": "wire-a",
        "published_at": datetime(2024, 5, 1, tzinfo=UTC),
        "recorded_at": datetime(2024, 5, 1, 1, tzinfo=UTC),
        "title": "Capital plan",
        "body": "The bank approved a capital issuance programme.",
        "language": "en",
        "document_class": "news_wire",
        "barrier_side": "public",
        "mentioned_names": ["Example Bank"],
        "identifiers": '{"lei": "LEI-1"}',
        "url": "https://example.test/doc-1",
        "chunk_index": 0,
        "char_start": 0,
        "char_end": 20,
        "text": "Capital issuance programme",
        "bm25_rank": 1,
        "vector_rank": 2,
        "bm25_score": 1.0,
        "vector_score": 0.7,
    }


class FakePool:
    def __init__(
        self,
        state: dict[str, object] | None,
        rows: list[dict[str, object]],
    ) -> None:
        self.state = state
        self.rows = rows
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.transaction_options: list[dict[str, object]] = []

    def acquire(self) -> "FakePool":
        return self

    def transaction(self, **kwargs: object) -> "FakePool":
        self.transaction_options.append(kwargs)
        return self

    async def __aenter__(self) -> "FakePool":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self.fetchrow_calls.append((sql, args))
        return self.state

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.fetch_calls.append((sql, args))
        return self.rows


def test_sql_contract_filters_before_bounded_lexical_and_vector_generation() -> None:
    sql = " ".join(INDEXED_CANDIDATE_SQL.lower().split())

    assert "join entitlement_grant" in sql
    assert "sr.licensed" in sql
    assert "d.barrier_side = 'public'" in sql
    assert "d.recorded_at <= $3::timestamptz" in sql
    assert "from document_entity_link del" in sql
    assert "del.lei = $4::text" in sql
    assert "del.recorded_at <= $3::timestamptz" in sql
    assert "d.source_id = any($5::text[])" in sql
    assert "c.embed_model_version = $8::text" in sql
    assert "e.search_vector @@ websearch_to_tsquery" in sql
    assert "e.embedding <=> $7::vector" in sql
    assert sql.count("limit $9::int") == 2
    assert "select d.*" not in sql


def test_migration_adds_version_metadata_before_version_index() -> None:
    migration = (
        Path(__file__).parents[1] / "deploy" / "migrations" / "0003_indexed_hybrid_retrieval.sql"
    ).read_text(encoding="utf-8")

    version_column = migration.index("ADD COLUMN IF NOT EXISTS embed_model_version")
    version_index = migration.index("CREATE INDEX IF NOT EXISTS document_chunk_embed_version_idx")
    assert version_column < version_index
    assert "CREATE TABLE IF NOT EXISTS retrieval_index_state" in migration


async def test_postgres_store_uses_one_bounded_candidate_fetch_with_bound_parameters() -> None:
    pool = FakePool(_state(), [_row()])
    store = PostgresCorpusStore("unused")
    store._pool = cast(asyncpg.Pool, pool)  # noqa: SLF001

    candidates = await store.indexed_candidates(
        "capital programme",
        [0.0] * EMBEDDING_DIM,
        embed_model_version="hashing-v1",
        embedding_dim=EMBEDDING_DIM,
        principal=PRINCIPAL,
        as_of=AS_OF,
        entity_lei="LEI-1",
        source_ids={"wire-b", "wire-a"},
        mode="hybrid",
        candidate_limit=50,
    )

    assert len(pool.fetchrow_calls) == 1
    assert len(pool.fetch_calls) == 1
    assert pool.transaction_options == [{"isolation": "repeatable_read", "readonly": True}]
    sql, args = pool.fetch_calls[0]
    assert sql == INDEXED_CANDIDATE_SQL
    assert args[0:6] == (
        PRINCIPAL.entitlement_group,
        "public",
        AS_OF,
        "LEI-1",
        ["wire-a", "wire-b"],
        "capital programme",
    )
    assert args[7:] == ("hashing-v1", 50, "hybrid")
    assert len(candidates) == 1
    assert candidates[0].doc.identifiers == {"lei": "LEI-1"}
    assert candidates[0].bm25_rank == 1
    assert candidates[0].vector_rank == 2


async def test_index_version_mismatch_fails_before_candidate_query() -> None:
    pool = FakePool(_state("old-model"), [_row()])
    store = PostgresCorpusStore("unused")
    store._pool = cast(asyncpg.Pool, pool)  # noqa: SLF001

    with pytest.raises(RetrievalIndexVersionError, match="reembed"):
        await store.indexed_candidates(
            "capital programme",
            [0.0] * EMBEDDING_DIM,
            embed_model_version="hashing-v1",
            embedding_dim=EMBEDDING_DIM,
            principal=PRINCIPAL,
            as_of=None,
            entity_lei=None,
            source_ids=None,
            mode="hybrid",
            candidate_limit=50,
        )

    assert pool.fetch_calls == []


async def test_missing_index_state_fails_closed_before_candidate_query() -> None:
    pool = FakePool(None, [_row()])
    store = PostgresCorpusStore("unused")
    store._pool = cast(asyncpg.Pool, pool)  # noqa: SLF001

    with pytest.raises(RetrievalIndexNotReadyError, match="no completed version"):
        await store.indexed_candidates(
            "capital programme",
            [0.0] * EMBEDDING_DIM,
            embed_model_version="hashing-v1",
            embedding_dim=EMBEDDING_DIM,
            principal=PRINCIPAL,
            as_of=None,
            entity_lei=None,
            source_ids=None,
            mode="hybrid",
            candidate_limit=50,
        )

    assert pool.fetch_calls == []


def _candidate(doc_id: str, bm25_rank: int | None, vector_rank: int | None) -> IndexedCandidate:
    doc = CanonicalDocument(
        doc_id=doc_id,
        source_id="wire-a",
        published_at=datetime(2024, 5, 1, tzinfo=UTC),
        recorded_at=datetime(2024, 5, 1, 1, tzinfo=UTC),
        title=f"Title {doc_id}",
        body="Capital issuance programme.",
        document_class=DocumentClass.NEWS_WIRE,
        identifiers={"lei": "LEI-1"},
    )
    return IndexedCandidate(
        doc=doc,
        chunk=Chunk(
            source_id=doc.source_id,
            doc_id=doc.doc_id,
            chunk_index=0,
            char_start=0,
            char_end=10,
            text="Capital issuance programme",
        ),
        bm25_rank=bm25_rank,
        vector_rank=vector_rank,
        bm25_score=1.0 if bm25_rank is not None else None,
        vector_score=0.7 if vector_rank is not None else None,
    )


class FakeIndexedStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def indexed_candidates(self, query: str, query_embedding: list[float], **kwargs: Any):
        self.calls.append({"query": query, "embedding": query_embedding, **kwargs})
        return [_candidate("both", 2, 1), _candidate("lexical", 1, None)]


async def test_corpus_search_selects_indexed_path_and_preserves_rrf_contract() -> None:
    store = FakeIndexedStore()
    search = CorpusSearch(store, HashingEmbedder())

    results = await search.search(
        "capital programme",
        PRINCIPAL,
        as_of=AS_OF,
        entity_lei="LEI-1",
        source_ids={"wire-a"},
        mode="hybrid",
        limit=10,
    )

    assert [result.doc.doc_id for result in results] == ["both", "lexical"]
    assert results[0].score > results[1].score
    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["embed_model_version"] == "hashing-v1"
    assert call["embedding_dim"] == EMBEDDING_DIM
    assert call["candidate_limit"] == 50
    assert call["entity_lei"] == "LEI-1"
    assert call["source_ids"] == {"wire-a"}


def test_removing_a_retrieval_leg_changes_rank_but_not_floor_eligibility() -> None:
    search = CorpusSearch(object(), HashingEmbedder())  # type: ignore[arg-type]
    both = _candidate("both", 5, 1).model_copy(
        update={"bm25_score": 0.5, "vector_score": 0.5}
    )
    lexical = _candidate("lexical", 1, None)

    fused = search._rank_indexed([both, lexical], AS_OF, 10, "hybrid")  # noqa: SLF001
    without_vector = search._rank_indexed(  # noqa: SLF001
        [both.model_copy(update={"vector_rank": None, "vector_score": None}), lexical],
        AS_OF,
        10,
        "hybrid",
    )

    assert [item.doc.doc_id for item in fused] == ["both", "lexical"]
    assert [item.doc.doc_id for item in without_vector] == ["lexical", "both"]
    assert {item.doc.doc_id for item in fused} == {
        item.doc.doc_id for item in without_vector
    }


@pytest.mark.skipif(PG_DSN is None, reason="FI_INTEL_TEST_PG_DSN not set")
async def test_indexed_query_executes_against_postgres_with_all_optional_filters() -> None:
    assert PG_DSN is not None
    source_id = "indexed_retrieval_contract"
    entitlement_group = "indexed_retrieval_contract"
    embedder = HashingEmbedder()
    store = PostgresCorpusStore(PG_DSN)
    pool = await store._get_pool()  # noqa: SLF001
    previous_state = await pool.fetchrow(
        "SELECT * FROM retrieval_index_state WHERE index_name = 'document_chunk'"
    )
    docs = [
        CanonicalDocument(
            doc_id="eligible",
            source_id=source_id,
            published_at=datetime(2024, 5, 1, tzinfo=UTC),
            recorded_at=datetime(2024, 5, 1, 1, tzinfo=UTC),
            title="Board approval",
            body="The bank approved a capital issuance programme.",
            document_class=DocumentClass.NEWS_WIRE,
            identifiers={"lei": "LEI-1"},
        ),
        CanonicalDocument(
            doc_id="wrong-entity",
            source_id=source_id,
            published_at=datetime(2024, 5, 2, tzinfo=UTC),
            recorded_at=datetime(2024, 5, 2, 1, tzinfo=UTC),
            title="Board approval",
            body="Another bank approved a capital issuance programme.",
            document_class=DocumentClass.NEWS_WIRE,
            identifiers={"lei": "LEI-2"},
        ),
        CanonicalDocument(
            doc_id="future",
            source_id=source_id,
            published_at=datetime(2024, 7, 1, tzinfo=UTC),
            recorded_at=datetime(2024, 7, 1, 1, tzinfo=UTC),
            title="Board approval",
            body="The bank approved a later capital issuance programme.",
            document_class=DocumentClass.NEWS_WIRE,
            identifiers={"lei": "LEI-1"},
        ),
    ]
    try:
        await pool.execute(
            """
            INSERT INTO source_registry
                (source_id, display_name, licence_group, barrier_side, licensed)
            VALUES ($1, 'Indexed retrieval contract', $2, 'public', TRUE)
            ON CONFLICT (source_id) DO UPDATE SET licensed = TRUE
            """,
            source_id,
            entitlement_group,
        )
        await pool.execute(
            """
            INSERT INTO entitlement_grant (entitlement_group, source_id)
            VALUES ($1, $2) ON CONFLICT DO NOTHING
            """,
            entitlement_group,
            source_id,
        )
        for index, doc in enumerate(docs):
            await pool.execute(
                """
                INSERT INTO document
                    (doc_id, source_id, content_hash, title, body, language,
                     document_class, barrier_side, published_at, recorded_at,
                     mentioned_names, identifiers)
                VALUES ($1, $2, $3, $4, $5, 'en', $6, 'public', $7, $8, '{}', $9::jsonb)
                ON CONFLICT (source_id, doc_id) DO NOTHING
                """,
                doc.doc_id,
                source_id,
                f"indexed-contract-{index}",
                doc.title,
                doc.body,
                str(doc.document_class),
                doc.published_at,
                doc.recorded_at,
                json.dumps(doc.identifiers),
            )
            chunks = chunk_document(doc)
            embeddings = await embedder.embed_batch(
                [chunk.text for chunk in chunks], kind="document"
            )
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                await pool.execute(
                    """
                    INSERT INTO document_chunk
                        (source_id, doc_id, chunk_index, char_start, char_end,
                         text, embedding, embed_model_version)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::vector, $8)
                    ON CONFLICT (source_id, doc_id, chunk_index) DO UPDATE SET
                        text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding,
                        embed_model_version = EXCLUDED.embed_model_version
                    """,
                    source_id,
                    doc.doc_id,
                    chunk.chunk_index,
                    chunk.char_start,
                    chunk.char_end,
                    chunk.text,
                    str(embedding),
                    embedder.model_version,
                )
        for lei in {"LEI-1", "LEI-2"}:
            await pool.execute(
                """
                INSERT INTO entity (lei, canonical_name)
                VALUES ($1, $1) ON CONFLICT (lei) DO NOTHING
                """,
                lei,
            )
        for doc in docs:
            entity_id = await pool.fetchval(
                "SELECT entity_id FROM entity WHERE lei = $1",
                doc.identifiers["lei"],
            )
            resolution_id = await pool.fetchval(
                """
                INSERT INTO entity_resolution
                    (entity_id, source_id, doc_id, mention_text, resolver, score, resolved_at)
                VALUES ($1, $2, $3, $4, 'exact_identifier', 1.0, $5)
                RETURNING resolution_id
                """,
                entity_id,
                source_id,
                doc.doc_id,
                doc.title,
                doc.recorded_at,
            )
            await pool.execute(
                """
                INSERT INTO document_entity_link
                    (resolution_id, source_id, doc_id, lei, resolver, score, recorded_at)
                VALUES ($1, $2, $3, $4, 'exact_identifier', 1.0, $5)
                """,
                resolution_id,
                source_id,
                doc.doc_id,
                doc.identifiers["lei"],
                doc.recorded_at,
            )
        await pool.execute(
            """
            INSERT INTO retrieval_index_state
                (index_name, embed_model_version, embedding_dim,
                 chunker_version, status, indexed_at)
            VALUES ('document_chunk', $1, $2, $3, 'ready', now())
            ON CONFLICT (index_name) DO UPDATE SET
                embed_model_version = EXCLUDED.embed_model_version,
                embedding_dim = EXCLUDED.embedding_dim,
                chunker_version = EXCLUDED.chunker_version,
                status = 'ready',
                indexed_at = now()
            """,
            embedder.model_version,
            embedder.dim,
            CHUNKER_VERSION,
        )

        results = await CorpusSearch(store, embedder).search(
            "approved capital issuance programme",
            Principal(
                principal_id="postgres-query-contract",
                entitlement_group=entitlement_group,
                side=Side.PUBLIC,
            ),
            as_of=AS_OF,
            entity_lei="LEI-1",
            source_ids={source_id},
            mode="hybrid",
        )

        assert {result.doc.doc_id for result in results} == {"eligible"}
    finally:
        await pool.execute("DELETE FROM document_entity_link WHERE source_id = $1", source_id)
        await pool.execute("DELETE FROM entity_resolution WHERE source_id = $1", source_id)
        await pool.execute("DELETE FROM document_chunk WHERE source_id = $1", source_id)
        await pool.execute("DELETE FROM document WHERE source_id = $1", source_id)
        await pool.execute(
            "DELETE FROM entitlement_grant WHERE entitlement_group = $1 AND source_id = $2",
            entitlement_group,
            source_id,
        )
        await pool.execute("DELETE FROM source_registry WHERE source_id = $1", source_id)
        await pool.execute("DELETE FROM entity WHERE lei IN ('LEI-1', 'LEI-2')")
        await pool.execute("DELETE FROM retrieval_index_state WHERE index_name = 'document_chunk'")
        if previous_state is not None:
            await pool.execute(
                """
                INSERT INTO retrieval_index_state
                    (index_name, embed_model_version, embedding_dim,
                     chunker_version, status, indexed_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                previous_state["index_name"],
                previous_state["embed_model_version"],
                previous_state["embedding_dim"],
                previous_state["chunker_version"],
                previous_state["status"],
                previous_state["indexed_at"],
            )
        await store.close()
