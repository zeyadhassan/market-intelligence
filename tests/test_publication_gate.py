"""Service-free tests for the fail-closed publication boundary."""

from datetime import UTC, datetime, timedelta

import pytest

from fi_intel.agents.opportunity_research import (
    EvidenceCitationError,
    OpportunityResearcher,
    ResearchClaim,
    ResearchRequest,
    ResearchResponse,
)
from fi_intel.agents.validate import EvidenceValidationError, validate_opportunity
from fi_intel.governance.policy import GraphAccessContext, trusted_test_access
from fi_intel.graph.registry import Signal
from fi_intel.ingest.store import InMemoryDocumentStore
from fi_intel.sources.base import FetchCursor
from fi_intel.sources.canonical import (
    BarrierSide,
    CanonicalDocument,
    DocumentClass,
    document_text,
)
from fi_intel.tools.evidence import EvidenceItem, GraphFact, Opportunity, OpportunityClaim

AS_OF = datetime(2024, 6, 1, tzinfo=UTC)


class _Tools:
    def __init__(
        self,
        evidence: list[EvidenceItem],
        profile: dict[str, object] | None = None,
    ) -> None:
        self._evidence = evidence
        self._profile = profile

    @property
    def access(self) -> GraphAccessContext:
        return trusted_test_access("wire")

    async def entity_profile(self, entity_key: str) -> dict[str, object]:
        assert entity_key == "LEI-1"
        return self._profile or {
            "assertion_count": 1,
            "predicates": ["HAS_PROGRAMME"],
        }

    async def corpus_search(self, query: str, limit: int, entity_lei: str) -> list[EvidenceItem]:
        assert "Example Bank" in query
        assert "board approved issuance programme" in query
        assert limit == 10
        assert entity_lei == "LEI-1"
        return self._evidence


class _Model:
    def __init__(self, response: ResearchResponse) -> None:
        self.response = response
        self.requests: list[ResearchRequest] = []

    async def research(self, request: ResearchRequest) -> ResearchResponse:
        self.requests.append(request)
        return self.response


class _TargetedDocumentStore(InMemoryDocumentStore):
    def __init__(self) -> None:
        super().__init__()
        self.targeted_reads: list[tuple[str, str]] = []

    async def load_documents(self, source_id: str) -> list[CanonicalDocument]:
        raise AssertionError(f"publication must not scan the {source_id!r} corpus")

    async def load_document(self, source_id: str, doc_id: str) -> CanonicalDocument | None:
        self.targeted_reads.append((source_id, doc_id))
        return await super().load_document(source_id, doc_id)


def _signal() -> Signal:
    return Signal(
        signal_id="signal-1",
        pattern="board_approved_issuance_programme",
        entity_key="LEI-1",
        entity_name="Example Bank",
        priority=90,
        fired_at=AS_OF,
        as_of=AS_OF,
        evidence={"programme": "USD 2bn EMTN"},
    )


def _document_and_evidence() -> tuple[CanonicalDocument, EvidenceItem]:
    doc = CanonicalDocument(
        doc_id="DOC-1",
        source_id="wire",
        published_at=AS_OF,
        recorded_at=AS_OF,
        title="Board approval",
        body="Example Bank approved a USD 2bn EMTN programme.",
        document_class=DocumentClass.NEWS_WIRE,
        url="https://example.test/DOC-1",
    )
    text = document_text(doc)
    excerpt = "Example Bank approved a USD 2bn EMTN programme."
    start = text.index(excerpt)
    end = start + len(excerpt)
    return doc, EvidenceItem(
        evidence_id=EvidenceItem.make_id(doc.source_id, doc.doc_id, start, end),
        source_id=doc.source_id,
        doc_id=doc.doc_id,
        char_start=start,
        char_end=end,
        excerpt=excerpt,
        source_url=doc.url,
    )


@pytest.mark.parametrize("bad_index", [-1, 1, 999])
async def test_research_rejects_any_out_of_bundle_citation(bad_index: int) -> None:
    _, evidence = _document_and_evidence()
    model = _Model(
        ResearchResponse(
            title="Issuance opportunity",
            claims=[
                ResearchClaim(
                    text="The bank approved an issuance programme.",
                    evidence_indices=[bad_index],
                    confidence=0.9,
                )
            ],
            falsifier="The programme is withdrawn.",
        )
    )
    researcher = OpportunityResearcher(
        _Tools([evidence]),  # type: ignore[arg-type]
        model,
        InMemoryDocumentStore(),
    )

    with pytest.raises(EvidenceCitationError, match="invalid evidence indices"):
        await researcher.research_signal(_signal())


async def test_publication_resolves_atomic_claim_and_exact_excerpt() -> None:
    doc, evidence = _document_and_evidence()
    store = _TargetedDocumentStore()
    await store.commit_batch(
        [doc],
        [],
        FetchCursor(source_id="wire", position="1", updated_at=AS_OF),
    )
    opportunity = Opportunity(
        title="Issuance opportunity",
        entity_key="LEI-1",
        summary="The bank approved an issuance programme.",
        falsifier="The programme is withdrawn.",
        evidence_ids=[evidence.evidence_id],
        claims=[
            OpportunityClaim(
                text="The bank approved an issuance programme.",
                evidence_ids=[evidence.evidence_id],
                confidence=0.9,
            )
        ],
    )

    assert await validate_opportunity(opportunity, store, [evidence]) is opportunity
    assert store.targeted_reads == [("wire", doc.doc_id)]

    tampered = evidence.model_copy(update={"excerpt": "different text"})
    with pytest.raises(EvidenceValidationError, match="excerpt does not match span"):
        await validate_opportunity(opportunity, store, [tampered])


async def test_publication_rejects_global_citation_without_claim_owner() -> None:
    doc, evidence = _document_and_evidence()
    store = InMemoryDocumentStore()
    await store.commit_batch(
        [doc],
        [],
        FetchCursor(source_id="wire", position="1", updated_at=AS_OF),
    )
    opportunity = Opportunity(
        title="Issuance opportunity",
        entity_key="LEI-1",
        summary="Claim.",
        falsifier="Counterevidence.",
        evidence_ids=[evidence.evidence_id],
        claims=[OpportunityClaim(text="Claim.", evidence_ids=[], confidence=0.5)],
    )

    with pytest.raises(EvidenceValidationError, match="has no evidence IDs"):
        await validate_opportunity(opportunity, store, [evidence])


async def test_publication_rechecks_as_of_and_information_barrier() -> None:
    doc, evidence = _document_and_evidence()
    opportunity = Opportunity(
        title="Issuance opportunity",
        entity_key="LEI-1",
        summary="The bank approved an issuance programme.",
        falsifier="The programme is withdrawn.",
        evidence_ids=[evidence.evidence_id],
        claims=[
            OpportunityClaim(
                text="The bank approved an issuance programme.",
                evidence_ids=[evidence.evidence_id],
                confidence=0.9,
            )
        ],
    )
    store = InMemoryDocumentStore()
    await store.commit_batch(
        [doc],
        [],
        FetchCursor(source_id="wire", position="1", updated_at=AS_OF),
    )
    access = trusted_test_access("wire")

    with pytest.raises(EvidenceValidationError, match="was not known at as_of"):
        await validate_opportunity(
            opportunity,
            store,
            [evidence],
            as_of=AS_OF - timedelta(seconds=1),
            access=access,
        )

    private_store = InMemoryDocumentStore()
    private_doc = doc.model_copy(update={"barrier_side": BarrierSide.PRIVATE})
    await private_store.commit_batch(
        [private_doc],
        [],
        FetchCursor(source_id="wire", position="1", updated_at=AS_OF),
    )
    with pytest.raises(EvidenceValidationError, match="information barrier"):
        await validate_opportunity(
            opportunity, private_store, [evidence], as_of=AS_OF, access=access
        )


async def test_research_rejects_unauthorized_trigger_source() -> None:
    _, evidence = _document_and_evidence()
    model = _Model(
        ResearchResponse(title="t", claims=[], falsifier="f", insufficient_evidence=True)
    )
    researcher = OpportunityResearcher(
        _Tools([evidence]),  # type: ignore[arg-type]
        model,
        InMemoryDocumentStore(),
    )
    signal = _signal().model_copy(update={"source_ids": ("private-wire",)})

    with pytest.raises(EvidenceCitationError, match="unauthorized triggering sources"):
        await researcher.research_signal(signal)
    assert model.requests == []


async def test_reasoning_request_carries_graph_fact_and_its_evidence_index() -> None:
    _, evidence = _document_and_evidence()
    fact = GraphFact(
        assertion_id="assertion-1",
        predicate="PROGRAMME_APPROVED_BY",
        subject_type="Programme",
        subject_key="programme-1",
        subject_name="EMTN",
        object_type="Organization",
        object_key="LEI-1",
        object_name="Example Bank",
        properties={"programme": "EMTN"},
        valid_from=AS_OF.isoformat(),
        confidence=0.9,
        evidence_id=evidence.evidence_id,
    )
    model = _Model(
        ResearchResponse(
            title="Abstained",
            claims=[],
            falsifier="More evidence arrives.",
            insufficient_evidence=True,
        )
    )
    researcher = OpportunityResearcher(
        _Tools(
            [evidence],
            {
                "assertion_count": 1,
                "predicates": [fact.predicate],
                "assertions": [fact],
                "evidence": [evidence],
            },
        ),  # type: ignore[arg-type]
        model,
        InMemoryDocumentStore(),
    )

    await researcher.research_signal(_signal())

    (request,) = model.requests
    assert request.profile_assertions[0].assertion_id == "assertion-1"
    assert request.profile_assertions[0].evidence_index == 0
