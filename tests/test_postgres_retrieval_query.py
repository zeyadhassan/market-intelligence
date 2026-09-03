"""Service-free contracts for bounded indexed Postgres retrieval."""

import hashlib
import inspect
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

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


def _contract_uuid(*parts: str) -> UUID:
    return uuid5(NAMESPACE_URL, "\x1f".join(parts))


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
        "section_path": ["Capital", "Issuance"],
        "chunk_id": 7,
        "document_version_id": "00000000-0000-0000-0000-000000000007",
        "entity_ids": ["entity-1"],
        "assertion_ids": ["assertion-1"],
        "evidence_span_ids": ["evidence-1"],
        "policy_id": "00000000-0000-0000-0000-000000000008",
        "valid_from": AS_OF,
        "valid_to": None,
        "content_hash": "a" * 64,
        "chunker_version": "structure-v2",
        "embed_model_version": "hashing-v1",
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
    assert "d.published_at >= $6::date" in sql
    assert "d.published_at < ($7::date + 1)" in sql


def test_incremental_index_does_not_delete_append_only_authority_links() -> None:
    implementation = inspect.getsource(PostgresCorpusStore._commit_document_chunks)

    assert "DELETE FROM document_chunk" not in implementation
    assert "c.embed_model_version = $10::text" in sql
    assert "c.normalized_search_vector as search_vector" in sql
    assert "c.canonical_lineage" in sql
    assert "join access_policy chunk_policy" in sql
    assert "exists ( select 1 from document_chunk_assertion_v4" in sql
    assert "assertion.recorded_at <= $3::timestamptz" in sql
    assert "e.search_vector @@ websearch_to_tsquery" in sql
    assert "e.embedding::halfvec(2048) <=> $9::halfvec(2048)" in sql
    assert sql.count("limit $11::int") == 2
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
    assert args[0:8] == (
        PRINCIPAL.entitlement_group,
        "public",
        AS_OF,
        "LEI-1",
        ["wire-a", "wire-b"],
        None,
        None,
        "capital programme",
    )
    assert args[9:] == ("hashing-v1", 50, "hybrid")
    assert len(candidates) == 1
    assert candidates[0].doc.identifiers == {"lei": "LEI-1"}
    assert candidates[0].bm25_rank == 1
    assert candidates[0].vector_rank == 2
    assert candidates[0].chunk.document_version_id == "00000000-0000-0000-0000-000000000007"
    assert candidates[0].chunk.entity_ids == ("entity-1",)
    assert candidates[0].chunk.assertion_ids == ("assertion-1",)
    assert candidates[0].chunk.evidence_span_ids == ("evidence-1",)
    assert candidates[0].chunk.structure_path == ("Capital", "Issuance")


async def test_postgres_store_normalizes_arabic_lexical_query_before_sql() -> None:
    pool = FakePool(_state(), [_row()])
    store = PostgresCorpusStore("unused")
    store._pool = cast(asyncpg.Pool, pool)  # noqa: SLF001

    await store.indexed_candidates(
        "إِصْدَار صُكُوك",
        [0.0] * EMBEDDING_DIM,
        embed_model_version="hashing-v1",
        embedding_dim=EMBEDDING_DIM,
        principal=PRINCIPAL,
        as_of=AS_OF,
        entity_lei=None,
        source_ids=None,
        mode="hybrid",
        candidate_limit=10,
    )

    assert pool.fetch_calls[0][1][7] == "اصدار صكوك"


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
    both = _candidate("both", 5, 1).model_copy(update={"bm25_score": 0.5, "vector_score": 0.5})
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
    assert {item.doc.doc_id for item in fused} == {item.doc.doc_id for item in without_vector}


@pytest.mark.skipif(PG_DSN is None, reason="FI_INTEL_TEST_PG_DSN not set")
async def test_indexed_query_executes_against_postgres_with_all_optional_filters() -> None:
    assert PG_DSN is not None
    source_id = "indexed_retrieval_contract_v2"
    entitlement_group = "indexed_retrieval_contract_v2"
    embedder = HashingEmbedder()
    store = PostgresCorpusStore(PG_DSN)
    pool = await store._get_pool()  # noqa: SLF001
    previous_state = await pool.fetchrow(
        "SELECT * FROM retrieval_index_state WHERE index_name = 'document_chunk'"
    )
    policy_id = _contract_uuid(source_id, "policy")
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
        await pool.execute(
            """
            INSERT INTO access_policy
                (policy_id, barrier_side, allowed_entitlement_groups,
                 semantic_key, created_at)
            VALUES ($1, 'public', ARRAY[$2]::text[], $3, $4)
            ON CONFLICT (policy_id) DO NOTHING
            """,
            policy_id,
            entitlement_group,
            hashlib.sha256(str(policy_id).encode()).hexdigest(),
            AS_OF,
        )
        entity_ids = {lei: _contract_uuid(source_id, "entity", lei) for lei in {"LEI-1", "LEI-2"}}
        entity_created_at = {
            lei: min(doc.recorded_at for doc in docs if doc.identifiers["lei"] == lei)
            for lei in entity_ids
        }
        for index, doc in enumerate(docs):
            raw_asset_id = _contract_uuid(source_id, doc.doc_id, "raw")
            document_identity_id = _contract_uuid(source_id, doc.doc_id, "document")
            document_version_id = _contract_uuid(source_id, doc.doc_id, "version")
            mention_id = _contract_uuid(source_id, doc.doc_id, "mention")
            entity_link_id = _contract_uuid(source_id, doc.doc_id, "entity-link")
            evidence_span_id = _contract_uuid(source_id, doc.doc_id, "evidence")
            candidate_id = _contract_uuid(source_id, doc.doc_id, "candidate")
            assertion_id = _contract_uuid(source_id, doc.doc_id, "assertion")
            governed_text = f"{doc.title}\n{doc.body}"
            governed_hash = hashlib.sha256(governed_text.encode()).hexdigest()
            await pool.execute(
                """
                INSERT INTO raw_asset
                    (raw_asset_id, source_id, external_id, source_revision,
                     object_uri, content_hash, media_type, fetched_at, policy_id)
                VALUES ($1,$2,$3,'1',$4,$5,'text/plain',$6,$7)
                ON CONFLICT (raw_asset_id) DO NOTHING
                """,
                raw_asset_id,
                source_id,
                doc.doc_id,
                f"memory://{source_id}/{doc.doc_id}",
                governed_hash,
                doc.recorded_at,
                policy_id,
            )
            await pool.execute(
                """
                INSERT INTO document_identity
                    (document_id, source_id, external_id, created_at)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (document_id) DO NOTHING
                """,
                document_identity_id,
                source_id,
                doc.doc_id,
                doc.recorded_at,
            )
            await pool.execute(
                """
                INSERT INTO document_version
                    (document_version_id, document_id, raw_asset_id,
                     version_number, source_revision, normalized_object_uri,
                     normalized_text_hash, title, language, document_class,
                     published_at, recorded_at, parser_version, policy_id)
                VALUES ($1,$2,$3,1,'1',$4,$5,$6,'en',$7,$8,$9,'test-v1',$10)
                ON CONFLICT (document_version_id) DO NOTHING
                """,
                document_version_id,
                document_identity_id,
                raw_asset_id,
                f"memory://normalized/{source_id}/{doc.doc_id}",
                governed_hash,
                doc.title,
                str(doc.document_class),
                doc.published_at,
                doc.recorded_at,
                policy_id,
            )
            await pool.execute(
                "UPDATE document_identity SET current_version_id=$2 WHERE document_id=$1",
                document_identity_id,
                document_version_id,
            )
            await pool.execute(
                """
                INSERT INTO entity_identity
                    (entity_id, entity_type, canonical_name, created_at, policy_id)
                VALUES ($1, 'organization', $2, $3, $4)
                ON CONFLICT (entity_id) DO NOTHING
                """,
                entity_ids[doc.identifiers["lei"]],
                doc.identifiers["lei"],
                entity_created_at[doc.identifiers["lei"]],
                policy_id,
            )
            await pool.execute(
                """
                INSERT INTO mention
                    (mention_id, document_version_id, kind, surface, char_start,
                     char_end, extractor_bundle_version, recorded_at, policy_id)
                VALUES ($1,$2,'organization',$3,0,$4,'test-v1',$5,$6)
                ON CONFLICT (mention_id) DO NOTHING
                """,
                mention_id,
                document_version_id,
                doc.title,
                len(doc.title),
                doc.recorded_at,
                policy_id,
            )
            await pool.execute(
                """
                INSERT INTO entity_link_decision
                    (decision_id, mention_id, status, entity_link_id, entity_id,
                     candidate_entity_ids, confidence, resolver_version, reason,
                     decided_at, decided_by, policy_id)
                SELECT $1,$2,'linked',$3,$4,ARRAY[$4]::uuid[],1.0,
                       'test-v1','exact test identifier',$5,'test',$6
                WHERE NOT EXISTS (
                    SELECT 1 FROM entity_link_decision WHERE decision_id=$1
                )
                ON CONFLICT (decision_id) DO NOTHING
                """,
                _contract_uuid(source_id, doc.doc_id, "link-decision"),
                mention_id,
                entity_link_id,
                entity_ids[doc.identifiers["lei"]],
                doc.recorded_at,
                policy_id,
            )
            await pool.execute(
                """
                INSERT INTO evidence_span
                    (evidence_span_id, document_version_id, char_start, char_end,
                     quote, quote_hash, recorded_at, policy_id)
                VALUES ($1,$2,0,$3,$4,$5,$6,$7)
                ON CONFLICT (evidence_span_id) DO NOTHING
                """,
                evidence_span_id,
                document_version_id,
                len(governed_text),
                governed_text,
                governed_hash,
                doc.recorded_at,
                policy_id,
            )
            async with pool.acquire() as connection, connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO claim_candidate
                        (candidate_id, document_version_id, subject_mention_id,
                         predicate, object_json, qualifiers, valid_from,
                         extractor_bundle_version, confidence, recorded_at, policy_id)
                    VALUES ($1,$2,$3,'mentions_programme',$4::jsonb,'{}'::jsonb,$5,
                            'test-v1',1.0,$6,$7)
                    ON CONFLICT (candidate_id) DO NOTHING
                    """,
                    candidate_id,
                    document_version_id,
                    mention_id,
                    json.dumps({"kind": "text", "value": "capital issuance programme"}),
                    doc.published_at,
                    doc.recorded_at,
                    policy_id,
                )
                await connection.execute(
                    """
                    INSERT INTO claim_candidate_evidence (candidate_id, evidence_span_id)
                    VALUES ($1,$2)
                    ON CONFLICT DO NOTHING
                    """,
                    candidate_id,
                    evidence_span_id,
                )
            async with pool.acquire() as connection, connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO knowledge_assertion
                        (assertion_id, candidate_id, subject_entity_id,
                         subject_entity_link_id, predicate, object_json, qualifiers,
                         valid_from, recorded_at, confidence, ontology_version, policy_id)
                    VALUES ($1,$2,$3,$4,'mentions_programme',$5::jsonb,'{}'::jsonb,
                            $6,$7,1.0,'test-v1',$8)
                    ON CONFLICT (assertion_id) DO NOTHING
                    """,
                    assertion_id,
                    candidate_id,
                    entity_ids[doc.identifiers["lei"]],
                    entity_link_id,
                    json.dumps({"kind": "text", "value": "capital issuance programme"}),
                    doc.published_at,
                    doc.recorded_at,
                    policy_id,
                )
                await connection.execute(
                    """
                    INSERT INTO knowledge_assertion_evidence
                        (assertion_id, evidence_span_id)
                    VALUES ($1,$2)
                    ON CONFLICT DO NOTHING
                    """,
                    assertion_id,
                    evidence_span_id,
                )
            await pool.execute(
                """
                INSERT INTO document
                    (doc_id, source_id, content_hash, title, body, language,
                     document_class, barrier_side, published_at, recorded_at,
                     mentioned_names, identifiers, metadata)
                VALUES ($1, $2, $3, $4, $5, 'en', $6, 'public', $7, $8,
                        '{}', $9::jsonb, $10::jsonb)
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
                json.dumps({"ledger_document_version_id": str(document_version_id)}),
            )
            chunks = chunk_document(doc)
            embeddings = await embedder.embed_batch(
                [chunk.text for chunk in chunks], kind="document"
            )
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                chunk_id = await pool.fetchval(
                    """
                    INSERT INTO document_chunk
                        (source_id, doc_id, chunk_index, char_start, char_end,
                         text, embedding, embed_model_version, document_version_id,
                         policy_id, chunker_version, content_hash, canonical_lineage)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::vector, $8,
                            $9, $10, $11, $12, TRUE)
                    ON CONFLICT (source_id, doc_id, chunk_index) DO UPDATE SET
                        text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding,
                        embed_model_version = EXCLUDED.embed_model_version,
                        document_version_id = EXCLUDED.document_version_id,
                        policy_id = EXCLUDED.policy_id,
                        chunker_version = EXCLUDED.chunker_version,
                        content_hash = EXCLUDED.content_hash,
                        canonical_lineage = TRUE
                    RETURNING chunk_id
                    """,
                    source_id,
                    doc.doc_id,
                    chunk.chunk_index,
                    chunk.char_start,
                    chunk.char_end,
                    chunk.text,
                    str(embedding),
                    embedder.model_version,
                    document_version_id,
                    policy_id,
                    CHUNKER_VERSION,
                    governed_hash,
                )
                assert chunk_id is not None
                await pool.execute(
                    """
                    INSERT INTO document_chunk_assertion_v4
                        (chunk_id, assertion_id, policy_id)
                    VALUES ($1,$2,$3) ON CONFLICT DO NOTHING
                    """,
                    chunk_id,
                    assertion_id,
                    policy_id,
                )
                await pool.execute(
                    """
                    INSERT INTO document_chunk_evidence_v4
                        (chunk_id, evidence_span_id, policy_id)
                    VALUES ($1,$2,$3) ON CONFLICT DO NOTHING
                    """,
                    chunk_id,
                    evidence_span_id,
                    policy_id,
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
        # Governed document/chunk lineage is append-only. Stable fixture IDs
        # make repeated runs idempotent; revoking the mutable grant leaves the
        # audit fixture inert without bypassing immutability.
        await pool.execute(
            "DELETE FROM entitlement_grant WHERE entitlement_group = $1 AND source_id = $2",
            entitlement_group,
            source_id,
        )
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
