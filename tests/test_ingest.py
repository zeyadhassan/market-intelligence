"""Ingestion pipeline tests.

The resume test injects a real failure mid-stream (a store that raises
after N committed batches) rather than mocking the failure away — the
pipeline's actual exception path is what must leave a resumable cursor.
"""

import pytest

from fi_intel.ingest.pipeline import IngestPipeline
from fi_intel.ingest.store import InMemoryDocumentStore
from fi_intel.sources.fixture import FixtureAdapter, synthetic_wire


class FailingStore:
    """Commits N batches, then raises. Not a mock: a real broken store."""

    def __init__(self, inner: InMemoryDocumentStore, fail_after_batches: int) -> None:
        self._inner = inner
        self._remaining = fail_after_batches

    async def commit_batch(self, docs, duplicates, cursor) -> None:  # noqa: ANN001
        if self._remaining <= 0:
            raise RuntimeError("injected store failure")
        self._remaining -= 1
        await self._inner.commit_batch(docs, duplicates, cursor)

    def __getattr__(self, name: str):  # noqa: ANN001, ANN204
        return getattr(self._inner, name)


async def test_ingesting_twice_produces_ten_documents_not_twenty() -> None:
    store = InMemoryDocumentStore()
    first = await IngestPipeline(store).run(synthetic_wire())
    assert first.fetched == 12
    assert first.persisted == 10
    assert first.near_duplicates == 1
    assert first.exact_duplicates == 1

    second = await IngestPipeline(store).run(synthetic_wire())
    # Cursor resumes at end of stream: nothing re-fetched, nothing re-persisted.
    assert second.fetched == 0
    assert second.persisted == 0

    status = {s.source_id: s for s in await store.status()}["synthetic_wire"]
    assert status.document_count == 10
    assert status.duplicate_count == 1


async def test_resume_after_injected_failure_has_no_gap_or_duplicate() -> None:
    inner = InMemoryDocumentStore()
    failing = FailingStore(inner, fail_after_batches=1)

    with pytest.raises(RuntimeError, match="injected store failure"):
        await IngestPipeline(failing, batch_size=4).run(synthetic_wire())

    committed_after_crash = len(await inner.load_documents("synthetic_wire"))
    assert 0 < committed_after_crash < 10

    # Restart with a healthy store against the same persisted state.
    result = await IngestPipeline(inner, batch_size=4).run(synthetic_wire())

    final_docs = await inner.load_documents("synthetic_wire")
    doc_ids = [d.doc_id for d in final_docs]
    assert len(doc_ids) == 10
    assert len(set(doc_ids)) == 10, "duplicate persisted after resume"
    assert result.persisted == 10 - committed_after_crash


async def test_malformed_document_fails_loudly_and_commits_nothing_partial() -> None:
    store = InMemoryDocumentStore()
    adapter = FixtureAdapter(source_id="synthetic_wire", fixture_name="malformed_wire.json")

    with pytest.raises(Exception, match="SW-BAD-0001"):
        await IngestPipeline(store, batch_size=10).run(adapter)

    assert await store.load_documents("synthetic_wire") == []
    assert await store.load_cursor("synthetic_wire") is None


async def test_malformed_after_good_batch_keeps_only_committed_batches() -> None:
    store = InMemoryDocumentStore()
    adapter = FixtureAdapter(source_id="synthetic_wire", fixture_name="malformed_wire.json")

    with pytest.raises(Exception, match="SW-BAD-0001"):
        await IngestPipeline(store, batch_size=1).run(adapter)

    # One good document committed before the malformed one; the cursor
    # points exactly after it, so a fixed source resumes without re-doing it.
    docs = await store.load_documents("synthetic_wire")
    assert [d.doc_id for d in docs] == ["SW-OK-0001"]
    cursor = await store.load_cursor("synthetic_wire")
    assert cursor is not None and cursor.position == "1"


async def test_status_reports_counts_and_cursor() -> None:
    store = InMemoryDocumentStore()
    await IngestPipeline(store).run(synthetic_wire())
    (status,) = await store.status()
    assert status.source_id == "synthetic_wire"
    assert status.document_count == 10
    assert status.duplicate_count == 1
    assert status.cursor_position == "12"
    assert status.cursor_updated_at is not None
