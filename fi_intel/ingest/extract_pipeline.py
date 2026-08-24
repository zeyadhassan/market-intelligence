"""Extraction pipeline: document -> model -> validate -> graph.

Failure handling follows the project's invariants: a model that returns
out-of-vocabulary types is not an error — those claims go to proposed_type
and the valid claims proceed. A claim whose offsets don't resolve is
rejected, not written. The document's own failure (e.g. extractor raising)
propagates loudly.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, ValidationError

from fi_intel.graph.writer import AssertionWriter
from fi_intel.ingest.extract import (
    EXTRACTOR_VERSION,
    StructuredExtractor,
    build_request,
    claim_to_assertion,
)
from fi_intel.logging import get_logger
from fi_intel.ontology.validators import (
    ProposedType,
    proposed_type_from_validation_error,
    validate_claims,
)
from fi_intel.sources.canonical import CanonicalDocument


@runtime_checkable
class ProposedTypeSink(Protocol):
    """Where out-of-vocabulary proposals go for human review."""

    async def record(self, proposal: ProposedType) -> None: ...


class InMemoryProposedTypeSink:
    def __init__(self) -> None:
        self.proposals: list[ProposedType] = []

    async def record(self, proposal: ProposedType) -> None:
        self.proposals.append(proposal)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    assertions_written: int
    offset_rejections: int
    proposed_types: int


class ExtractionPipeline:
    def __init__(
        self,
        extractor: StructuredExtractor,
        writer: AssertionWriter,
        proposed_sink: ProposedTypeSink,
    ) -> None:
        self._extractor = extractor
        self._writer = writer
        self._sink = proposed_sink
        self._log = get_logger(component="ingest.extract")

    async def extract_document(
        self, doc: CanonicalDocument, recorded_at: datetime
    ) -> ExtractionResult:
        request = build_request(doc)
        self._log.info(
            "extract.request",
            doc_id=doc.doc_id,
            prompt_version=request.prompt_version,
            extractor_version=request.extractor_version,
        )
        try:
            response = await self._extractor.extract(request)
        except ValidationError as exc:
            # The model used an out-of-vocabulary type. Route to review,
            # admit nothing.
            proposal = proposed_type_from_validation_error(exc, doc, EXTRACTOR_VERSION)
            await self._sink.record(proposal)
            self._log.info(
                "extract.proposed_type",
                doc_id=doc.doc_id,
                proposed=proposal.proposed_name,
                kind=proposal.kind,
            )
            return ExtractionResult(
                doc_id=doc.doc_id,
                assertions_written=0,
                offset_rejections=0,
                proposed_types=1,
            )

        outcome = validate_claims(response.claims, doc, EXTRACTOR_VERSION)
        written = 0
        for claim in outcome.accepted:
            assertion = claim_to_assertion(claim, doc, recorded_at)
            await self._writer.write(assertion)
            written += 1
        for rejected in outcome.rejected_offsets:
            self._log.info(
                "extract.offset_rejected",
                doc_id=doc.doc_id,
                offset=rejected.snippet_offset,
            )
        return ExtractionResult(
            doc_id=doc.doc_id,
            assertions_written=written,
            offset_rejections=len(outcome.rejected_offsets),
            proposed_types=len(outcome.proposed_types),
        )
