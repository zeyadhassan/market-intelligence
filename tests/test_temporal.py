"""Temporal leakage tests (invariant 10) and required-field validation.

The leakage test is the one that matters: an assertion recorded at T+1
must be invisible to an as-of read at T. A backtest that can see the
outcome is measuring hindsight.
"""

import os
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fi_intel.graph.client import GraphClient
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import EdgeType, NodeType

NEO4J_URI = os.environ.get("FI_INTEL_TEST_NEO4J_URI")

ORG = EntityRef(
    node_type=NodeType.ORGANIZATION, key="213800GMBQPSC000000001", display_name="Gulf Meridian"
)
RATING = EntityRef(node_type=NodeType.RATING, key="rating:gm-a-minus", display_name="A- rating")

T = datetime(2024, 3, 1, 0, tzinfo=UTC)


def _kwargs(**overrides: object) -> dict:
    base: dict = {
        "predicate": EdgeType.RATING_ACTION_ON,
        "subject": RATING,
        "object": ORG,
        "source_doc_id": "SW-2024-0001",
        "snippet_offset": (0, 20),
        "extractor_version": "test-1.0",
        "confidence": 0.9,
        "valid_from": datetime(2024, 1, 15, tzinfo=UTC),
        "recorded_at": T,
    }
    base.update(overrides)
    return base


# --- Required-field validation: each missing field must raise (M5 criterion 4) ---

def test_missing_source_doc_id_raises() -> None:
    kwargs = _kwargs()
    del kwargs["source_doc_id"]
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

    visible_at_t = await graph.read_assertions(as_of=T)
    assert len(visible_at_t) == 1
    assert visible_at_t[0]["a"]["source_doc_id"] == "SW-2024-0001"

    # Full history at T also excludes the future assertion.
    history_at_t = await graph.read_all_assertions_including_superseded(as_of=T)
    assert len(history_at_t) == 1

    # At T+1 both are visible.
    visible_later = await graph.read_assertions(as_of=datetime(2024, 3, 2, 1, tzinfo=UTC))
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
    rows = await graph.read_assertions(as_of=T, subject_key=RATING.key)
    assert len(rows) == 1
    assert rows[0]["a"]["source_doc_id"] == "SW-2024-0001"
