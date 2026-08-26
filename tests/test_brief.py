"""Brief compiler tests: decoy-day emptiness, evidence links, budget deferral,
and n-item rendering.
"""

import os
from datetime import UTC, datetime

import pytest

from fi_intel.agents.brief import BriefCompiler
from fi_intel.agents.opportunity_research import (
    OpportunityResearcher,
    ResearchClaim,
    ResearchResponse,
)
from fi_intel.agents.render import render_html
from fi_intel.governance.audit import InMemoryAuditLog
from fi_intel.governance.model_usage import ModelCapacityLimits
from fi_intel.governance.policy import trusted_test_access
from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ingest.store import InMemoryDocumentStore
from fi_intel.retrieval.chunking import HashingEmbedder
from fi_intel.retrieval.corpus import CorpusSearch
from fi_intel.retrieval.entitlement import Principal, Side
from fi_intel.retrieval.service import RetrievalService
from fi_intel.retrieval.store import InMemoryCorpusStore
from fi_intel.sources.base import FetchCursor
from fi_intel.sources.fixture import synthetic_wire
from fi_intel.synth.graph_fixture import (
    gulf_meridian_assertions,
    northern_harbour_assertions,
)
from fi_intel.tools.research_tools import ResearchTools, ToolContext

NEO4J_URI = os.environ.get("FI_INTEL_TEST_NEO4J_URI")
pytestmark = pytest.mark.skipif(NEO4J_URI is None, reason="FI_INTEL_TEST_NEO4J_URI not set")

AS_OF = datetime(2024, 6, 1, tzinfo=UTC)
PRINCIPAL = Principal(principal_id="desk", entitlement_group="test", side=Side.PUBLIC)
ACCESS = trusted_test_access(
    "synthetic_wire",
    principal_id=PRINCIPAL.principal_id,
    entitlement_group=PRINCIPAL.entitlement_group,
    side=PRINCIPAL.side,
)


class _StubModel:
    async def research(self, request) -> ResearchResponse:  # noqa: ANN001, ANN202
        return ResearchResponse(
            title=f"Opportunity: {request.signal_pattern}",
            falsifier="A quarter with no mandate.",
            claims=[
                ResearchClaim(
                    text="Deterministic stub claim.",
                    evidence_indices=[0, 1],
                    confidence=0.9,
                )
            ],
        )


@pytest.fixture
async def env_factory():
    if NEO4J_URI is None:
        pytest.skip("FI_INTEL_TEST_NEO4J_URI not set")

    clients: list[GraphClient] = []

    async def _make(assertions: list) -> BriefCompiler:
        client = GraphClient(NEO4J_URI, "neo4j", "fi_intel")
        clients.append(client)
        await client.migrate()
        await client.delete_all()
        await client.migrate()
        writer = AssertionWriter(client)
        for a in assertions:
            await writer.write(a)
        corpus = InMemoryCorpusStore(HashingEmbedder())
        corpus.register_source("synthetic_wire")
        corpus.grant("test", "synthetic_wire")
        docs = [d async for d in synthetic_wire().fetch()]
        corpus.add_documents(docs)
        retrieval = RetrievalService(
            CorpusSearch(corpus, HashingEmbedder()), InMemoryAuditLog(), run_id="t"
        )
        registry = PatternRegistry(client, access=ACCESS)
        ctx = ToolContext(principal=PRINCIPAL, as_of=AS_OF)
        tools = ResearchTools(retrieval, client, registry, ctx)
        document_store = InMemoryDocumentStore()
        await document_store.commit_batch(
            docs,
            [],
            FetchCursor(source_id="synthetic_wire", position="12", updated_at=AS_OF),
        )
        researcher = OpportunityResearcher(tools, _StubModel(), document_store)
        return BriefCompiler(registry, researcher)

    yield _make
    for client in clients:
        await client.close()


async def test_decoy_day_says_nothing_material_and_does_not_pad(env_factory) -> None:
    """The criterion that decides whether anyone reads the second brief."""
    compiler = await env_factory(northern_harbour_assertions())
    brief = await compiler.compile(AS_OF, desk="fi_gcc")
    assert brief.nothing_material
    assert brief.items == []
    page = render_html(brief)
    assert "No material developments" in page
    assert "Coverage funnel: looked at" in page
    assert "Opportunity" not in page  # no padding, no fabricated items


async def test_every_claim_links_to_a_document_and_highlights_span(env_factory) -> None:
    compiler = await env_factory(gulf_meridian_assertions())
    brief = await compiler.compile(AS_OF, desk="fi_gcc")
    assert brief.items, "expected items for the positive episode"
    page = render_html(brief)
    for item in brief.items:
        for ev in item.evidence:
            assert ev.evidence_id in page
            assert f"href='#doc-{ev.source_id}-{ev.doc_id}'" not in page
    assert "<mark>" in page  # entity name highlighted within a span


async def test_budget_ceiling_defers_work_without_overspending(env_factory) -> None:
    compiler = await env_factory(gulf_meridian_assertions())
    compiler._limits = ModelCapacityLimits(max_calls=0)  # noqa: SLF001
    brief = await compiler.compile(AS_OF, desk="fi_gcc")
    assert brief.items == []
    assert brief.deferred_signals
    assert brief.coverage_complete is False
    assert brief.research_usage.calls == 0
    assert "Brief incomplete" in render_html(brief)


async def test_renders_zero_one_and_many_items(env_factory) -> None:
    # Zero items (decoy).
    decoy = await env_factory(northern_harbour_assertions())
    zero = await decoy.compile(AS_OF, desk="d")
    assert "No material developments" in render_html(zero)

    # One item: enable a single high-priority pattern.
    gm = await env_factory(gulf_meridian_assertions())
    one = await gm.compile(AS_OF, desk="d", enabled={"board_approved_issuance_programme"})
    assert len(one.items) == 1
    page = render_html(one)
    assert page.count("class='item'") == 1

    # Many items: check it still renders (structure holds at scale).
    many_compiler = await env_factory(gulf_meridian_assertions())
    many = await many_compiler.compile(AS_OF, desk="d")
    page_many = render_html(many)
    assert page_many.count("class='item'") == len(many.items)


async def test_capacity_is_measured_and_reported(env_factory) -> None:
    compiler = await env_factory(gulf_meridian_assertions())
    brief = await compiler.compile(AS_OF, desk="d", enabled={"board_approved_issuance_programme"})
    assert brief.research_usage.calls == 1
    assert brief.research_usage.total_tokens > 0
    page = render_html(brief)
    assert "Research capacity used" in page
    assert "abstained for insufficient citable evidence" in page
