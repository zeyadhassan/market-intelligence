"""Agent tests: end-to-end signal→Opportunity, insufficient-evidence, and
evidence validation. The reasoning model is stubbed; tests assert on the
constructed request and on evidence resolvability against the real corpus.
"""

import os
from datetime import UTC, datetime

import pytest

from fi_intel.agents.opportunity_research import (
    RESEARCH_PROMPT_VERSION,
    EvidenceCitationError,
    OpportunityResearcher,
    ResearchClaim,
    ResearchRequest,
    ResearchResponse,
)
from fi_intel.agents.validate import EvidenceValidationError, validate_opportunity
from fi_intel.governance.audit import InMemoryAuditLog
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
from fi_intel.sources.fixture import synthetic_wire
from fi_intel.synth.episodes import GULF_MERIDIAN_LEI
from fi_intel.synth.graph_fixture import gulf_meridian_assertions
from fi_intel.tools.evidence import Opportunity, OpportunityClaim
from fi_intel.tools.research_tools import ResearchTools, ToolContext

NEO4J_URI = os.environ.get("FI_INTEL_TEST_NEO4J_URI")
pytestmark = pytest.mark.skipif(NEO4J_URI is None, reason="FI_INTEL_TEST_NEO4J_URI not set")

AS_OF = datetime(2024, 6, 1, tzinfo=UTC)
PRINCIPAL = Principal(principal_id="banker", entitlement_group="test", side=Side.PUBLIC)
ACCESS = trusted_test_access(
    "synthetic_wire",
    principal_id=PRINCIPAL.principal_id,
    entitlement_group=PRINCIPAL.entitlement_group,
    side=PRINCIPAL.side,
)


class StubReasoningModel:
    """Records the request; returns a canned response citing shown evidence."""

    def __init__(self, response: ResearchResponse) -> None:
        self._response = response
        self.requests: list[ResearchRequest] = []

    async def research(self, request: ResearchRequest) -> ResearchResponse:
        self.requests.append(request)
        return self._response


@pytest.fixture
async def env():
    assert NEO4J_URI is not None
    # Graph with the positive episode.
    client = GraphClient(NEO4J_URI, "neo4j", "fi_intel")
    await client.migrate()
    await client.delete_all()
    await client.migrate()
    writer = AssertionWriter(client)
    for a in gulf_meridian_assertions():
        await writer.write(a)

    # Corpus with the documents (the evidence base).
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

    # Document store for evidence validation (resolvability).
    doc_store = InMemoryDocumentStore()
    await doc_store.commit_batch(docs, [], _cursor())

    yield client, registry, tools, doc_store
    await client.delete_all()
    await client.close()


def _cursor():
    from fi_intel.sources.base import FetchCursor

    return FetchCursor(source_id="synthetic_wire", position="12", updated_at=AS_OF)


async def test_end_to_end_signal_produces_opportunity_with_resolvable_evidence(env) -> None:
    client, registry, tools, doc_store = env
    signals = await registry.run(AS_OF, enabled={"board_approved_issuance_programme"})
    assert signals
    signal = signals[0]

    model = StubReasoningModel(
        ResearchResponse(
            title="EMTN programme signals upcoming issuance",
            falsifier="Programme lapses without any mandate within two quarters.",
            claims=[
                ResearchClaim(
                    text="Board-approved programme indicates readiness to issue.",
                    evidence_indices=[0, 1],
                    confidence=0.9,
                )
            ],
        )
    )
    researcher = OpportunityResearcher(tools, model, doc_store)
    opportunity, cited = await researcher.research_signal(signal)

    # The request carries the versioned prompt and only-shown evidence.
    (request,) = model.requests
    assert request.prompt_version == RESEARCH_PROMPT_VERSION
    assert request.entity_name == signal.entity_name
    assert request.signal_evidence == signal.evidence
    assert request.profile_predicates
    assert len(request.evidence_excerpts) >= len(cited) > 0

    # The opportunity cites real evidence that resolves against the corpus.
    assert not opportunity.insufficient_evidence
    assert opportunity.evidence_ids
    assert opportunity.claims[0].evidence_ids == opportunity.evidence_ids
    validated = await validate_opportunity(opportunity, doc_store, cited)
    assert validated is opportunity


async def test_insufficient_evidence_signal_returns_nothing(env) -> None:
    """A signal with no corroborating documents yields the blessed empty
    outcome, not a constructed narrative."""
    _, _, tools, doc_store = env
    from fi_intel.graph.registry import Signal

    # A synthetic signal for an entity with no corpus documents.
    orphan = Signal(
        signal_id="test:nonexistent:2024-06-01",
        pattern="maturity_wall_no_refi",
        entity_key="999999NONEXISTENT000000",
        entity_name="Nonexistent Bank",
        priority=80,
        fired_at=AS_OF,
        as_of=AS_OF,
        evidence={},
    )
    model = StubReasoningModel(
        ResearchResponse(title="x", claims=[], falsifier="z", insufficient_evidence=True)
    )
    researcher = OpportunityResearcher(tools, model, doc_store)
    opportunity, cited = await researcher.research_signal(orphan)

    assert opportunity.insufficient_evidence
    assert opportunity.evidence_ids == []
    assert cited == []
    # The model is never asked to narrate without evidence.
    assert model.requests == []
    # And it validates clean (nothing to resolve).
    await validate_opportunity(opportunity, doc_store)


async def test_unresolvable_evidence_id_fails_validation(env) -> None:
    _, _, _, doc_store = env
    bad = Opportunity(
        title="Fabricated claim",
        entity_key=GULF_MERIDIAN_LEI,
        summary="Cites a document that does not exist.",
        falsifier="f",
        evidence_ids=["synthetic_wire/SW-9999-9999:0-10"],  # no such doc
        claims=[
            OpportunityClaim(
                text="Cites a document that does not exist.",
                evidence_ids=["synthetic_wire/SW-9999-9999:0-10"],
                confidence=0.5,
            )
        ],
    )
    with pytest.raises(EvidenceValidationError, match="unresolvable evidence_id"):
        await validate_opportunity(bad, doc_store)


async def test_out_of_range_span_fails_validation(env) -> None:
    _, _, _, doc_store = env
    bad = Opportunity(
        title="Bad span",
        entity_key=GULF_MERIDIAN_LEI,
        summary="Span beyond the document length.",
        falsifier="f",
        evidence_ids=["synthetic_wire/SW-2024-0001:0-999999"],
        claims=[
            OpportunityClaim(
                text="Span beyond the document length.",
                evidence_ids=["synthetic_wire/SW-2024-0001:0-999999"],
                confidence=0.5,
            )
        ],
    )
    with pytest.raises(EvidenceValidationError, match="span out of range"):
        await validate_opportunity(bad, doc_store)


async def test_model_cannot_cite_unshown_evidence(env) -> None:
    """An out-of-bundle citation fails closed instead of being dropped."""
    _, registry, tools, _ = env
    signals = await registry.run(AS_OF, enabled={"board_approved_issuance_programme"})
    signal = signals[0]
    model = StubReasoningModel(
        ResearchResponse(
            title="t",
            falsifier="f",
            claims=[ResearchClaim(text="s", evidence_indices=[0, 999], confidence=0.5)],
        )
    )
    researcher = OpportunityResearcher(tools, model, InMemoryDocumentStore())
    with pytest.raises(EvidenceCitationError, match="invalid evidence indices"):
        await researcher.research_signal(signal)


async def test_model_cannot_use_negative_evidence_index(env) -> None:
    _, registry, tools, _ = env
    signals = await registry.run(AS_OF, enabled={"board_approved_issuance_programme"})
    model = StubReasoningModel(
        ResearchResponse(
            title="t",
            falsifier="f",
            claims=[ResearchClaim(text="s", evidence_indices=[-1], confidence=0.5)],
        )
    )
    researcher = OpportunityResearcher(tools, model, InMemoryDocumentStore())
    with pytest.raises(EvidenceCitationError, match=r"invalid evidence indices \[-1\]"):
        await researcher.research_signal(signals[0])
