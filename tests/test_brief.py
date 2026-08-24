"""Brief compiler tests: decoy-day emptiness, evidence links, budget abort,
and n-item rendering.
"""

import os
from datetime import UTC, datetime

import pytest

from fi_intel.agents.brief import BriefCompiler, BudgetExceededError
from fi_intel.agents.opportunity_research import OpportunityResearcher, ResearchResponse
from fi_intel.agents.render import render_html
from fi_intel.governance.audit import InMemoryAuditLog
from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry
from fi_intel.graph.writer import AssertionWriter
from fi_intel.retrieval.chunking import HashingEmbedder
from fi_intel.retrieval.corpus import CorpusSearch
from fi_intel.retrieval.entitlement import Principal, Side
from fi_intel.retrieval.service import RetrievalService
from fi_intel.retrieval.store import InMemoryCorpusStore
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


class _StubModel:
    def __init__(self, n_evidence: int) -> None:
        self._n = n_evidence

    async def research(self, request) -> ResearchResponse:  # noqa: ANN001, ANN202
        return ResearchResponse(
            title=f"Opportunity: {request.signal_pattern}",
            summary="Deterministic stub summary.",
            falsifier="A quarter with no mandate.",
            evidence_indices=[0, 1],
        )


@pytest.fixture
async def env_factory():
    if NEO4J_URI is None:
        pytest.skip("FI_INTEL_TEST_NEO4J_URI not set")

    async def _make(assertions: list) -> BriefCompiler:
        client = GraphClient(NEO4J_URI, "neo4j", "fi_intel")
        await client.migrate()
        await client.delete_all()
        await client.migrate()
        writer = AssertionWriter(client)
        for a in assertions:
            await writer.write(a)
        corpus = InMemoryCorpusStore(HashingEmbedder())
        corpus.register_source("synthetic_wire")
        corpus.grant("test", "synthetic_wire")
        corpus.add_documents([d async for d in synthetic_wire().fetch()])
        retrieval = RetrievalService(
            CorpusSearch(corpus, HashingEmbedder()), InMemoryAuditLog(), run_id="t"
        )
        registry = PatternRegistry(client)
        ctx = ToolContext(principal=PRINCIPAL, as_of=AS_OF)
        tools = ResearchTools(retrieval, client, registry, ctx)
        researcher = OpportunityResearcher(tools, _StubModel(2))
        # Attach client for teardown
        compiler = BriefCompiler(registry, tools, researcher)
        compiler._client = client  # noqa: SLF001
        return compiler

    yield _make


def _close(compiler: BriefCompiler) -> None:
    pass  # in-memory stores need no teardown; graph client closed in fixture teardown


async def test_decoy_day_says_nothing_material_and_does_not_pad(env_factory) -> None:
    """The criterion that decides whether anyone reads the second brief."""
    compiler = await env_factory(northern_harbour_assertions())
    brief = await compiler.compile(AS_OF, desk="fi_gcc")
    assert brief.nothing_material
    assert brief.items == []
    page = render_html(brief)
    assert "No material developments" in page
    assert "Opportunity" not in page  # no padding, no fabricated items


async def test_every_claim_links_to_a_document_and_highlights_span(env_factory) -> None:
    compiler = await env_factory(gulf_meridian_assertions())
    brief = await compiler.compile(AS_OF, desk="fi_gcc")
    assert brief.items, "expected items for the positive episode"
    page = render_html(brief)
    for item in brief.items:
        for ev in item.evidence:
            assert f"href='#doc-{ev.source_id}-{ev.doc_id}'" in page
            assert ev.evidence_id in page
    assert "<mark>" in page  # entity name highlighted within a span


async def test_budget_ceiling_aborts_instead_of_overspending(env_factory) -> None:
    compiler = await env_factory(gulf_meridian_assertions())
    compiler._ceiling = 100.0  # noqa: SLF001  # one signal costs 200
    with pytest.raises(BudgetExceededError):
        await compiler.compile(AS_OF, desk="fi_gcc")


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


async def test_cost_is_logged_and_reported(env_factory) -> None:
    compiler = await env_factory(gulf_meridian_assertions())
    brief = await compiler.compile(AS_OF, desk="d", enabled={"board_approved_issuance_programme"})
    assert brief.deep_research_cost > 0
    page = render_html(brief)
    assert "Deep-research cost" in page
