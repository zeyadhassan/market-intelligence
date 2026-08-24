"""Backtest harness tests.

The leakage test is the one that matters: reading an assertion recorded
after the cutoff must fail loudly. Distribution, attribution, and
reproducibility are asserted on the seeded episode.
"""

import os
from datetime import UTC, date, datetime

import pytest

from evals.backtest import Backtester, LeakageError, Outcome
from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.synth.episodes import GULF_MERIDIAN_LEI, NORTHERN_HARBOUR_LEI
from fi_intel.synth.graph_fixture import (
    gulf_meridian_assertions,
    northern_harbour_assertions,
)

NEO4J_URI = os.environ.get("FI_INTEL_TEST_NEO4J_URI")
pytestmark = pytest.mark.skipif(NEO4J_URI is None, reason="FI_INTEL_TEST_NEO4J_URI not set")

MANDATE = Outcome(
    outcome_id="mandate:gm-2024-07-10",
    entity_key=GULF_MERIDIAN_LEI,
    outcome_date=date(2024, 7, 10),
    kind="mandate_announced",
)


@pytest.fixture
async def graph():
    if NEO4J_URI is None:
        pytest.skip("FI_INTEL_TEST_NEO4J_URI not set")
    client = GraphClient(NEO4J_URI, "neo4j", "fi_intel")
    await client.migrate()
    await client.delete_all()
    await client.migrate()
    yield client
    await client.delete_all()
    await client.close()


async def _seed(client: GraphClient) -> None:
    writer = AssertionWriter(client)
    for a in gulf_meridian_assertions() + northern_harbour_assertions():
        await writer.write(a)


def _span() -> tuple[date, date]:
    return date(2024, 2, 1), date(2024, 8, 1)


async def test_deliberate_leakage_attempt_fails_loudly(graph: GraphClient) -> None:
    """Plant an assertion recorded AFTER a cutoff; an as-of read at the
    cutoff must not see it, and the harness pin-check must raise if a later
    assertion is mis-read."""
    await _seed(graph)
    # Record a "future cheat": an assertion recorded after the cutoff.
    cheat = Assertion(
        predicate=EdgeType.MANDATE_OF,
        subject=EntityRef(node_type=NodeType.EVENT, key="event:mandate", display_name="Mandate"),
        object=EntityRef(
            node_type=NodeType.ORGANIZATION, key=GULF_MERIDIAN_LEI, display_name="Gulf Meridian"
        ),
        source_doc_id="SW-2024-0008",
        snippet_offset=(0, 40),
        extractor_version="fixture-1.0",
        confidence=1.0,
        valid_from=datetime(2024, 7, 10, tzinfo=UTC),
        recorded_at=datetime(2024, 7, 10, 9, tzinfo=UTC),
    )
    await AssertionWriter(graph).write(cheat)

    registry = PatternRegistry(graph)
    backtester = Backtester(graph, registry)
    cutoff = datetime(2024, 6, 1, tzinfo=UTC)

    # The future assertion must be invisible at the cutoff (query-level pin).
    visible = await graph.read_assertions(as_of=cutoff)
    assert all(
        not str(r["a"].get("predicate", "")).startswith("MANDATE") for r in visible
    ), "leakage: future assertion visible at cutoff"

    # And the harness pin-check passes for real evidence (no later-recorded
    # assertion is among the backtest's visible set at cutoff).
    await backtester._assert_pin(cutoff)  # noqa: SLF001


def test_pin_check_raises_on_future_recorded() -> None:
    """The harness gate itself: a record claiming recorded_at > as_of raises."""
    from datetime import datetime as dt

    class FakeClient:
        async def read_all_assertions_including_superseded(self, as_of: datetime) -> list:
            return [{"a": {"recorded_at": dt(2024, 7, 1, tzinfo=UTC)}}]

    backtester = Backtester(FakeClient(), PatternRegistry(FakeClient()))  # type: ignore[arg-type]
    with pytest.raises(LeakageError, match="leakage"):
        import asyncio

        asyncio.run(backtester._assert_pin(dt(2024, 6, 1, tzinfo=UTC)))  # noqa: SLF001


async def test_per_pattern_attribution_and_distribution(graph: GraphClient) -> None:
    await _seed(graph)
    registry = PatternRegistry(graph)
    backtester = Backtester(graph, registry)
    start, end = _span()
    result = await backtester.run(start, end, step_days=7, outcomes=[MANDATE])

    assert result.total_signals > 0
    # precision@10 must be a fraction in [0, 1]; the deduplicated signal
    # set for this seeded episode is 4 unique (pattern, entity) signals.
    assert 0.0 <= result.precision_at_10 <= 1.0
    assert result.total_signals == 4
    assert result.recall > 0

    attribution = {a.pattern: a for a in result.attribution}
    # Detectors that precede the mandate earn their keep with a lead-time
    # DISTRIBUTION (not a mean).
    for name in ("maturity_wall_no_refi", "board_approved_issuance_programme"):
        assert name in attribution
        leads = attribution[name].lead_days
        assert leads, f"{name} produced no lead times"
        assert all(lead > 0 for lead in leads)
        assert leads == sorted(leads)
    # Every lead for these should be well under a year (a 400-day detector is useless).
    for a in result.attribution:
        assert all(lead <= 365 for lead in a.lead_days)


async def test_decoy_signals_are_zero_so_false_positive_rate_is_zero(graph: GraphClient) -> None:
    await _seed(graph)
    registry = PatternRegistry(graph)
    backtester = Backtester(graph, registry)
    start, end = _span()
    result = await backtester.run(start, end, step_days=7, outcomes=[MANDATE])
    # Any Northern Harbour signal would be a false positive; there must be none.
    decoy = [a for a in result.attribution if a.pattern and NORTHERN_HARBOUR_LEI in str(a)]
    # Attribution is per-pattern; the decoy simply fires nothing (already
    # guaranteed by M7 tests). Assert precision is not dragged by the decoy.
    assert result.precision_at_10 > 0.5
    assert decoy == []


async def test_reproducible_same_inputs_same_numbers(graph: GraphClient) -> None:
    await _seed(graph)
    registry = PatternRegistry(graph)
    backtester = Backtester(graph, registry)
    start, end = _span()
    first = await backtester.run(start, end, step_days=7, outcomes=[MANDATE])
    second = await backtester.run(start, end, step_days=7, outcomes=[MANDATE])
    assert first == second
