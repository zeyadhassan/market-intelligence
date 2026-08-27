"""Dedupe tests, including the negative cases that actually carry weight.

The positive case (two wires, same story) must collapse. The negative
cases — a different story in the same event window, and the decoy's
steady-state documents — must NOT collapse. A detector that fires on the
decoy is broken even if it also fires on the positive case.
"""

from fi_intel.ingest.dedupe import NEAR_DUP_THRESHOLD, DedupeIndex, DuplicateVerdict
from fi_intel.sources.fixture import synthetic_wire


async def _corpus() -> dict:
    return {d.doc_id: d async for d in synthetic_wire().fetch()}


async def test_exact_duplicate_is_dropped() -> None:
    docs = await _corpus()
    index = DedupeIndex()
    assert index.classify(docs["SW-2024-0001"]) is docs["SW-2024-0001"]
    # SW-2024-0002 is a byte-identical reprint of 0001.
    assert index.classify(docs["SW-2024-0002"]) is None


async def test_near_duplicate_wire_collapses_with_score() -> None:
    docs = await _corpus()
    index = DedupeIndex()
    index.classify(docs["SW-2024-0001"])
    verdict = index.classify(docs["SW-2024-0003"])
    assert isinstance(verdict, DuplicateVerdict)
    assert verdict.canonical.doc_id == "SW-2024-0001"
    assert verdict.similarity >= NEAR_DUP_THRESHOLD


async def test_distinct_story_same_event_window_survives() -> None:
    docs = await _corpus()
    index = DedupeIndex()
    index.classify(docs["SW-2024-0001"])
    index.classify(docs["SW-2024-0003"])
    # FY2023 results: same entity, same window, genuinely different story.
    assert index.classify(docs["SW-2024-0004"]) is docs["SW-2024-0004"]


async def test_decoy_documents_never_collapse() -> None:
    """Negative test: the steady-state decoy must produce zero dedupe hits."""
    docs = await _corpus()
    index = DedupeIndex()
    decoy_ids = ["SW-2024-0009", "SW-2024-0010", "SW-2024-0011"]
    for doc_id in decoy_ids:
        assert index.classify(docs[doc_id]) is docs[doc_id]


async def test_full_corpus_classifies_to_ten_unique() -> None:
    docs = await _corpus()
    index = DedupeIndex()
    novel, near, exact = 0, 0, 0
    for doc in docs.values():
        verdict = index.classify(doc)
        if verdict is None:
            exact += 1
        elif isinstance(verdict, DuplicateVerdict):
            near += 1
        else:
            novel += 1
    assert (novel, near, exact) == (10, 1, 1)


async def test_resume_seed_classifies_identically_to_first_run() -> None:
    """A resumed run must not re-persist or mis-classify already-seen docs."""
    docs = await _corpus()
    first = DedupeIndex()
    seen = []
    for doc in list(docs.values())[:6]:
        verdict = first.classify(doc)
        if verdict is not None and not isinstance(verdict, DuplicateVerdict):
            seen.append(verdict)

    resumed = DedupeIndex()
    resumed.load(seen)
    for doc in list(docs.values())[:6]:
        verdict = resumed.classify(doc)
        assert verdict is None or isinstance(verdict, DuplicateVerdict)
