"""Graph writer tests: idempotency, supersede-with-history, required fields.

Run live against Neo4j when FI_INTEL_TEST_NEO4J_URI is set; skipped
otherwise. The graph is a store with real semantics (constraints, temporal
predicates), so these tests belong against the real thing, not a fake.
"""

import os
from datetime import UTC, datetime

import pytest

from fi_intel.graph.client import GraphClient
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import EdgeType, NodeType

NEO4J_URI = os.environ.get("FI_INTEL_TEST_NEO4J_URI")
pytestmark = pytest.mark.skipif(NEO4J_URI is None, reason="FI_INTEL_TEST_NEO4J_URI not set")

GULF_MERIDIAN = EntityRef(
    node_type=NodeType.ORGANIZATION,
    key="213800GMBQPSC000000001",
    display_name="Gulf Meridian Bank Q.P.S.C.",
)
SUKUK = EntityRef(
    node_type=NodeType.INSTRUMENT, key="XS0000000001", display_name="USD 500m sukuk"
)
MATURITY_EVENT = EntityRef(
    node_type=NodeType.EVENT, key="event:sukuk-maturity-2025-05-14", display_name="Sukuk maturity"
)

T1 = datetime(2024, 5, 15, 8, tzinfo=UTC)
T2 = datetime(2024, 5, 16, 8, tzinfo=UTC)


def _assertion(recorded_at: datetime, doc_id: str = "SW-2024-0007", offset=(0, 40)) -> Assertion:
    return Assertion(
        predicate=EdgeType.MATURES_ON,
        subject=SUKUK,
        object=MATURITY_EVENT,
        source_doc_id=doc_id,
        snippet_offset=offset,
        extractor_version="test-1.0",
        confidence=0.95,
        valid_from=datetime(2019, 5, 14, tzinfo=UTC),
        recorded_at=recorded_at,
        properties={"maturity_date": "2025-05-14"},
    )


@pytest.fixture
async def graph():
    assert NEO4J_URI is not None
    client = GraphClient(NEO4J_URI, "neo4j", "fi_intel")
    await client.migrate()
    await client.delete_all()
    await client.migrate()  # recreate constraints after wipe
    yield client
    await client.delete_all()
    await client.close()


async def test_write_is_idempotent(graph: GraphClient) -> None:
    writer = AssertionWriter(graph)
    a = _assertion(T1)
    id1 = await writer.write(a)
    id2 = await writer.write(a)
    assert id1 == id2
    assert await graph.assertion_count() == 1


async def test_correction_supersedes_and_preserves_history(graph: GraphClient) -> None:
    writer = AssertionWriter(graph)
    old = _assertion(T1)
    old_id = await writer.write(old)

    # Correction: the maturity date was misreported; fix valid content.
    new = Assertion(
        predicate=EdgeType.MATURES_ON,
        subject=SUKUK,
        object=EntityRef(
            node_type=NodeType.EVENT,
            key="event:sukuk-maturity-2025-05-15",
            display_name="Sukuk maturity (corrected)",
        ),
        source_doc_id="SW-2024-0007",
        snippet_offset=(0, 40),
        extractor_version="test-1.0",
        confidence=0.97,
        valid_from=datetime(2019, 5, 14, tzinfo=UTC),
        recorded_at=T2,
        properties={"maturity_date": "2025-05-15"},
    )
    new_id = await writer.correct(old, new, corrected_at=T2)
    assert new_id != old_id

    # Both remain queryable in full history.
    all_rows = await graph.read_all_assertions_including_superseded(as_of=T2)
    assert {r["a"]["assertion_id"] for r in all_rows} == {old_id, new_id}

    # At T1 (before the correction) the old assertion is the visible one.
    visible_t1 = await graph.read_assertions(as_of=T1)
    assert {r["a"]["assertion_id"] for r in visible_t1} == {old_id}

    # At T2 the new one is visible and the old is superseded.
    visible_t2 = await graph.read_assertions(as_of=T2)
    assert {r["a"]["assertion_id"] for r in visible_t2} == {new_id}


async def test_cannot_supersede_unknown_assertion(graph: GraphClient) -> None:
    writer = AssertionWriter(graph)
    ghost = _assertion(T1, doc_id="SW-2024-0007", offset=(50, 60))
    with pytest.raises(ValueError, match="unknown assertion"):
        await writer.correct(ghost, _assertion(T2), corrected_at=T2)
