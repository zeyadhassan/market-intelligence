"""Contract tests for DocumentStore implementations.

InMemoryDocumentStore always runs. PostgresDocumentStore runs when
FI_INTEL_TEST_PG_DSN points at a live database (e.g. the compose stack);
otherwise it skips. Both must satisfy the same contract.
"""

import os
from datetime import UTC, datetime

import pytest

from fi_intel.ingest.dedupe import DuplicateVerdict
from fi_intel.ingest.store import (
    DocumentStore,
    InMemoryDocumentStore,
    PostgresDocumentStore,
)
from fi_intel.sources.base import FetchCursor
from fi_intel.sources.canonical import CanonicalDocument, DocumentClass

PG_DSN = os.environ.get("FI_INTEL_TEST_PG_DSN")


def _doc(doc_id: str, day: int) -> CanonicalDocument:
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="contract_source",
        published_at=datetime(2024, 1, day, 8, tzinfo=UTC),
        recorded_at=datetime(2024, 1, day, 9, tzinfo=UTC),
        title=f"Title {doc_id}",
        body=f"Body {doc_id}",
        document_class=DocumentClass.NEWS_WIRE,
    )


def _cursor(position: str) -> FetchCursor:
    return FetchCursor(
        source_id="contract_source",
        position=position,
        updated_at=datetime(2024, 1, 2, 10, tzinfo=UTC),
    )


async def _check_contract(store: DocumentStore) -> None:
    docs = [_doc("C-1", 1), _doc("C-2", 2)]
    dupe = DuplicateVerdict(doc=_doc("C-2R", 2), canonical=docs[0], similarity=0.9)

    await store.commit_batch(docs, [dupe], _cursor("2"))
    # Idempotent: committing the same batch again changes nothing.
    await store.commit_batch(docs, [dupe], _cursor("2"))

    loaded = await store.load_documents("contract_source")
    assert [d.doc_id for d in loaded] == ["C-1", "C-2"]

    cursor = await store.load_cursor("contract_source")
    assert cursor is not None and cursor.position == "2"

    status = {s.source_id: s for s in await store.status()}["contract_source"]
    assert status.document_count == 2
    assert status.duplicate_count == 1

    assert await store.load_cursor("nonexistent_source") is None


async def test_in_memory_store_contract() -> None:
    await _check_contract(InMemoryDocumentStore())


@pytest.mark.skipif(PG_DSN is None, reason="FI_INTEL_TEST_PG_DSN not set")
async def test_postgres_store_contract() -> None:
    assert PG_DSN is not None
    store = PostgresDocumentStore(PG_DSN)
    try:
        # Clean slate for the contract source only.
        pool = await store._get_pool()  # noqa: SLF001
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO source_registry (source_id, display_name, licence_group)"
                " VALUES ('contract_source', 'Contract test', 'test')"
                " ON CONFLICT (source_id) DO NOTHING"
            )
            await conn.execute(
                "DELETE FROM document_chunk WHERE source_id = 'contract_source'"
            )
            await conn.execute(
                "DELETE FROM document_duplicate WHERE source_id = 'contract_source'"
            )
            await conn.execute("DELETE FROM document WHERE source_id = 'contract_source'")
            await conn.execute(
                "DELETE FROM ingest_cursor WHERE source_id = 'contract_source'"
            )
        await _check_contract(store)
    finally:
        await store.close()
