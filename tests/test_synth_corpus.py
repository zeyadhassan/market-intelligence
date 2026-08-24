"""Golden-path tests over the synthetic corpus.

These pin the corpus contract that later milestones rely on: document
counts, dedupe case structure, name variants, and the decoy's silence.
"""

import json
from datetime import date
from importlib import resources

from fi_intel.sources.fixture import synthetic_wire
from fi_intel.synth.episodes import (
    EPISODE_START,
    GULF_MERIDIAN,
    GULF_MERIDIAN_NAME_VARIANTS,
    NORTHERN_HARBOUR,
    SIGNAL_DEADLINE_DAY,
)


async def _corpus() -> list:
    return [doc async for doc in synthetic_wire().fetch()]


async def test_raw_records_dedupe_to_ten_unique_documents() -> None:
    docs = await _corpus()
    assert len(docs) == 12
    # Exact dedupe (content hash) collapses the reprint pair 0001/0002,
    # leaving 11. Near-duplicate detection (M2) then collapses 0003 into
    # 0001, reaching the 10 unique documents the M2 criterion refers to.
    unique_hashes = {d.content_hash() for d in docs}
    assert len(unique_hashes) == 11
    near_dupes = [d for d in docs if d.metadata.get("dedupe_case") == "near_duplicate_wire"]
    assert len(near_dupes) == 1
    canonical = [d for d in docs if d.doc_id == "SW-2024-0001"]
    assert near_dupes[0].content_hash() != canonical[0].content_hash()


async def test_gulf_meridian_episode_has_seven_unique_documents() -> None:
    docs = await _corpus()
    gm = [d for d in docs if d.metadata.get("episode") == "gulf_meridian_dcm"]
    assert len({d.content_hash() for d in gm}) == 7


async def test_all_name_variants_present_for_resolution_test() -> None:
    docs = await _corpus()
    mentioned = {name for d in docs for name in d.mentioned_names}
    for variant in GULF_MERIDIAN_NAME_VARIANTS:
        assert variant in mentioned, f"missing name variant {variant!r}"


async def test_decoy_episode_produces_documents_but_expects_no_signals() -> None:
    docs = await _corpus()
    decoy = [d for d in docs if d.metadata.get("episode") == NORTHERN_HARBOUR.episode_id]
    assert len(decoy) == 3
    assert NORTHERN_HARBOUR.expected_signals == ()
    assert NORTHERN_HARBOUR.is_decoy


async def test_similar_named_distinct_entity_present() -> None:
    docs = await _corpus()
    trap = [d for d in docs if d.metadata.get("episode") == "resolution_trap"]
    assert len(trap) == 1
    assert trap[0].identifiers["lei"] != GULF_MERIDIAN.entity_lei


def test_ground_truth_matches_episode_definitions() -> None:
    ref = resources.files("fi_intel.synth.data").joinpath("ground_truth.json")
    truth = json.loads(ref.read_text(encoding="utf-8"))
    by_id = {e["episode_id"]: e for e in truth["episodes"]}

    gm = by_id[GULF_MERIDIAN.episode_id]
    assert gm["entity_lei"] == GULF_MERIDIAN.entity_lei
    assert {s["pattern"] for s in gm["expected_signals"]} == {
        s.pattern for s in GULF_MERIDIAN.expected_signals
    }
    # Every expected signal must fire before the outcome and before the deadline.
    outcome_day = date.fromisoformat(gm["outcome"]["date"])
    for signal in GULF_MERIDIAN.expected_signals:
        assert signal.fires_by < outcome_day
        assert (signal.fires_by - EPISODE_START).days < SIGNAL_DEADLINE_DAY

    decoy = by_id[NORTHERN_HARBOUR.episode_id]
    assert decoy["expected_signals"] == []
    assert decoy["outcome"] is None
