"""Pattern detector tests.

The criterion that matters most: ZERO signals fire for the Northern
Harbour decoy. A detector that fires on the decoy is broken even if it
also fires on the positive case — precision determines whether anyone
reads the second daily brief.
"""

import os
from datetime import UTC, datetime

import pytest

from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry
from fi_intel.graph.writer import AssertionWriter
from fi_intel.synth.episodes import (
    EPISODE_START,
    GULF_MERIDIAN,
    GULF_MERIDIAN_LEI,
    NORTHERN_HARBOUR_LEI,
    SIGNAL_DEADLINE_DAY,
)
from fi_intel.synth.graph_fixture import (
    gulf_meridian_assertions,
    northern_harbour_assertions,
)

NEO4J_URI = os.environ.get("FI_INTEL_TEST_NEO4J_URI")
pytestmark = pytest.mark.skipif(NEO4J_URI is None, reason="FI_INTEL_TEST_NEO4J_URI not set")

AS_OF = datetime(2024, 6, 1, tzinfo=UTC)


@pytest.fixture
async def graph():
    assert NEO4J_URI is not None
    client = GraphClient(NEO4J_URI, "neo4j", "fi_intel")
    await client.migrate()
    await client.delete_all()
    await client.migrate()
    yield client
    await client.delete_all()
    await client.close()


async def _seed(client: GraphClient, assertions: list) -> None:
    writer = AssertionWriter(client)
    for a in assertions:
        await writer.write(a)


async def test_every_expected_signal_fires_for_gulf_meridian(graph: GraphClient) -> None:
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph)
    signals = await registry.run(AS_OF)
    gm_fired = {s.pattern for s in signals if s.entity_key == GULF_MERIDIAN_LEI}
    expected = {s.pattern for s in GULF_MERIDIAN.expected_signals}
    assert expected <= gm_fired, f"missing: {expected - gm_fired}"


async def test_signals_fire_before_day_205(graph: GraphClient) -> None:
    """Lead-time discipline: signals fire before the mandate, within the window."""
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph)
    signals = await registry.run(AS_OF)
    assert signals, "expected signals at 2024-06-01"
    assert (AS_OF.date() - EPISODE_START).days < SIGNAL_DEADLINE_DAY
    # All Gulf Meridian expected signals present at this early as-of date.
    gm = {s.pattern for s in signals if s.entity_key == GULF_MERIDIAN_LEI}
    assert {s.pattern for s in GULF_MERIDIAN.expected_signals} <= gm


async def test_decoy_produces_zero_signals(graph: GraphClient) -> None:
    """The test that matters most."""
    await _seed(graph, northern_harbour_assertions())
    registry = PatternRegistry(graph)
    signals = await registry.run(AS_OF)
    decoy = [s for s in signals if s.entity_key == NORTHERN_HARBOUR_LEI]
    assert decoy == [], f"decoy fired: {[s.pattern for s in decoy]}"


async def test_decoy_silent_even_with_positive_episode_present(graph: GraphClient) -> None:
    """Both episodes in the graph: positive fires, decoy stays silent."""
    await _seed(graph, gulf_meridian_assertions() + northern_harbour_assertions())
    registry = PatternRegistry(graph)
    signals = await registry.run(AS_OF)
    assert any(s.entity_key == GULF_MERIDIAN_LEI for s in signals)
    assert not any(s.entity_key == NORTHERN_HARBOUR_LEI for s in signals)


async def test_patterns_independently_toggleable(graph: GraphClient) -> None:
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph)
    only_rating = await registry.run(AS_OF, enabled={"negative_rating_action_with_capital_decline"})
    assert {s.pattern for s in only_rating} == {"negative_rating_action_with_capital_decline"}
    none_enabled = await registry.run(AS_OF, enabled=set())
    assert none_enabled == []


async def test_each_pattern_independently(graph: GraphClient) -> None:
    """Each detector fires alone for the positive episode."""
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph)
    for expected in GULF_MERIDIAN.expected_signals:
        signals = await registry.run(AS_OF, enabled={expected.pattern})
        assert any(
            s.entity_key == GULF_MERIDIAN_LEI and s.pattern == expected.pattern
            for s in signals
        ), f"{expected.pattern} did not fire independently"


async def test_maturity_wall_requires_no_refinancing(graph: GraphClient) -> None:
    """A negative control: add a REFINANCES edge and the wall must go quiet."""
    from fi_intel.ontology.schema import Assertion, EntityRef
    from fi_intel.ontology.vocab import EdgeType, NodeType

    await _seed(graph, gulf_meridian_assertions())
    # Announced refinancing of the sukuk kills the maturity-wall signal.
    refi = Assertion(
        predicate=EdgeType.REFINANCES,
        subject=EntityRef(node_type=NodeType.PROGRAMME, key="prog:gm-emtn", display_name="EMTN"),
        object=EntityRef(node_type=NodeType.INSTRUMENT, key="XS0000000001", display_name="sukuk"),
        source_doc_id="SW-2024-0006",
        snippet_offset=(0, 20),
        extractor_version="fixture-1.0",
        confidence=0.9,
        valid_from=datetime(2024, 5, 20, tzinfo=UTC),
        recorded_at=datetime(2024, 5, 20, 9, tzinfo=UTC),
    )
    await AssertionWriter(graph).write(refi)
    registry = PatternRegistry(graph)
    signals = await registry.run(AS_OF, enabled={"maturity_wall_no_refi"})
    assert not any(s.entity_key == GULF_MERIDIAN_LEI for s in signals)


async def test_explain_returns_evidence(graph: GraphClient) -> None:
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph)
    signals = await registry.run(AS_OF, enabled={"board_approved_issuance_programme"})
    assert signals
    explained = await registry.explain(signals[0].signal_id)
    assert explained is not None
    assert explained.evidence.get("doc") == "SW-2024-0006"
    assert explained.entity_key == GULF_MERIDIAN_LEI


async def test_as_of_pinning_hides_future_facts(graph: GraphClient) -> None:
    """A signal must not fire at an as-of before its evidence was recorded."""
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph)
    early = await registry.run(datetime(2024, 1, 1, tzinfo=UTC))  # before any recording
    assert early == []
