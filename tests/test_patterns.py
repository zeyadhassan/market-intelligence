"""Pattern detector tests.

The criterion that matters most: ZERO signals fire for the Northern
Harbour decoy. A detector that fires on the decoy is broken even if it
also fires on the positive case — precision determines whether anyone
reads the second daily brief.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest

from fi_intel.governance.policy import trusted_test_access
from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry
from fi_intel.graph.signals import SignalLifecycleState
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ontology.vocab import EdgeType
from fi_intel.sources.canonical import BarrierSide
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
ACCESS = trusted_test_access("synthetic_wire")


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


async def _signal_count(client: GraphClient) -> int:
    async with client._driver.session() as session:  # noqa: SLF001
        result = await session.run("MATCH (s:Signal) RETURN count(s) AS n")
        row = await result.single()
        return int(row["n"])


async def test_every_expected_signal_fires_for_gulf_meridian(graph: GraphClient) -> None:
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    signals = await registry.run(AS_OF)
    gm_fired = {s.pattern for s in signals if s.entity_key == GULF_MERIDIAN_LEI}
    expected = {s.pattern for s in GULF_MERIDIAN.expected_signals}
    assert expected <= gm_fired, f"missing: {expected - gm_fired}"


async def test_signals_fire_before_day_205(graph: GraphClient) -> None:
    """Lead-time discipline: signals fire before the mandate, within the window."""
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    signals = await registry.run(AS_OF)
    assert signals, "expected signals at 2024-06-01"
    assert (AS_OF.date() - EPISODE_START).days < SIGNAL_DEADLINE_DAY
    # All Gulf Meridian expected signals present at this early as-of date.
    gm = {s.pattern for s in signals if s.entity_key == GULF_MERIDIAN_LEI}
    assert {s.pattern for s in GULF_MERIDIAN.expected_signals} <= gm


async def test_decoy_produces_zero_signals(graph: GraphClient) -> None:
    """The test that matters most."""
    await _seed(graph, northern_harbour_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    signals = await registry.run(AS_OF)
    decoy = [s for s in signals if s.entity_key == NORTHERN_HARBOUR_LEI]
    assert decoy == [], f"decoy fired: {[s.pattern for s in decoy]}"


async def test_decoy_silent_even_with_positive_episode_present(graph: GraphClient) -> None:
    """Both episodes in the graph: positive fires, decoy stays silent."""
    await _seed(graph, gulf_meridian_assertions() + northern_harbour_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    signals = await registry.run(AS_OF)
    assert any(s.entity_key == GULF_MERIDIAN_LEI for s in signals)
    assert not any(s.entity_key == NORTHERN_HARBOUR_LEI for s in signals)


async def test_patterns_independently_toggleable(graph: GraphClient) -> None:
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    only_rating = await registry.run(AS_OF, enabled={"negative_rating_action_with_capital_decline"})
    assert {s.pattern for s in only_rating} == {"negative_rating_action_with_capital_decline"}
    none_enabled = await registry.run(AS_OF, enabled=set())
    assert none_enabled == []


async def test_each_pattern_independently(graph: GraphClient) -> None:
    """Each detector fires alone for the positive episode."""
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    for expected in GULF_MERIDIAN.expected_signals:
        signals = await registry.run(AS_OF, enabled={expected.pattern})
        assert any(
            s.entity_key == GULF_MERIDIAN_LEI and s.pattern == expected.pattern for s in signals
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
        source_id="synthetic_wire",
        source_doc_id="SW-2024-0006",
        barrier_side=BarrierSide.PUBLIC,
        policy_version="fixture-policy-v1",
        snippet_offset=(0, 20),
        extractor_version="fixture-1.0",
        confidence=0.9,
        valid_from=datetime(2024, 5, 20, tzinfo=UTC),
        recorded_at=datetime(2024, 5, 20, 9, tzinfo=UTC),
    )
    await AssertionWriter(graph).write(refi)
    registry = PatternRegistry(graph, access=ACCESS)
    signals = await registry.run(AS_OF, enabled={"maturity_wall_no_refi"})
    assert not any(s.entity_key == GULF_MERIDIAN_LEI for s in signals)


async def test_refinancing_by_another_entity_does_not_suppress_this_ones_signal(
    graph: GraphClient,
) -> None:
    """The 'no refinancing' check must be scoped to the maturing instrument,
    not the whole graph: another organization refinancing an unrelated
    instrument must not silence Gulf Meridian's own maturity-wall signal."""
    from fi_intel.ontology.schema import Assertion, EntityRef
    from fi_intel.ontology.vocab import EdgeType, NodeType

    await _seed(graph, gulf_meridian_assertions() + northern_harbour_assertions())
    # Northern Harbour refinances some unrelated instrument of its own.
    refi = Assertion(
        predicate=EdgeType.REFINANCES,
        subject=EntityRef(node_type=NodeType.PROGRAMME, key="prog:nh-emtn", display_name="NH EMTN"),
        object=EntityRef(
            node_type=NodeType.INSTRUMENT, key="XS_NH_UNRELATED", display_name="NH bond"
        ),
        source_id="synthetic_wire",
        source_doc_id="SW-2024-0009",
        barrier_side=BarrierSide.PUBLIC,
        policy_version="fixture-policy-v1",
        snippet_offset=(0, 20),
        extractor_version="fixture-1.0",
        confidence=0.9,
        valid_from=datetime(2024, 5, 20, tzinfo=UTC),
        recorded_at=datetime(2024, 5, 20, 9, tzinfo=UTC),
    )
    await AssertionWriter(graph).write(refi)
    registry = PatternRegistry(graph, access=ACCESS)
    signals = await registry.run(AS_OF, enabled={"maturity_wall_no_refi"})
    assert any(s.entity_key == GULF_MERIDIAN_LEI for s in signals), (
        "an unrelated entity's refinancing must not silence Gulf Meridian's signal"
    )


async def test_explain_returns_evidence(graph: GraphClient) -> None:
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    signals = await registry.run(AS_OF, enabled={"board_approved_issuance_programme"})
    assert signals
    explained = await registry.explain(signals[0].signal_id)
    assert explained is not None
    assert explained.evidence.get("doc") == "SW-2024-0006"
    assert explained.entity_key == GULF_MERIDIAN_LEI
    assert (
        await registry.explain(
            signals[0].signal_id,
            as_of=datetime(2024, 5, 31, tzinfo=UTC),
        )
        is None
    )


async def test_as_of_pinning_hides_future_facts(graph: GraphClient) -> None:
    """A signal must not fire at an as-of before its evidence was recorded."""
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    early = await registry.run(datetime(2024, 1, 1, tzinfo=UTC))  # before any recording
    assert early == []


async def test_stable_identity_suppresses_unchanged_repeat_and_keeps_observations(
    graph: GraphClient,
) -> None:
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    first = await registry.run(AS_OF, enabled={"board_approved_issuance_programme"})
    assert len(first) == 1

    repeated = await registry.run(
        AS_OF + timedelta(days=1),
        enabled={"board_approved_issuance_programme"},
    )
    assert repeated == []
    explained = await registry.explain(first[0].signal_id)
    assert explained is not None
    assert explained.lifecycle_state is SignalLifecycleState.UNCHANGED
    assert explained.opened_at == AS_OF
    assert explained.last_confirmed_at == AS_OF + timedelta(days=1)
    assert explained.matched_assertion_ids
    assert explained.score_contributions

    async with graph._driver.session() as session:  # noqa: SLF001
        result = await session.run(
            """
            MATCH (s:Signal {signal_id: $id})-[:HAS_OBSERVATION]->(o)
            OPTIONAL MATCH (o)-[:SUPPORTED_BY]->(a:Assertion)
            RETURN count(DISTINCT o) AS observations,
                   collect(DISTINCT a.assertion_id) AS assertion_ids,
                   collect(DISTINCT o.assertion_ids) AS observation_assertion_ids
            """,
            id=first[0].signal_id,
        )
        row = await result.single()
    assert row["observations"] == 2
    assert set(row["assertion_ids"]) == set(first[0].matched_assertion_ids)
    assert all(
        set(assertion_ids) == set(first[0].matched_assertion_ids)
        for assertion_ids in row["observation_assertion_ids"]
    )


async def test_new_evidence_strengthens_same_signal_and_preserves_history(
    graph: GraphClient,
) -> None:
    base = next(
        assertion
        for assertion in gulf_meridian_assertions()
        if assertion.predicate is EdgeType.PROGRAMME_APPROVED_BY
    )
    original = base.model_copy(update={"properties": {**base.properties, "limit_usd_bn": "0.5"}})
    writer = AssertionWriter(graph)
    original_id = await writer.write(original)
    registry = PatternRegistry(graph, access=ACCESS)
    first = await registry.run(AS_OF, enabled={"board_approved_issuance_programme"})
    assert len(first) == 1

    changed_at = AS_OF + timedelta(days=1)
    stronger = original.model_copy(
        update={
            "recorded_at": changed_at,
            "properties": {**original.properties, "limit_usd_bn": "1.5"},
        }
    )
    stronger_id = await writer.correct(original, stronger, changed_at)
    second = await registry.run(
        changed_at,
        enabled={"board_approved_issuance_programme"},
    )

    assert len(second) == 1
    assert second[0].signal_id == first[0].signal_id
    assert second[0].lifecycle_state is SignalLifecycleState.STRENGTHENED
    assert second[0].matched_assertion_ids == (stronger_id,)
    historical = await registry.explain(first[0].signal_id, as_of=AS_OF)
    assert historical is not None
    assert historical.matched_assertion_ids == (original_id,)
    assert historical.lifecycle_state is SignalLifecycleState.NEW


async def test_material_freshness_change_weakens_same_signal(graph: GraphClient) -> None:
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    first = await registry.run(AS_OF, enabled={"board_approved_issuance_programme"})
    weakened = await registry.run(
        AS_OF + timedelta(days=100),
        enabled={"board_approved_issuance_programme"},
    )
    assert len(first) == len(weakened) == 1
    assert weakened[0].signal_id == first[0].signal_id
    assert weakened[0].lifecycle_state is SignalLifecycleState.WEAKENED
    assert weakened[0].opportunity_score < first[0].opportunity_score


async def test_missing_condition_resolves_existing_signal(graph: GraphClient) -> None:
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    first = await registry.run(AS_OF, enabled={"board_approved_issuance_programme"})
    assert first
    assert (
        await registry.run(
            AS_OF + timedelta(days=200),
            enabled={"board_approved_issuance_programme"},
        )
        == []
    )
    resolved = await registry.explain(first[0].signal_id)
    assert resolved is not None
    assert resolved.lifecycle_state is SignalLifecycleState.RESOLVED
    assert resolved.resolved_at == AS_OF + timedelta(days=200)


async def test_analyst_suppression_survives_later_confirmation(graph: GraphClient) -> None:
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    first = await registry.run(AS_OF, enabled={"board_approved_issuance_programme"})
    suppressed = await registry.suppress(
        first[0].signal_id,
        reason="Already covered by the desk.",
        at=AS_OF + timedelta(hours=1),
    )
    assert suppressed is not None
    assert suppressed.lifecycle_state is SignalLifecycleState.SUPPRESSED

    repeated = await registry.run(
        AS_OF + timedelta(days=1),
        enabled={"board_approved_issuance_programme"},
    )
    assert repeated == []
    explained = await registry.explain(first[0].signal_id)
    assert explained is not None
    assert explained.lifecycle_state is SignalLifecycleState.SUPPRESSED
    assert explained.analyst_reason == "Already covered by the desk."


async def test_read_only_evaluation_does_not_persist_signals(graph: GraphClient) -> None:
    await _seed(graph, gulf_meridian_assertions())
    registry = PatternRegistry(graph, access=ACCESS)
    signals = await registry.evaluate(
        AS_OF,
        enabled={"board_approved_issuance_programme"},
    )
    assert len(signals) == 1
    assert signals[0].matched_assertion_ids
    assert await _signal_count(graph) == 0
