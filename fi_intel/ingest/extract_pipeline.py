"""Extraction pipeline: document -> resolve -> model -> validate -> graph.

Failure handling follows the project's invariants: a model that returns
out-of-vocabulary types is not an error — those claims go to proposed_type
and the valid claims proceed. A claim whose offsets don't resolve is
rejected, not written. The document's own failure (e.g. extractor raising)
propagates loudly.
"""

import hashlib
from datetime import datetime
from typing import Protocol, runtime_checkable

import asyncpg
from pydantic import BaseModel, ConfigDict, ValidationError

from fi_intel.graph.writer import AssertionWriter
from fi_intel.ingest.extract import (
    EXTRACTOR_VERSION,
    RawEntityMention,
    StructuredExtractor,
    build_request,
    claim_to_assertion,
)
from fi_intel.ingest.resolve import EntityResolver, normalize_name
from fi_intel.logging import get_logger
from fi_intel.ontology.validators import (
    ProposedType,
    proposed_type_from_validation_error,
    validate_claims,
)
from fi_intel.ontology.vocab import NodeType
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


class PostgresProposedTypeSink:
    """Persist proposed types to the review queue."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def record(self, proposal: ProposedType) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO proposed_type
                (source_id, doc_id, proposed_name, kind, context_snippet, extractor_version)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            proposal.source_id,
            proposal.doc_id,
            proposal.proposed_name,
            proposal.kind,
            proposal.context_snippet,
            proposal.extractor_version,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


class ExtractionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    assertions_written: int
    offset_rejections: int
    proposed_types: int
    low_confidence_dropped: int = 0
    semantic_rejections: int = 0
    unresolved_entity_rejections: int = 0
    claims_held_for_resolution: int = 0


class ExtractionPipeline:
    def __init__(
        self,
        extractor: StructuredExtractor,
        writer: AssertionWriter,
        proposed_sink: ProposedTypeSink,
        resolver: EntityResolver,
        min_confidence: float = 0.0,
    ) -> None:
        self._extractor = extractor
        self._writer = writer
        self._sink = proposed_sink
        self._resolver = resolver
        # Default 0.0 deliberately disables uncalibrated self-confidence as
        # an admission gate. Deployments may opt in only with a governed
        # reliability curve (evals/confidence_calibration.py).
        self._min_confidence = min_confidence
        self._log = get_logger(component="ingest.extract")

    async def extract_document(
        self, doc: CanonicalDocument, recorded_at: datetime
    ) -> ExtractionResult:
        if recorded_at < doc.recorded_at:
            msg = f"extraction recorded_at precedes source recording for {doc.doc_id!r}"
            raise ValueError(msg)

        resolved_mentions: dict[str, str] = {}
        for mention in doc.mentioned_names:
            resolution = await self._resolver.resolve_mention(doc, mention)
            if resolution is not None:
                resolved_mentions[mention] = resolution.lei
        request = build_request(doc, resolved_mentions)
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
        low_confidence = 0
        unresolved = 0
        for claim in outcome.accepted:
            # Low-confidence claims are logged and dropped, never written.
            if claim.confidence < self._min_confidence:
                low_confidence += 1
                self._log.info(
                    "extract.low_confidence_dropped",
                    doc_id=doc.doc_id,
                    predicate=str(claim.predicate),
                    confidence=claim.confidence,
                    threshold=self._min_confidence,
                )
                continue
            subject_key = await self._endpoint_key(claim.subject, doc)
            object_key = await self._endpoint_key(claim.object, doc)
            if subject_key is None or object_key is None:
                unresolved += 1
                # EntityResolver has already persisted each unresolved
                # organization mention in the resolution review queue.  Keep
                # the claim out of the graph and make the held count part of
                # the run result instead of silently losing it.
                self._log.info(
                    "extract.claim_held_for_resolution",
                    doc_id=doc.doc_id,
                    predicate=str(claim.predicate),
                )
                continue
            assertion = claim_to_assertion(
                claim,
                doc,
                recorded_at,
                subject_key=subject_key,
                object_key=object_key,
            )
            await self._writer.write(assertion)
            written += 1
        for offset_rejected in outcome.rejected_offsets:
            self._log.info(
                "extract.offset_rejected",
                doc_id=doc.doc_id,
                offset=offset_rejected.snippet_offset,
            )
        for semantic_rejected in outcome.rejected_semantics:
            self._log.info(
                "extract.semantic_rejected",
                doc_id=doc.doc_id,
                predicate=str(semantic_rejected.claim.predicate),
                reasons=semantic_rejected.reasons,
            )
        return ExtractionResult(
            doc_id=doc.doc_id,
            assertions_written=written,
            offset_rejections=len(outcome.rejected_offsets),
            proposed_types=len(outcome.proposed_types),
            low_confidence_dropped=low_confidence,
            semantic_rejections=len(outcome.rejected_semantics),
            unresolved_entity_rejections=unresolved,
            claims_held_for_resolution=unresolved,
        )

    async def _endpoint_key(self, mention: RawEntityMention, doc: CanonicalDocument) -> str | None:
        if mention.node_type == NodeType.ORGANIZATION:
            resolution = await self._resolver.resolve_mention(doc, mention.name)
            return resolution.lei if resolution is not None else None

        # Accept an identifier candidate only when the document supplied the
        # exact value. All other model keys are ignored.
        if mention.key is not None and mention.key in doc.identifiers.values():
            return mention.key
        payload = "|".join(
            [
                doc.source_id,
                doc.doc_id,
                str(mention.node_type),
                normalize_name(mention.name),
            ]
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
        return f"{str(mention.node_type).lower()}:{digest}"
