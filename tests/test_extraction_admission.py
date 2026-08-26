"""Service-free tests for semantic admission and resolver-bound graph keys."""

from datetime import UTC, datetime

from fi_intel.graph.queries import ALL_PATTERNS
from fi_intel.ingest.extract import (
    PREDICATE_PROPERTY_REQUIREMENTS,
    ChangeDirection,
    ClaimProperties,
    ExtractionRequest,
    ExtractionResponse,
    RawClaim,
    RawEntityMention,
)
from fi_intel.ingest.extract_pipeline import ExtractionPipeline, InMemoryProposedTypeSink
from fi_intel.ingest.resolve import EntityResolver, InMemoryResolutionStore
from fi_intel.ontology.schema import Assertion
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.sources.adapters.gleif import gleif_fixture
from fi_intel.sources.canonical import CanonicalDocument, DocumentClass

RECORDED = datetime(2024, 1, 15, 9, tzinfo=UTC)


class _Extractor:
    def __init__(self, claims: list[RawClaim]) -> None:
        self._claims = claims
        self.requests: list[ExtractionRequest] = []

    async def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        self.requests.append(request)
        return ExtractionResponse(claims=self._claims)


class _Writer:
    def __init__(self) -> None:
        self.assertions: list[Assertion] = []

    async def write(self, assertion: Assertion) -> str:
        self.assertions.append(assertion)
        return assertion.assertion_id()


def _rating_doc(*, known: bool = True) -> CanonicalDocument:
    name = "Gulf Meridian Bank Q.P.S.C." if known else "Unlisted Example Bank"
    return CanonicalDocument(
        source_id="wire",
        doc_id="D-1",
        published_at=RECORDED,
        recorded_at=RECORDED,
        title="Outlook action",
        body=f"{name} outlook changed to negative.",
        document_class=DocumentClass.RATING_ACTION,
        mentioned_names=(name,),
        identifiers={"lei": "213800GMBQPSC000000001" if known else "MODEL-INVENTED"},
    )


def _claim(
    properties: ClaimProperties, name: str = "Gulf Meridian Bank Q.P.S.C."
) -> RawClaim:
    text = f"{name} outlook changed to negative."
    return RawClaim(
        predicate=EdgeType.RATING_ACTION_ON,
        subject=RawEntityMention(
            node_type=NodeType.ORGANIZATION,
            name=name,
            key="MODEL-CONTROLLED-KEY",
        ),
        object=RawEntityMention(node_type=NodeType.RATING, name="negative outlook"),
        valid_from=RECORDED,
        confidence=0.9,
        snippet_offset=(len("Outlook action\n"), len("Outlook action\n") + len(text)),
        snippet_text=text,
        properties=properties,
    )


async def _resolver() -> EntityResolver:
    store = InMemoryResolutionStore()
    await store.load_reference([document async for document in gleif_fixture().fetch()])
    return EntityResolver(store)


async def test_valid_sibling_writes_with_resolver_key_while_bad_claim_is_rejected() -> None:
    bad = _claim(ClaimProperties())
    good = _claim(
        ClaimProperties(direction=ChangeDirection.NEGATIVE, rating_type="outlook")
    )
    writer = _Writer()
    pipeline = ExtractionPipeline(
        _Extractor([bad, good]),
        writer,  # type: ignore[arg-type]
        InMemoryProposedTypeSink(),
        await _resolver(),
    )

    result = await pipeline.extract_document(_rating_doc(), RECORDED)

    assert result.assertions_written == 1
    assert result.semantic_rejections == 1
    assert len(writer.assertions) == 1
    assert writer.assertions[0].subject.key == "213800GMBQPSC000000001"
    assert writer.assertions[0].subject.key != "MODEL-CONTROLLED-KEY"
    assert writer.assertions[0].properties["direction"] == "negative"


async def test_unresolved_organization_never_uses_model_key() -> None:
    writer = _Writer()
    pipeline = ExtractionPipeline(
        _Extractor(
            [
                    _claim(
                        ClaimProperties(
                            direction=ChangeDirection.NEGATIVE,
                            rating_type="outlook",
                        ),
                        name="Unlisted Example Bank",
                    )
            ]
        ),
        writer,  # type: ignore[arg-type]
        InMemoryProposedTypeSink(),
        await _resolver(),
    )

    result = await pipeline.extract_document(_rating_doc(known=False), RECORDED)

    assert result.assertions_written == 0
    assert result.unresolved_entity_rejections == 1
    assert writer.assertions == []


def test_every_detector_attribute_is_extractable_and_required() -> None:
    schema_attributes = set(ClaimProperties.model_json_schema()["properties"])

    for pattern in ALL_PATTERNS:
        required_by_claims = set().union(
            *(PREDICATE_PROPERTY_REQUIREMENTS.get(predicate, frozenset())
              for predicate in pattern.required_claim_types)
        )
        assert pattern.required_attributes <= schema_attributes
        assert pattern.required_attributes <= required_by_claims
