"""Contract tests for source adapters.

Every adapter — including future vendor adapters — must pass this module
unmodified (CLAUDE.md testing requirements). Add the adapter to
ADAPTERS_UNDER_TEST; do not edit the tests to fit the adapter.
"""

from collections.abc import AsyncIterator

import pytest

from fi_intel.sources.base import FetchCursor, SourceAdapter
from fi_intel.sources.canonical import CanonicalDocument
from fi_intel.sources.fixture import synthetic_wire
from fi_intel.sources.vendor_stub import VendorStubAdapter

ADAPTERS_UNDER_TEST: list[tuple[str, object]] = [
    ("synthetic_wire", synthetic_wire),
    ("vendor_stub", VendorStubAdapter),
]


def _make(adapter_factory: object) -> SourceAdapter:
    adapter = adapter_factory()  # type: ignore[operator]
    assert isinstance(adapter, SourceAdapter), "adapter must satisfy the SourceAdapter protocol"
    return adapter


async def _collect(stream: AsyncIterator[CanonicalDocument]) -> list[CanonicalDocument]:
    return [doc async for doc in stream]


@pytest.mark.parametrize("name,factory", ADAPTERS_UNDER_TEST)
async def test_fetch_yields_only_canonical_documents(name: str, factory: object) -> None:
    docs = await _collect(_make(factory).fetch())
    assert docs, f"{name}: adapter must serve at least one document"
    assert all(isinstance(d, CanonicalDocument) for d in docs)


@pytest.mark.parametrize("name,factory", ADAPTERS_UNDER_TEST)
async def test_documents_carry_source_id_and_timestamps(name: str, factory: object) -> None:
    adapter = _make(factory)
    async for doc in adapter.fetch():
        assert doc.source_id == adapter.source_id
        assert doc.recorded_at >= doc.published_at


@pytest.mark.parametrize("name,factory", ADAPTERS_UNDER_TEST)
async def test_fetch_is_resumable_without_gap_or_duplicate(name: str, factory: object) -> None:
    adapter = _make(factory)
    docs = await _collect(adapter.fetch())
    if len(docs) < 2:
        pytest.skip(f"{name}: needs at least two documents to test resumption")

    mid = len(docs) // 2
    cursor = adapter.cursor_for(docs[mid - 1])
    resumed = await _collect(adapter.fetch(cursor))

    assert [d.doc_id for d in resumed] == [d.doc_id for d in docs[mid:]]
    assert not {d.doc_id for d in resumed} & {d.doc_id for d in docs[:mid]}


@pytest.mark.parametrize("name,factory", ADAPTERS_UNDER_TEST)
async def test_cursor_round_trips_through_model(name: str, factory: object) -> None:
    adapter = _make(factory)
    docs = await _collect(adapter.fetch())
    cursor = adapter.cursor_for(docs[0])
    restored = FetchCursor.model_validate_json(cursor.model_dump_json())
    resumed = await _collect(adapter.fetch(restored))
    assert [d.doc_id for d in resumed] == [d.doc_id for d in docs[1:]]


@pytest.mark.parametrize("name,factory", ADAPTERS_UNDER_TEST)
async def test_foreign_cursor_rejected(name: str, factory: object) -> None:
    adapter = _make(factory)
    foreign = FetchCursor(
        source_id="some_other_source",
        position="0",
        updated_at=(await _collect(adapter.fetch()))[0].recorded_at,
    )
    with pytest.raises(ValueError, match="cursor"):
        await _collect(adapter.fetch(foreign))
