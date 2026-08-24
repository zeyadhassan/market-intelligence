"""Constrained event extraction.

The model is a protocol. Production wires an LLM client; tests use a stub
that asserts on the constructed request and returns canned typed output.
The request is constrained by construction: the response schema's
predicate/node fields ARE the closed enums, so an out-of-vocabulary value
fails Pydantic validation and is routed to proposed_type by the validator
— it can never reach the graph as a real assertion.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.sources.canonical import CanonicalDocument

# Bumped whenever the extraction code changes in a way that affects output.
EXTRACTOR_VERSION = "1.0.0"
# Bumped whenever the prompt template changes. Both travel onto assertions
# so a model or prompt change can be invalidated later (anti-pattern:
# caching model output as ground truth).
PROMPT_VERSION = "extract-v1"

PROMPT_TEMPLATE = """\
You are an extraction engine for a financial-institutions knowledge graph.
Extract ONLY claims about these event types, using ONLY the allowed
predicates and node types listed below. Do not invent types. For every
claim give the exact character span in the document that evidences it.

Allowed predicates: {predicates}
Allowed node types: {node_types}

Document:
{body}
"""


class RawEntityMention(BaseModel):
    """A mention as the model reports it — before resolution to a key."""

    model_config = ConfigDict(frozen=True)

    node_type: NodeType
    name: str
    # Stable key when the document itself provides one (LEI/ISIN/event slug);
    # otherwise resolved downstream from the mention.
    key: str | None = None


class RawClaim(BaseModel):
    """One extracted claim, pre-validation. Offsets are into the document
    body and must resolve to real text containing the subject mention."""

    model_config = ConfigDict(frozen=True)

    predicate: EdgeType
    subject: RawEntityMention
    object: RawEntityMention
    valid_from: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    snippet_offset: tuple[int, int]
    snippet_text: str


class ExtractionRequest(BaseModel):
    """The exact request sent to the model. Tests assert on this."""

    model_config = ConfigDict(frozen=True)

    prompt_version: str
    extractor_version: str
    prompt: str
    doc_id: str


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    claims: list[RawClaim]


@runtime_checkable
class StructuredExtractor(Protocol):
    """The model boundary. Implementations return typed claims only."""

    async def extract(self, request: ExtractionRequest) -> ExtractionResponse: ...


def build_request(doc: CanonicalDocument) -> ExtractionRequest:
    """Construct the constrained extraction request for a document."""
    prompt = PROMPT_TEMPLATE.format(
        predicates=", ".join(str(e) for e in EdgeType),
        node_types=", ".join(str(n) for n in NodeType),
        body=doc.body,
    )
    return ExtractionRequest(
        prompt_version=PROMPT_VERSION,
        extractor_version=EXTRACTOR_VERSION,
        prompt=prompt,
        doc_id=doc.doc_id,
    )


def claim_to_assertion(
    claim: RawClaim, doc: CanonicalDocument, recorded_at: datetime
) -> Assertion:
    """Lower a validated claim to an Assertion with full provenance."""
    return Assertion(
        predicate=claim.predicate,
        subject=EntityRef(
            node_type=claim.subject.node_type,
            key=claim.subject.key or claim.subject.name,
            display_name=claim.subject.name,
        ),
        object=EntityRef(
            node_type=claim.object.node_type,
            key=claim.object.key or claim.object.name,
            display_name=claim.object.name,
        ),
        source_doc_id=doc.doc_id,
        snippet_offset=claim.snippet_offset,
        extractor_version=EXTRACTOR_VERSION,
        confidence=claim.confidence,
        valid_from=claim.valid_from,
        recorded_at=recorded_at,
    )
