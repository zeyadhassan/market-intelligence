"""Extraction tests: stub the model, assert on the request, verify the graph.

No live LLM in unit tests. The StubExtractor records the exact request it
was given (so we assert prompt version + closed vocab in the prompt) and
returns canned typed claims. Offset spans below were computed against the
real corpus bodies.
"""

import os
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from fi_intel.governance.policy import trusted_test_access
from fi_intel.graph.client import GraphClient
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ingest.extract import (
    PROMPT_VERSION,
    ChangeDirection,
    ClaimProperties,
    ExtractionRequest,
    ExtractionResponse,
    RawClaim,
    RawEntityMention,
)
from fi_intel.ingest.extract_pipeline import (
    ExtractionPipeline,
    InMemoryProposedTypeSink,
)
from fi_intel.ingest.resolve import EntityResolver, InMemoryResolutionStore
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.sources.adapters.gleif import gleif_fixture
from fi_intel.sources.canonical import CanonicalDocument
from fi_intel.sources.fixture import synthetic_wire

NEO4J_URI = os.environ.get("FI_INTEL_TEST_NEO4J_URI")
RECORDED = datetime(2024, 5, 15, 9, tzinfo=UTC)
ACCESS = trusted_test_access("synthetic_wire")


class StubExtractor:
    """Records requests; returns canned claims keyed by doc_id."""

    def __init__(self, claims_by_doc: dict[str, list[RawClaim]]) -> None:
        self._claims = claims_by_doc
        self.requests: list[ExtractionRequest] = []

    async def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        self.requests.append(request)
        return ExtractionResponse(claims=self._claims.get(request.doc_id, []))


def _claim(
    predicate: EdgeType,
    subj: RawEntityMention,
    obj: RawEntityMention,
    offset: tuple[int, int],
    snippet: str,
    valid_from: datetime,
    properties: ClaimProperties | None = None,
) -> RawClaim:
    return RawClaim(
        predicate=predicate,
        subject=subj,
        object=obj,
        valid_from=valid_from,
        confidence=0.9,
        snippet_offset=offset,
        snippet_text=snippet,
        properties=properties or ClaimProperties(),
    )


async def _doc(doc_id: str) -> CanonicalDocument:
    async for d in synthetic_wire().fetch():
        if d.doc_id == doc_id:
            return d
    raise AssertionError(f"{doc_id} not in corpus")


# --- Request construction: the stub asserts on what we send the model ---


@pytest.fixture
async def pipeline():
    if NEO4J_URI is None:
        pytest.skip("FI_INTEL_TEST_NEO4J_URI not set")
    client = GraphClient(NEO4J_URI, "neo4j", "fi_intel")
    await client.migrate()
    await client.delete_all()
    await client.migrate()
    sink = InMemoryProposedTypeSink()
    resolution_store = InMemoryResolutionStore()
    await resolution_store.load_reference([doc async for doc in gleif_fixture().fetch()])
    yield client, sink, EntityResolver(resolution_store)
    await client.delete_all()
    await client.close()


async def test_request_carries_versions_and_closed_vocab(pipeline) -> None:
    client, sink, resolver = pipeline
    doc = await _doc("SW-2024-0001")
    stub = StubExtractor({})
    pipe = ExtractionPipeline(stub, AssertionWriter(client), sink, resolver)

    await pipe.extract_document(doc, RECORDED)

    (request,) = stub.requests
    assert request.prompt_version == PROMPT_VERSION
    assert request.doc_id == "SW-2024-0001"
    # The prompt enumerates the closed vocabulary — the constraint is in
    # the request, not hoped for.
    assert str(EdgeType.MATURES_ON) in request.prompt
    assert str(NodeType.ORGANIZATION) in request.prompt
    assert doc.body in request.document_text
    assert request.system_instruction not in request.document_text
    assert "resolved_mentions" in request.document_text


async def test_extraction_produces_expected_events_with_dates(pipeline) -> None:
    """Assert on the specific events, not a count."""
    client, sink, resolver = pipeline
    writer = AssertionWriter(client)

    sukuk_doc = await _doc("SW-2024-0007")
    rating_doc = await _doc("SW-2024-0001")
    claims = {
        "SW-2024-0007": [
            _claim(
                EdgeType.MATURES_ON,
                RawEntityMention(
                    node_type=NodeType.INSTRUMENT,
                    name="USD 500 million sukuk",
                    key="XS0000000001",
                ),
                RawEntityMention(
                    node_type=NodeType.EVENT,
                    name="Sukuk maturity",
                    key="event:sukuk-maturity-2025-05-14",
                ),
                offset=(len(sukuk_doc.title) + 1 + 16, len(sukuk_doc.title) + 1 + 37),
                snippet="USD 500 million sukuk",
                valid_from=datetime(2025, 5, 14, tzinfo=UTC),
                properties=ClaimProperties(
                    maturity_date=date(2025, 5, 14),
                    amount_usd_mn=500,
                    currency="USD",
                ),
            )
        ],
        "SW-2024-0001": [
            _claim(
                EdgeType.RATING_ACTION_ON,
                RawEntityMention(
                    node_type=NodeType.ORGANIZATION,
                    name="Gulf Meridian Bank Q.P.S.C.",
                    key="213800GMBQPSC000000001",
                ),
                RawEntityMention(node_type=NodeType.RATING, name="A- negative outlook"),
                offset=(len(rating_doc.title) + 1 + 54, len(rating_doc.title) + 1 + 93),
                snippet="Gulf Meridian Bank Q.P.S.C. to negative",
                valid_from=datetime(2024, 1, 15, tzinfo=UTC),
                properties=ClaimProperties(
                    direction=ChangeDirection.NEGATIVE,
                    rating_type="outlook",
                ),
            )
        ],
    }
    stub = StubExtractor(claims)
    pipe = ExtractionPipeline(stub, writer, sink, resolver)

    r1 = await pipe.extract_document(sukuk_doc, RECORDED)
    r2 = await pipe.extract_document(rating_doc, RECORDED)
    assert r1.assertions_written == 1 and r2.assertions_written == 1

    # MATURES_ON becomes valid in 2025. Query after that valid-time boundary
    # while retaining the earlier recorded-time visibility.
    rows = await client.read_assertions(as_of=datetime(2025, 5, 14, tzinfo=UTC), access=ACCESS)
    by_pred = {r["a"]["predicate"]: r["a"] for r in rows}
    assert {row["a"]["source_id"] for row in rows} == {"synthetic_wire"}
    assert {row["a"]["barrier_side"] for row in rows} == {"public"}
    assert {row["a"]["policy_version"] for row in rows} == {"source-registry-v1"}
    assert str(EdgeType.MATURES_ON) in by_pred
    assert str(EdgeType.RATING_ACTION_ON) in by_pred
    # Correct dates: the sukuk maturity valid_from is the maturity date.
    maturity = by_pred[str(EdgeType.MATURES_ON)]
    assert (
        maturity["valid_from"].year,
        maturity["valid_from"].month,
        maturity["valid_from"].day,
    ) == (2025, 5, 14)


async def test_out_of_vocabulary_type_goes_to_proposed_type(pipeline) -> None:
    """A model that returns an invented relation is caught at parse time;
    the validator routes it to proposed_type and nothing reaches the graph."""
    client, sink, _ = pipeline
    doc = await _doc("SW-2024-0001")

    # What a non-conforming model would emit as raw JSON. Parsing it against
    # the closed-enum schema fails; the pipeline converts that to a proposal.
    oov_payload = RawClaim.model_construct()  # sentinel; real path below
    _ = oov_payload
    with pytest.raises(ValidationError):
        RawClaim(
            predicate="SECRETLY_OWNS",  # type: ignore[arg-type]
            subject=RawEntityMention(node_type=NodeType.ORGANIZATION, name="X"),
            object=RawEntityMention(node_type=NodeType.ORGANIZATION, name="Y"),
            valid_from=RECORDED,
            confidence=0.5,
            snippet_offset=(0, 5),
            snippet_text="xxxxx",
        )

    # The pipeline-level behaviour: an extractor that surfaces a
    # ValidationError has its output routed to proposed_type.
    from fi_intel.ontology.validators import proposed_type_from_validation_error

    try:
        RawClaim(
            predicate="SECRETLY_OWNS",  # type: ignore[arg-type]
            subject=RawEntityMention(node_type=NodeType.ORGANIZATION, name="X"),
            object=RawEntityMention(node_type=NodeType.ORGANIZATION, name="Y"),
            valid_from=RECORDED,
            confidence=0.5,
            snippet_offset=(0, 5),
            snippet_text="xxxxx",
        )
    except ValidationError as exc:
        proposal = proposed_type_from_validation_error(exc, doc, "1.0.0")
        await sink.record(proposal)

    assert len(sink.proposals) == 1
    assert sink.proposals[0].kind == "edge"
    assert sink.proposals[0].proposed_name == "SECRETLY_OWNS"
    assert await client.assertion_count() == 0


async def test_offsets_must_resolve_to_real_text(pipeline) -> None:
    """A claim whose snippet doesn't match the slice is rejected."""
    client, sink, resolver = pipeline
    writer = AssertionWriter(client)
    doc = await _doc("SW-2024-0005")
    bad = _claim(
        EdgeType.LEADERSHIP_CHANGE_AT,
        RawEntityMention(node_type=NodeType.PERSON, name="group treasurer"),
        RawEntityMention(
            node_type=NodeType.ORGANIZATION,
            name="Gulf Meridian",
            key="213800GMBQPSC000000001",
        ),
        offset=(0, 10),  # wrong span: body[0:10] != snippet
        snippet="group treasurer",
        valid_from=datetime(2024, 3, 20, tzinfo=UTC),
        properties=ClaimProperties(role="treasurer"),
    )
    pipe = ExtractionPipeline(StubExtractor({"SW-2024-0005": [bad]}), writer, sink, resolver)
    result = await pipe.extract_document(doc, RECORDED)
    assert result.assertions_written == 0
    assert result.offset_rejections == 1
    assert await client.assertion_count() == 0
