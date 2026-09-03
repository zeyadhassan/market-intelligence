"""Orchestration for the one-command, service-free POC evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import JsonValue

from fi_intel.agents.brief import BriefCompiler
from fi_intel.agents.opportunity_research import OpportunityResearcher
from fi_intel.agents.validate import validate_opportunity
from fi_intel.demo.detector import POCAssertionDetector
from fi_intel.demo.heuristics import (
    POC_EXTRACTOR_VERSION,
    POC_REASONER_VERSION,
    POCHeuristicExtractor,
    POCHeuristicReasoningModel,
)
from fi_intel.demo.models import POCDemoReport, POCEvaluation, POCStageSummary
from fi_intel.governance.audit import InMemoryAuditLog
from fi_intel.governance.model_usage import ModelCallEstimate, ModelCapacityLimits
from fi_intel.governance.policy import GraphAccessContext
from fi_intel.graph.coverage import DetectorCoverageGap
from fi_intel.graph.registry import PatternRegistry
from fi_intel.graph.signals import Signal
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ingest.extract_pipeline import (
    ExtractionPipeline,
    ExtractionResult,
    InMemoryProposedTypeSink,
)
from fi_intel.ingest.pipeline import IngestPipeline, IngestResult
from fi_intel.ingest.resolve import EntityResolver, InMemoryResolutionStore
from fi_intel.ingest.store import InMemoryDocumentStore
from fi_intel.ontology.schema import Assertion
from fi_intel.retrieval.chunking import HashingEmbedder
from fi_intel.retrieval.corpus import CorpusSearch
from fi_intel.retrieval.entitlement import Principal, Side
from fi_intel.retrieval.service import RetrievalService
from fi_intel.retrieval.store import InMemoryCorpusStore
from fi_intel.sources.adapters.gleif import gleif_fixture
from fi_intel.sources.base import SourceAdapter
from fi_intel.sources.canonical import CanonicalDocument
from fi_intel.sources.fixture import synthetic_wire
from fi_intel.synth.episodes import (
    GULF_MERIDIAN,
    GULF_MERIDIAN_CAPITAL_LEI,
    GULF_MERIDIAN_LEI,
    NORTHERN_HARBOUR_LEI,
)
from fi_intel.tools.evidence import EvidenceItem
from fi_intel.tools.research_tools import ResearchTools, ToolContext

DEFAULT_POC_AS_OF = datetime(2024, 6, 1, 12, tzinfo=UTC)
POC_RUN_ID = "poc-local-vertical-slice"
POC_ENTITLEMENT_GROUP = "poc-fixture-public"


@dataclass(frozen=True)
class POCDemoArtifacts:
    """Report plus internal artifacts used for provenance tests and debugging."""

    report: POCDemoReport
    documents: tuple[CanonicalDocument, ...]
    assertions: tuple[Assertion, ...]


class _InMemoryAssertionWriter(AssertionWriter):
    """AssertionWriter-compatible sink; it does not construct a graph fixture."""

    def __init__(self) -> None:
        self._assertions: dict[str, Assertion] = {}

    @property
    def assertions(self) -> tuple[Assertion, ...]:
        return tuple(self._assertions.values())

    async def write(self, assertion: Assertion) -> str:
        assertion_id = assertion.assertion_id()
        self._assertions.setdefault(assertion_id, assertion)
        return assertion_id


class _LocalPatternRegistry(PatternRegistry):
    """The BriefCompiler's registry port, backed by admitted assertions."""

    def __init__(
        self,
        detector: POCAssertionDetector,
        assertions: tuple[Assertion, ...],
        access: GraphAccessContext,
    ) -> None:
        self._poc_detector = detector
        self._poc_assertions = assertions
        self._poc_access = access
        # PatternRegistry normally initializes this state in its graph-backed
        # constructor. The service-free registry intentionally does not create
        # a GraphClient, but BriefCompiler still consumes the same coverage-gap
        # port after every run.
        self._last_coverage_gaps: list[DetectorCoverageGap] = []

    @property
    def access(self) -> GraphAccessContext:
        return self._poc_access

    async def run(
        self,
        as_of: datetime,
        enabled: set[str] | None = None,
        *,
        include_unchanged: bool = False,
    ) -> list[Signal]:
        del include_unchanged
        return await self._poc_detector.detect(
            self._poc_assertions,
            as_of=as_of,
            access=self._poc_access,
            enabled=enabled,
        )


class _LocalResearchTools(ResearchTools):
    """Research tools port using real retrieval and in-memory assertions."""

    def __init__(
        self,
        retrieval: RetrievalService,
        assertions: tuple[Assertion, ...],
        registry: _LocalPatternRegistry,
        context: ToolContext,
    ) -> None:
        self._poc_retrieval = retrieval
        self._poc_assertions = assertions
        self._poc_registry = registry
        self._poc_context = context

    @property
    def access(self) -> GraphAccessContext:
        return self._poc_registry.access

    supports_graph_entry = False
    supports_neighborhood = False
    supports_planned_search = False
    supports_timeseries = False
    supports_precedents = False

    async def corpus_search(
        self,
        query: str,
        limit: int = 10,
        entity_lei: str | None = None,
    ) -> list[EvidenceItem]:
        results = await self._poc_retrieval.search(
            query,
            self._poc_context.principal,
            as_of=self._poc_context.as_of,
            entity_lei=entity_lei,
            limit=limit,
        )
        return [
            EvidenceItem(
                evidence_id=EvidenceItem.make_id(
                    result.doc.source_id,
                    result.doc.doc_id,
                    result.chunk.char_start,
                    result.chunk.char_end,
                ),
                source_id=result.doc.source_id,
                doc_id=result.doc.doc_id,
                char_start=result.chunk.char_start,
                char_end=result.chunk.char_end,
                excerpt=result.chunk.text,
                source_url=result.doc.url,
            )
            for result in results
        ]

    async def entity_profile(self, entity_key: str) -> dict[str, object]:
        visible = [
            assertion
            for assertion in self._poc_assertions
            if assertion.recorded_at <= self._poc_context.as_of
            and entity_key in {assertion.subject.key, assertion.object.key}
        ]
        return {
            "entity_key": entity_key,
            "assertion_count": len(visible),
            "predicates": sorted({str(assertion.predicate) for assertion in visible}),
        }


def _access(source_id: str) -> GraphAccessContext:
    principal = Principal(
        principal_id="poc.local.analyst",
        entitlement_group=POC_ENTITLEMENT_GROUP,
        side=Side.PUBLIC,
    )
    return GraphAccessContext(
        principal=principal,
        allowed_source_ids=frozenset({source_id}),
        policy_version="poc-explicit-local-grant-v1",
        run_id=POC_RUN_ID,
        require_audit=True,
    )


def _predicate_counts(assertions: tuple[Assertion, ...]) -> dict[str, JsonValue]:
    counts: dict[str, int] = {}
    for assertion in assertions:
        predicate = str(assertion.predicate)
        counts[predicate] = counts.get(predicate, 0) + 1
    return dict(sorted(counts.items()))


def _evaluation(
    signals: list[Signal],
    documents: tuple[CanonicalDocument, ...],
    as_of: datetime,
    citation_failures: int,
) -> POCEvaluation:
    expected = tuple(sorted(signal.pattern for signal in GULF_MERIDIAN.expected_signals))
    observed = tuple(
        sorted({signal.pattern for signal in signals if signal.entity_key == GULF_MERIDIAN_LEI})
    )
    missing = tuple(sorted(set(expected) - set(observed)))
    # The service-free fixture still measures the original labelled detector
    # set. Observation-only derivatives are product-safe views of the same
    # positive facts and are not additional labelled outcomes.
    observation_only = {"upcoming_maturity_observed", "at1_call_approaching_observed"}
    unexpected = tuple(sorted(set(observed) - set(expected) - observation_only))
    non_positive = [signal for signal in signals if signal.entity_key != GULF_MERIDIAN_LEI]
    docs_by_id = {(document.source_id, document.doc_id): document for document in documents}
    lookahead = sum(
        1
        for signal in signals
        for source_id, doc_id in zip(signal.source_ids, signal.source_doc_ids, strict=True)
        if docs_by_id[(source_id, doc_id)].recorded_at > as_of
    )
    true_positives = len(set(expected) & set(observed))
    false_positives = len(unexpected) + len(non_positive)
    precision = (
        true_positives / (true_positives + false_positives)
        if true_positives + false_positives
        else 0.0
    )
    recall = true_positives / len(expected) if expected else 1.0
    passed = not (missing or unexpected or non_positive or lookahead or citation_failures)
    return POCEvaluation(
        expected_positive_patterns=expected,
        observed_positive_patterns=observed,
        missing_positive_patterns=missing,
        unexpected_positive_patterns=unexpected,
        decoy_signal_count=len(non_positive),
        lookahead_violation_count=lookahead,
        citation_failure_count=citation_failures,
        pattern_precision=precision,
        pattern_recall=recall,
        passed=passed,
    )


async def run_poc_demo(
    *,
    as_of: datetime = DEFAULT_POC_AS_OF,
    adapter: SourceAdapter | None = None,
) -> POCDemoArtifacts:
    """Run the full packaged POC without databases, network, or an LLM."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("POC as_of must be timezone-aware")
    source = adapter or synthetic_wire()
    document_store = InMemoryDocumentStore()
    ingest_result: IngestResult = await IngestPipeline(document_store, batch_size=4).run(source)
    documents = tuple(await document_store.load_documents(source.source_id))
    visible_documents = tuple(
        sorted(
            (document for document in documents if document.recorded_at <= as_of),
            key=lambda document: (document.recorded_at, document.doc_id),
        )
    )

    resolution_store = InMemoryResolutionStore()
    await resolution_store.load_reference([document async for document in gleif_fixture().fetch()])
    resolver = EntityResolver(resolution_store)
    for document in visible_documents:
        await resolver.resolve_document(document, recorded_at=as_of)
    resolutions = await resolution_store.resolutions()
    resolution_queue = await resolution_store.queue()

    writer = _InMemoryAssertionWriter()
    proposed_sink = InMemoryProposedTypeSink()
    extraction = ExtractionPipeline(
        POCHeuristicExtractor(),
        writer,
        proposed_sink,
        resolver,
        min_confidence=0.90,
    )
    extraction_results: list[ExtractionResult] = []
    for document in visible_documents:
        extraction_results.append(await extraction.extract_document(document, document.recorded_at))
    assertions = writer.assertions

    access = _access(source.source_id)
    detector = POCAssertionDetector()
    registry = _LocalPatternRegistry(detector, assertions, access)
    signals = await registry.run(as_of)

    embedder = HashingEmbedder()
    corpus_store = InMemoryCorpusStore(embedder)
    corpus_store.register_source(source.source_id)
    corpus_store.grant(POC_ENTITLEMENT_GROUP, source.source_id)
    corpus_store.add_documents(list(documents))
    corpus_store.add_entity_links(await resolution_store.document_entity_links())
    audit = InMemoryAuditLog()
    retrieval = RetrievalService(CorpusSearch(corpus_store, embedder), audit, POC_RUN_ID)
    context = ToolContext(principal=access.principal, as_of=as_of)
    tools = _LocalResearchTools(retrieval, assertions, registry, context)
    researcher = OpportunityResearcher(
        tools,
        POCHeuristicReasoningModel(),
        document_store,
    )
    compiler = BriefCompiler(
        registry,
        researcher,
        capacity_limits=ModelCapacityLimits(
            max_calls=10,
            max_total_tokens=0,
            max_latency_ms=0.0,
            cold_start_estimate=ModelCallEstimate(
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                latency_ms=0.0,
            ),
        ),
    )
    brief = await compiler.compile(as_of, desk="fi_gcc_poc")

    citation_failures = 0
    for item in brief.items:
        try:
            await validate_opportunity(
                item.opportunity,
                document_store,
                item.evidence,
                as_of=as_of,
                access=access,
            )
        except ValueError:
            citation_failures += 1
    evaluation = _evaluation(signals, documents, as_of, citation_failures)

    extraction_rejections = sum(
        result.offset_rejections
        + result.proposed_types
        + result.low_confidence_dropped
        + result.semantic_rejections
        + result.unresolved_entity_rejections
        for result in extraction_results
    )
    resolved_leis = {resolution.lei for resolution in resolutions}
    claim_count = sum(len(item.opportunity.claims) for item in brief.items)
    citation_count = sum(
        len(claim.evidence_ids) for item in brief.items for claim in item.opportunity.claims
    )
    stages = (
        POCStageSummary(
            stage="canonical_ingestion_and_dedupe",
            processed=ingest_result.fetched,
            emitted=ingest_result.persisted,
            rejected=ingest_result.exact_duplicates + ingest_result.near_duplicates,
            measurements={
                "exact_duplicates": ingest_result.exact_duplicates,
                "near_duplicates": ingest_result.near_duplicates,
                "batches": ingest_result.batches_committed,
                "cursor": ingest_result.final_cursor or "",
            },
        ),
        POCStageSummary(
            stage="deterministic_entity_resolution",
            processed=sum(len(document.mentioned_names) for document in visible_documents),
            emitted=len(resolutions),
            rejected=len(resolution_queue),
            measurements={
                "unique_entities": len(resolved_leis),
                "northern_harbour_resolved": NORTHERN_HARBOUR_LEI in resolved_leis,
                "similar_name_trap_isolated": GULF_MERIDIAN_CAPITAL_LEI in resolved_leis,
            },
        ),
        POCStageSummary(
            stage="typed_claim_generation_and_admission",
            processed=len(visible_documents),
            emitted=len(assertions),
            rejected=extraction_rejections,
            measurements={
                "future_documents_excluded": len(documents) - len(visible_documents),
                "predicate_counts": _predicate_counts(assertions),
                "extractor": POC_EXTRACTOR_VERSION,
            },
        ),
        POCStageSummary(
            stage="governed_pattern_detection",
            processed=len(assertions),
            emitted=len(signals),
            rejected=0,
            measurements={
                "gulf_meridian_signals": sum(
                    signal.entity_key == GULF_MERIDIAN_LEI for signal in signals
                ),
                "northern_harbour_signals": sum(
                    signal.entity_key == NORTHERN_HARBOUR_LEI for signal in signals
                ),
            },
        ),
        POCStageSummary(
            stage="evidence_grounded_brief",
            processed=len(signals),
            emitted=len(brief.items),
            rejected=len(brief.abstained_signals)
            + len(brief.unresearched_signals)
            + len(brief.deferred_signals),
            measurements={
                "atomic_claims": claim_count,
                "claim_citations": citation_count,
                "audited_document_reads": len(audit.events),
                "reasoner": POC_REASONER_VERSION,
            },
        ),
        POCStageSummary(
            stage="fixture_quality_evaluation",
            processed=len(evaluation.expected_positive_patterns),
            emitted=len(evaluation.observed_positive_patterns),
            rejected=len(evaluation.missing_positive_patterns)
            + len(evaluation.unexpected_positive_patterns)
            + evaluation.decoy_signal_count,
            measurements={
                "pattern_precision": evaluation.pattern_precision,
                "pattern_recall": evaluation.pattern_recall,
                "lookahead_violations": evaluation.lookahead_violation_count,
                "passed": evaluation.passed,
            },
        ),
    )
    report = POCDemoReport(
        as_of=as_of,
        fixture_source=source.source_id,
        heuristic_components=(POC_EXTRACTOR_VERSION, POC_REASONER_VERSION),
        stages=stages,
        signals=tuple(signals),
        brief=brief,
        evaluation=evaluation,
    )
    return POCDemoArtifacts(report=report, documents=documents, assertions=assertions)


def format_poc_report(report: POCDemoReport) -> str:
    """Render a concise terminal report without hiding failed stages."""
    verdict = "PASS" if report.evaluation.passed else "FAIL"
    lines = [
        f"FI intelligence POC - {verdict}",
        f"as_of={report.as_of.isoformat()} source={report.fixture_source}",
        "heuristics=" + ", ".join(report.heuristic_components),
        "",
    ]
    for stage in report.stages:
        lines.append(
            f"{stage.stage:<38} processed={stage.processed:<3} "
            f"emitted={stage.emitted:<3} rejected={stage.rejected:<3}"
        )
    lines.extend(["", "Signals:"])
    if report.signals:
        lines.extend(
            f"  {signal.opportunity_score:.3f}  {signal.pattern:<42} {signal.entity_name}"
            for signal in report.signals
        )
    else:
        lines.append("  none")
    lines.extend(["", "Brief opportunities:"])
    if report.brief.items:
        lines.extend(
            f"  {item.opportunity.title} [{len(item.opportunity.evidence_ids)} citation(s)]"
            for item in report.brief.items
        )
    else:
        lines.append("  nothing material")
    lines.extend(
        [
            "",
            f"Fixture precision={report.evaluation.pattern_precision:.3f} "
            f"recall={report.evaluation.pattern_recall:.3f} "
            f"decoy_signals={report.evaluation.decoy_signal_count} "
            f"lookahead_violations={report.evaluation.lookahead_violation_count}",
        ]
    )
    return "\n".join(lines)
