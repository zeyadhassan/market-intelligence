"""Temporal leakage tests and required-field validation.

The leakage test is the one that matters: an assertion recorded at T+1
must be invisible to an as-of read at T. A backtest that can see the
outcome is measuring hindsight.
"""

import os
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fi_intel.governance.policy import trusted_test_access
from fi_intel.graph.client import GraphClient
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.sources.canonical import BarrierSide

NEO4J_URI = os.environ.get("FI_INTEL_TEST_NEO4J_URI")

ORG = EntityRef(
    node_type=NodeType.ORGANIZATION, key="213800GMBQPSC000000001", display_name="Gulf Meridian"
)
RATING = EntityRef(node_type=NodeType.RATING, key="rating:gm-a-minus", display_name="A- rating")

T = datetime(2024, 3, 1, 0, tzinfo=UTC)
ACCESS = trusted_test_access("synthetic_wire")


def _kwargs(**overrides: object) -> dict:
    base: dict = {
        "predicate": EdgeType.RATING_ACTION_ON,
        "subject": RATING,
        "object": ORG,
        "source_id": "synthetic_wire",
        "source_doc_id": "SW-2024-0001",
        "barrier_side": BarrierSide.PUBLIC,
        "policy_version": "test-policy-v1",
        "snippet_offset": (0, 20),
        "extractor_version": "test-1.0",
        "confidence": 0.9,
        "valid_from": datetime(2024, 1, 15, tzinfo=UTC),
        "recorded_at": T,
    }
    base.update(overrides)
    return base


# --- Required-field validation ---


@pytest.mark.parametrize(
    "field",
    ["source_id", "source_doc_id", "barrier_side", "policy_version"],
)
def test_missing_provenance_field_raises(field: str) -> None:
    kwargs = _kwargs()
    del kwargs[field]
    with pytest.raises(ValidationError):
        Assertion(**kwargs)


def test_missing_valid_from_raises() -> None:
    kwargs = _kwargs()
    del kwargs["valid_from"]
    with pytest.raises(ValidationError):
        Assertion(**kwargs)


def test_missing_recorded_at_raises() -> None:
    kwargs = _kwargs()
    del kwargs["recorded_at"]
    with pytest.raises(ValidationError):
        Assertion(**kwargs)


def test_empty_source_doc_id_raises() -> None:
    with pytest.raises(ValidationError):
        Assertion(**_kwargs(source_doc_id=""))


def test_barrier_reclassification_creates_a_distinct_assertion_identity() -> None:
    public = Assertion(**_kwargs())
    private = Assertion(
        **_kwargs(
            barrier_side=BarrierSide.PRIVATE,
            policy_version="test-policy-v2",
        )
    )
    assert public.assertion_id() != private.assertion_id()


def test_typed_fact_change_creates_a_distinct_assertion_identity() -> None:
    stable = Assertion(**_kwargs(properties={"direction": "flat"}))
    declining = Assertion(**_kwargs(properties={"direction": "down"}))
    assert stable.assertion_id() != declining.assertion_id()


def test_extractor_version_creates_a_distinct_assertion_identity() -> None:
    stable = Assertion(**_kwargs())
    upgraded = stable.model_copy(update={"extractor_version": "test-2.0"})
    assert stable.assertion_id() != upgraded.assertion_id()


# --- Leakage: as-of reads cannot see the future (live Neo4j) ---


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


async def test_assertion_recorded_after_cutoff_is_invisible(graph: GraphClient) -> None:
    writer = AssertionWriter(graph)
    before = Assertion(**_kwargs(recorded_at=T))
    after = Assertion(
        **_kwargs(
            recorded_at=datetime(2024, 3, 2, tzinfo=UTC),  # T + 1 day
            source_doc_id="SW-2024-0004",
            snippet_offset=(30, 50),
        )
    )
    await writer.write(before)
    await writer.write(after)

    visible_at_t = await graph.read_assertions(as_of=T, access=ACCESS)
    assert len(visible_at_t) == 1
    assert visible_at_t[0]["a"]["source_doc_id"] == "SW-2024-0001"

    # Full history at T also excludes the future assertion.
    history_at_t = await graph.read_all_assertions_including_superseded(as_of=T, access=ACCESS)
    assert len(history_at_t) == 1

    # At T+1 both are visible.
    visible_later = await graph.read_assertions(
        as_of=datetime(2024, 3, 2, 1, tzinfo=UTC), access=ACCESS
    )
    assert len(visible_later) == 2


async def test_subject_filter_keeps_temporal_pin(graph: GraphClient) -> None:
    """A filtered read must not widen the as-of window."""
    writer = AssertionWriter(graph)
    await writer.write(Assertion(**_kwargs(recorded_at=T)))
    await writer.write(
        Assertion(
            **_kwargs(
                recorded_at=datetime(2024, 6, 1, tzinfo=UTC),
                source_doc_id="SW-2024-0008",
                snippet_offset=(0, 10),
            )
        )
    )
    rows = await graph.read_assertions(as_of=T, access=ACCESS, subject_key=RATING.key)
    assert len(rows) == 1
    assert rows[0]["a"]["source_doc_id"] == "SW-2024-0001"


async def test_late_arriving_state_does_not_rewrite_prior_knowledge(graph: GraphClient) -> None:
    """A state learned in March cannot change what was knowable in February."""

    writer = AssertionWriter(graph)
    january = Assertion(
        **_kwargs(
            recorded_at=datetime(2024, 1, 2, tzinfo=UTC),
            valid_from=datetime(2024, 1, 1, tzinfo=UTC),
            properties={"rating_type": "outlook", "direction": "stable"},
        )
    )
    february_state_learned_in_march = Assertion(
        **_kwargs(
            source_doc_id="SW-2024-0009",
            snippet_offset=(30, 60),
            recorded_at=datetime(2024, 3, 1, tzinfo=UTC),
            valid_from=datetime(2024, 2, 1, tzinfo=UTC),
            properties={"rating_type": "outlook", "direction": "negative"},
        )
    )
    await writer.write(january)
    await writer.write(february_state_learned_in_march)

    known_in_february = await graph.read_assertions(
        as_of=datetime(2024, 2, 15, tzinfo=UTC), access=ACCESS
    )
    known_in_march = await graph.read_assertions(
        as_of=datetime(2024, 3, 2, tzinfo=UTC), access=ACCESS
    )

    assert {row["a"]["assertion_id"] for row in known_in_february} == {january.assertion_id()}
    assert {row["a"]["assertion_id"] for row in known_in_march} == {
        february_state_learned_in_march.assertion_id()
    }


async def test_state_projection_is_independent_of_replay_order(graph: GraphClient) -> None:
    writer = AssertionWriter(graph)
    older = Assertion(
        **_kwargs(
            recorded_at=datetime(2024, 1, 2, tzinfo=UTC),
            valid_from=datetime(2024, 1, 1, tzinfo=UTC),
            properties={"rating_type": "outlook", "direction": "stable"},
        )
    )
    newer = Assertion(
        **_kwargs(
            source_doc_id="SW-2024-0010",
            snippet_offset=(40, 70),
            recorded_at=datetime(2024, 2, 2, tzinfo=UTC),
            valid_from=datetime(2024, 2, 1, tzinfo=UTC),
            properties={"rating_type": "outlook", "direction": "negative"},
        )
    )
    await writer.write(newer)
    await writer.write(older)

    visible = await graph.read_assertions(as_of=datetime(2024, 2, 3, tzinfo=UTC), access=ACCESS)
    assert {row["a"]["assertion_id"] for row in visible} == {newer.assertion_id()}
