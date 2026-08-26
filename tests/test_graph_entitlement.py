"""Adversarial graph authorization and provenance tests."""

import os
from datetime import UTC, datetime

import pytest

from fi_intel.governance.audit import InMemoryAuditLog
from fi_intel.governance.policy import GraphAccessContext, trusted_test_access
from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.retrieval.entitlement import Side
from fi_intel.sources.canonical import BarrierSide
from fi_intel.tools.research_tools import ResearchTools, ToolContext

NEO4J_URI = os.environ.get("FI_INTEL_TEST_NEO4J_URI")
pytestmark = pytest.mark.skipif(NEO4J_URI is None, reason="FI_INTEL_TEST_NEO4J_URI not set")

AS_OF = datetime(2024, 6, 1, tzinfo=UTC)
PUBLIC_SOURCE = "public_wire"
PRIVATE_SOURCE = "private_wire"


def _access(
    principal_id: str,
    *source_ids: str,
    side: Side = Side.PUBLIC,
    require_audit: bool = True,
) -> GraphAccessContext:
    return trusted_test_access(
        *source_ids,
        side=side,
        principal_id=principal_id,
        require_audit=require_audit,
    )


def _programme_assertion(
    source_id: str,
    doc_id: str,
    barrier_side: BarrierSide,
) -> Assertion:
    return Assertion(
        predicate=EdgeType.PROGRAMME_APPROVED_BY,
        subject=EntityRef(
            node_type=NodeType.PROGRAMME,
            key=f"programme:{source_id}",
            display_name=f"{source_id} programme",
        ),
        object=EntityRef(
            node_type=NodeType.ORGANIZATION,
            key="lei:entitlement-test",
            display_name="Entitlement Test Bank",
        ),
        source_id=source_id,
        source_doc_id=doc_id,
        barrier_side=barrier_side,
        policy_version="source-registry-v1",
        snippet_offset=(0, 20),
        extractor_version="test-1.0",
        confidence=0.95,
        valid_from=datetime(2024, 5, 1, tzinfo=UTC),
        recorded_at=datetime(2024, 5, 1, 1, tzinfo=UTC),
        properties={
            "limit_usd_bn": "1.0",
            "currency": "USD",
            "status": "approved",
            "marketed": "false",
        },
    )


@pytest.fixture
async def graph():
    assert NEO4J_URI is not None
    audit = InMemoryAuditLog()
    client = GraphClient(NEO4J_URI, "neo4j", "fi_intel", audit=audit)
    await client.migrate()
    await client.delete_all()
    await client.migrate()
    writer = AssertionWriter(client)
    await writer.write(_programme_assertion(PUBLIC_SOURCE, "public-doc", BarrierSide.PUBLIC))
    await writer.write(_programme_assertion(PRIVATE_SOURCE, "private-doc", BarrierSide.PRIVATE))
    yield client, audit
    await client.delete_all()
    await client.close()


async def test_graph_reads_enforce_source_grants_and_barrier_independently(graph) -> None:
    client, audit = graph
    public = _access("public-reader", PUBLIC_SOURCE, PRIVATE_SOURCE)
    private = _access("private-reader", PUBLIC_SOURCE, PRIVATE_SOURCE, side=Side.PRIVATE)
    source_restricted = _access("source-reader", PUBLIC_SOURCE, side=Side.PRIVATE)

    public_rows = await client.read_assertions(AS_OF, public)
    private_rows = await client.read_assertions(AS_OF, private)
    restricted_rows = await client.read_assertions(AS_OF, source_restricted)

    assert {row["a"]["source_doc_id"] for row in public_rows} == {"public-doc"}
    assert {row["a"]["source_doc_id"] for row in private_rows} == {
        "public-doc",
        "private-doc",
    }
    assert {row["a"]["source_doc_id"] for row in restricted_rows} == {"public-doc"}
    public_audit_docs = {
        event.doc_id for event in audit.events if event.principal == "public-reader"
    }
    assert public_audit_docs == {"public-doc"}


async def test_patterns_explain_and_research_tools_cannot_cross_barrier(graph) -> None:
    client, _ = graph
    public = _access("public-tools", PUBLIC_SOURCE, PRIVATE_SOURCE)
    private = _access("private-tools", PUBLIC_SOURCE, PRIVATE_SOURCE, side=Side.PRIVATE)
    public_registry = PatternRegistry(client, access=public)
    private_registry = PatternRegistry(client, access=private)

    private_signals = await private_registry.run(
        AS_OF, enabled={"board_approved_issuance_programme"}
    )
    private_only = next(
        signal for signal in private_signals if signal.barrier_side is BarrierSide.PRIVATE
    )
    assert private_only.source_ids == (PRIVATE_SOURCE,)
    assert await public_registry.explain(private_only.signal_id) is None
    assert await private_registry.explain(private_only.signal_id) == private_only

    tools = ResearchTools(
        None,  # type: ignore[arg-type]  # corpus retrieval is not used in this test
        client,
        public_registry,
        ToolContext(principal=public.principal, as_of=AS_OF),
    )
    results = await tools.graph_query("board_approved_issuance_programme")
    profile = await tools.entity_profile(f"programme:{PRIVATE_SOURCE}")
    assert len(results) == 1
    assert results[0]["doc"] == "public-doc"
    assert profile["assertion_count"] == 0


async def test_protected_graph_read_without_audit_sink_fails_closed(graph) -> None:
    _, _ = graph
    assert NEO4J_URI is not None
    unaudited = GraphClient(NEO4J_URI, "neo4j", "fi_intel")
    access = _access("must-audit", PUBLIC_SOURCE)
    try:
        with pytest.raises(RuntimeError, match="requires an audit log"):
            await unaudited.read_assertions(AS_OF, access)
    finally:
        await unaudited.close()
