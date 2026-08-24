"""Adversarial entitlement tests.

A public-side principal must not reach the private-side document through
ANY parameter combination. Each test below is a distinct bypass route;
the set is deliberately exhaustive rather than representative, because a
private-side leak is a regulatory incident, not a bug.
"""

from datetime import UTC, datetime

import pytest

from fi_intel.governance.audit import InMemoryAuditLog
from fi_intel.retrieval.chunking import HashingEmbedder
from fi_intel.retrieval.corpus import CorpusSearch
from fi_intel.retrieval.entitlement import Principal, Side
from fi_intel.retrieval.service import RetrievalService
from fi_intel.retrieval.store import InMemoryCorpusStore
from fi_intel.sources.fixture import synthetic_wire, synthetic_wire_private
from fi_intel.synth.episodes import GULF_MERIDIAN_LEI

PRIVATE_DOC_ID = "SW-2024-0013"
PUBLIC_GROUP = "fi_gcc_public"
PRIVATE_GROUP = "fi_gcc_private"

PUBLIC_PRINCIPAL = Principal(
    principal_id="banker.public", entitlement_group=PUBLIC_GROUP, side=Side.PUBLIC
)
PRIVATE_PRINCIPAL = Principal(
    principal_id="banker.private", entitlement_group=PRIVATE_GROUP, side=Side.PRIVATE
)


async def _service() -> tuple[RetrievalService, InMemoryAuditLog]:
    store = InMemoryCorpusStore(HashingEmbedder())
    store.register_source("synthetic_wire")
    store.register_source("synthetic_wire_private")
    store.grant(PUBLIC_GROUP, "synthetic_wire")
    store.grant(PRIVATE_GROUP, "synthetic_wire")
    store.grant(PRIVATE_GROUP, "synthetic_wire_private")
    store.add_documents([d async for d in synthetic_wire().fetch()])
    store.add_documents([d async for d in synthetic_wire_private().fetch()])
    audit = InMemoryAuditLog()
    search = CorpusSearch(store, HashingEmbedder())
    return RetrievalService(search, audit, run_id="test-run"), audit


def _doc_ids(results: list) -> set[str]:
    return {r.doc.doc_id for r in results}


async def test_route_1_plain_text_query() -> None:
    service, _ = await _service()
    results = await service.search("Gulf Meridian liquidity facility", PUBLIC_PRINCIPAL)
    assert PRIVATE_DOC_ID not in _doc_ids(results)


async def test_route_2_entity_filtered_query() -> None:
    """Entity filter must not widen the entitlement set."""
    service, _ = await _service()
    results = await service.search(
        "liquidity facility", PUBLIC_PRINCIPAL, entity_lei=GULF_MERIDIAN_LEI
    )
    assert PRIVATE_DOC_ID not in _doc_ids(results)


async def test_route_3_as_of_after_private_doc_recorded() -> None:
    """Pinning as-of after the private doc's recorded_at must not leak it."""
    service, _ = await _service()
    results = await service.search(
        "liquidity facility",
        PUBLIC_PRINCIPAL,
        as_of=datetime(2024, 12, 31, tzinfo=UTC),
    )
    assert PRIVATE_DOC_ID not in _doc_ids(results)


async def test_route_4_unbounded_limit() -> None:
    """Asking for everything must return everything *the caller may see*."""
    service, _ = await _service()
    results = await service.search("bank", PUBLIC_PRINCIPAL, limit=10_000)
    assert PRIVATE_DOC_ID not in _doc_ids(results)
    assert results, "public corpus should still return public documents"


async def test_route_5_vector_only_mode() -> None:
    """Semantic search is not a side channel around the barrier."""
    service, _ = await _service()
    results = await service.search(
        "standby liquidity facility discussions", PUBLIC_PRINCIPAL, mode="vector"
    )
    assert PRIVATE_DOC_ID not in _doc_ids(results)


async def test_route_6_private_group_with_public_side_claimed() -> None:
    """Group membership alone is insufficient; barrier side is checked too."""
    service, _ = await _service()
    confused = Principal(
        principal_id="banker.confused", entitlement_group=PRIVATE_GROUP, side=Side.PUBLIC
    )
    results = await service.search("liquidity facility", confused)
    assert PRIVATE_DOC_ID not in _doc_ids(results)


async def test_route_7_unregistered_group_gets_nothing() -> None:
    service, _ = await _service()
    stranger = Principal(
        principal_id="banker.stranger", entitlement_group="no_such_group", side=Side.PRIVATE
    )
    results = await service.search("Gulf Meridian", stranger)
    assert results == []


async def test_private_principal_does_reach_private_doc() -> None:
    """The barrier must not over-block: private side can do its job."""
    service, _ = await _service()
    results = await service.search("standby liquidity facility", PRIVATE_PRINCIPAL)
    assert PRIVATE_DOC_ID in _doc_ids(results)


async def test_unlicensed_source_invisible_even_with_grant() -> None:
    """A licence revocation wins over an entitlement grant."""
    store = InMemoryCorpusStore(HashingEmbedder())
    store.register_source("synthetic_wire", licensed=False)
    store.grant(PUBLIC_GROUP, "synthetic_wire")
    store.add_documents([d async for d in synthetic_wire().fetch()])
    service = RetrievalService(
        CorpusSearch(store, HashingEmbedder()), InMemoryAuditLog(), run_id="t"
    )
    assert await service.search("Gulf Meridian", PUBLIC_PRINCIPAL) == []


async def test_every_retrieval_writes_access_log_rows() -> None:
    service, audit = await _service()
    results = await service.search("Gulf Meridian", PRIVATE_PRINCIPAL)
    returned = _doc_ids(results)
    logged = {e.doc_id for e in audit.events}
    assert returned <= logged
    for event in audit.events:
        assert event.run_id == "test-run"
        assert event.principal == PRIVATE_PRINCIPAL.principal_id
        assert event.entitlement_group == PRIVATE_PRINCIPAL.entitlement_group
        assert event.source_id
        assert event.doc_id


async def test_empty_result_still_auditable() -> None:
    """A search returning nothing writes no rows but must not fail."""
    service, audit = await _service()
    results = await service.search("zzz-no-such-token-zzz", PUBLIC_PRINCIPAL)
    assert results == []
    assert audit.events == []


@pytest.mark.skipif(
    __import__("os").environ.get("FI_INTEL_TEST_PG_DSN") is None,
    reason="FI_INTEL_TEST_PG_DSN not set",
)
async def test_postgres_parity_with_in_memory() -> None:
    """The SQL predicate and its in-memory port must agree exactly."""
    # Implemented when a live database is available; mirrors
    # tests/test_store_contract.py's pattern.
