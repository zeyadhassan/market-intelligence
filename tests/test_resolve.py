"""Entity resolution tests.

The trap test (similar name, different institution must NOT merge) matters
more than the positive test. Precision is the metric; recall is allowed to
be low — queue depth is visible, a false merge is not.
"""

import os
from datetime import UTC, datetime

import pytest

from fi_intel.ingest.resolve import (
    FUZZY_AUTO_MERGE_THRESHOLD,
    EntityResolver,
    InMemoryResolutionStore,
    ReferenceEntity,
    ResolutionStore,
    ResolverName,
    normalize_name,
    token_sort_ratio,
)
from fi_intel.sources.adapters.gleif import gleif_fixture
from fi_intel.sources.canonical import CanonicalDocument, DocumentClass
from fi_intel.sources.fixture import synthetic_wire
from fi_intel.synth.episodes import (
    GULF_MERIDIAN_CAPITAL_LEI,
    GULF_MERIDIAN_LEI,
    GULF_MERIDIAN_NAME_VARIANTS,
    NORTHERN_HARBOUR_LEI,
)


async def _resolved(store: ResolutionStore) -> None:
    await store.load_reference([d async for d in gleif_fixture().fetch()])
    resolver = EntityResolver(store)
    async for doc in synthetic_wire().fetch():
        await resolver.resolve_document(doc)


async def test_all_name_variants_collapse_to_one_lei() -> None:
    store = InMemoryResolutionStore()
    await _resolved(store)
    resolutions = await store.resolutions()

    gm = [r for r in resolutions if r.mention_text in GULF_MERIDIAN_NAME_VARIANTS]
    assert {r.lei for r in gm} == {GULF_MERIDIAN_LEI}
    # All three variants appear in the corpus and all three resolve.
    assert {r.mention_text for r in gm} == set(GULF_MERIDIAN_NAME_VARIANTS)


async def test_similar_named_different_institution_does_not_merge() -> None:
    """Similar names must not cause a false entity merge."""
    store = InMemoryResolutionStore()
    await _resolved(store)
    resolutions = await store.resolutions()

    trap = [r for r in resolutions if "Capital Partners" in r.mention_text]
    assert trap, "trap mention should resolve to its OWN entity"
    assert {r.lei for r in trap} == {GULF_MERIDIAN_CAPITAL_LEI}
    assert all(r.lei != GULF_MERIDIAN_LEI for r in trap)


async def test_decoy_resolves_to_its_own_lei_only() -> None:
    store = InMemoryResolutionStore()
    await _resolved(store)
    resolutions = await store.resolutions()

    decoy = [r for r in resolutions if "Northern Harbour" in r.mention_text]
    assert decoy
    assert {r.lei for r in decoy} == {NORTHERN_HARBOUR_LEI}


async def test_precision_on_labelled_set() -> None:
    """Every resolution in the corpus is labelled by construction."""
    store = InMemoryResolutionStore()
    await _resolved(store)
    resolutions = await store.resolutions()

    expected = {
        GULF_MERIDIAN_LEI,
        NORTHERN_HARBOUR_LEI,
        GULF_MERIDIAN_CAPITAL_LEI,
    }
    correct = 0
    for r in resolutions:
        if "Northern Harbour" in r.mention_text:
            correct += r.lei == NORTHERN_HARBOUR_LEI
        elif "Capital Partners" in r.mention_text:
            correct += r.lei == GULF_MERIDIAN_CAPITAL_LEI
        else:
            correct += r.lei == GULF_MERIDIAN_LEI
    precision = correct / len(resolutions)
    assert all(r.lei in expected for r in resolutions)
    assert precision > 0.98, f"precision {precision:.3f} below 0.98"
    # Record precision on the labelled set.
    from fi_intel.logging import get_logger

    get_logger(component="test.resolve").info(
        "precision.report", precision=precision, n_resolutions=len(resolutions)
    )


async def test_every_resolution_records_resolver_and_score() -> None:
    store = InMemoryResolutionStore()
    await _resolved(store)
    for r in await store.resolutions():
        assert r.resolver in set(ResolverName)
        assert 0.0 < r.score <= 1.0


async def test_unmatched_mention_queues_instead_of_guessing() -> None:
    store = InMemoryResolutionStore()
    await store.load_reference([d async for d in gleif_fixture().fetch()])
    resolver = EntityResolver(store)

    docs = [d async for d in synthetic_wire().fetch()]
    trap_doc = next(d for d in docs if d.metadata.get("episode") == "resolution_trap")
    # Strip the document's own identifiers AND the trap's reference entity:
    # now its mention has only a below-threshold candidate (Gulf Meridian
    # Bank) and must queue rather than guess.
    store._reference.pop(GULF_MERIDIAN_CAPITAL_LEI)  # noqa: SLF001
    bare_doc = trap_doc.model_copy(update={"identifiers": {}})

    await resolver.resolve_document(bare_doc)
    assert await store.resolutions() == []
    queue = await store.queue()
    assert len(queue) == 1
    assert queue[0].candidate_lei == GULF_MERIDIAN_LEI
    assert queue[0].best_score is not None
    assert queue[0].best_score < FUZZY_AUTO_MERGE_THRESHOLD / 100.0
    assert 0.0 <= queue[0].best_score <= 1.0


def test_threshold_separates_variants_from_trap() -> None:
    """Pin the measured numbers the threshold is justified from."""
    variant = token_sort_ratio(
        normalize_name("Gulf Meridian"), normalize_name("Gulf Meridian Bank Q.P.S.C.")
    )
    trap = token_sort_ratio(
        normalize_name("Gulf Meridian Capital Partners"),
        normalize_name("Gulf Meridian Bank Q.P.S.C."),
    )
    assert variant > FUZZY_AUTO_MERGE_THRESHOLD > trap


@pytest.mark.parametrize(
    ("mention", "reference_name"),
    [
        ("Credit Suisse Group AG", "Credit Suisse AG"),
        ("Meridian Group Holdings", "Meridian Group"),
        ("Gulf Meridian Holdings", "Gulf Meridian Bank"),
    ],
)
async def test_holdco_opco_pairs_always_queue(
    mention: str, reference_name: str
) -> None:
    store = InMemoryResolutionStore()
    store._reference["TRAP-LEI"] = ReferenceEntity(  # noqa: SLF001
        lei="TRAP-LEI",
        legal_name=reference_name,
        jurisdiction="GB",
        sector="financial_services",
    )
    document = CanonicalDocument(
        doc_id="family-form-trap",
        source_id="resolution-test",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        recorded_at=datetime(2024, 1, 1, 1, tzinfo=UTC),
        title=mention,
        body=f"{mention} published an update.",
        document_class=DocumentClass.NEWS_WIRE,
        mentioned_names=(mention,),
    )

    await EntityResolver(store).resolve_document(document, recorded_at=document.recorded_at)

    assert await store.resolutions() == []
    (queued,) = await store.queue()
    assert queued.candidate_lei == "TRAP-LEI"


def test_normalization_preserves_family_and_single_letter_identity_tokens() -> None:
    assert normalize_name("Credit Suisse Group AG") != normalize_name("Credit Suisse AG")
    assert normalize_name("Q Bank Q.P.S.C.") == "q bank"


PG_DSN = os.environ.get("FI_INTEL_TEST_PG_DSN")


@pytest.mark.skipif(PG_DSN is None, reason="FI_INTEL_TEST_PG_DSN not set")
async def test_postgres_resolution_store_matches_in_memory() -> None:
    """Same scenario, both stores: identical resolutions and queue."""
    assert PG_DSN is not None
    from fi_intel.ingest.resolve_store import PostgresResolutionStore

    pg = PostgresResolutionStore(PG_DSN)
    try:
        pool = await pg._get_pool()  # noqa: SLF001
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM document_entity_link")
            await conn.execute("DELETE FROM entity_resolution")
            await conn.execute("DELETE FROM resolution_queue")
            await conn.execute("DELETE FROM entity_parent")
            await conn.execute("DELETE FROM entity")
        await _resolved(pg)
        pg_resolutions = await pg.resolutions()
        pg_queue = await pg.queue()
    finally:
        await pg.close()

    mem = InMemoryResolutionStore()
    await _resolved(mem)
    mem_resolutions = await mem.resolutions()
    mem_queue = await mem.queue()

    assert [(r.doc_id, r.mention_text, r.lei) for r in pg_resolutions] == [
        (r.doc_id, r.mention_text, r.lei) for r in mem_resolutions
    ]
    assert len(pg_queue) == len(mem_queue)
