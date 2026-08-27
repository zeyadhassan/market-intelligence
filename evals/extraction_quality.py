"""Gated extraction-quality evaluation against the synthetic claim ledger.

Usage: ``python -m evals.extraction_quality``
Requires ``FI_INTEL_TEST_NEO4J_URI`` and ``FI_INTEL_LLM_BASE_URL``.
Thresholds can be overridden with ``FI_INTEL_EVAL_EXTRACTION_*`` variables.
"""

import asyncio
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from evals.statistics import BinaryCounts, RateGate, evaluate_rate_gate
from fi_intel.config import Settings
from fi_intel.governance.model_usage import InMemoryModelUsageLog
from fi_intel.governance.policy import trusted_test_access
from fi_intel.graph.client import GraphClient
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ingest.extract_pipeline import (
    ExtractionPipeline,
    ExtractionResult,
    InMemoryProposedTypeSink,
)
from fi_intel.ingest.extractors.openai_compatible_extractor import build_structured_extractor
from fi_intel.ingest.resolve import EntityResolver, InMemoryResolutionStore
from fi_intel.logging import new_run_id
from fi_intel.ontology.schema import Assertion
from fi_intel.sources.adapters.gleif import gleif_fixture
from fi_intel.sources.fixture import synthetic_wire
from fi_intel.synth.graph_fixture import (
    gulf_meridian_assertions,
    northern_harbour_assertions,
)

AssertionKey = tuple[str, ...]
ACCESS = trusted_test_access("synthetic_wire")


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@dataclass(frozen=True)
class ExtractionQualityThresholds:
    min_documents: int = 1
    min_assertions: int = 1
    min_precision: float = 0.80
    min_recall: float = 0.70
    max_rejection_rate: float = 0.25
    min_model_calls_per_document: float = 1.0
    min_rate_samples: int = 100
    confidence: float = 0.95

    @classmethod
    def from_env(cls) -> "ExtractionQualityThresholds":
        prefix = "FI_INTEL_EVAL_EXTRACTION_"
        return cls(
            min_documents=_env_int(f"{prefix}MIN_DOCUMENTS", 1),
            min_assertions=_env_int(f"{prefix}MIN_ASSERTIONS", 1),
            min_precision=_env_float(f"{prefix}MIN_PRECISION", 0.80),
            min_recall=_env_float(f"{prefix}MIN_RECALL", 0.70),
            max_rejection_rate=_env_float(f"{prefix}MAX_REJECTION_RATE", 0.25),
            min_model_calls_per_document=_env_float(
                f"{prefix}MIN_MODEL_CALLS_PER_DOCUMENT",
                1.0,
            ),
            min_rate_samples=_env_int(f"{prefix}MIN_RATE_SAMPLES", 100),
            confidence=_env_float(f"{prefix}CONFIDENCE", 0.95),
        )


@dataclass(frozen=True)
class ExtractionQualityReport:
    documents: int
    assertions_written: int
    true_positives: int
    precision: float
    recall: float
    rejection_rate: float
    precision_lower: float
    recall_lower: float
    admission_lower: float
    model_calls: int
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def fixture_assertion_keys(assertions: list[Assertion]) -> list[AssertionKey]:
    return [
        (
            assertion.source_doc_id,
            str(assertion.predicate),
            str(assertion.subject.node_type),
            assertion.subject.key,
            str(assertion.object.node_type),
            assertion.object.key,
        )
        for assertion in assertions
    ]


def assess_extraction_quality(
    results: list[ExtractionResult],
    actual: list[AssertionKey],
    expected: list[AssertionKey],
    model_calls: int,
    thresholds: ExtractionQualityThresholds,
) -> ExtractionQualityReport:
    """Score claim identity as a multiset so duplicate hallucinations are penalized."""
    actual_counts = Counter(actual)
    expected_counts = Counter(expected)
    true_positives = sum((actual_counts & expected_counts).values())
    precision = true_positives / sum(actual_counts.values()) if actual_counts else 0.0
    recall = true_positives / sum(expected_counts.values()) if expected_counts else 0.0

    assertions_written = sum(result.assertions_written for result in results)
    rejected = sum(
        result.offset_rejections + result.low_confidence_dropped + result.proposed_types
        for result in results
    )
    processed = assertions_written + rejected
    rejection_rate = rejected / processed if processed else 1.0

    failures: list[str] = []
    if len(results) < thresholds.min_documents:
        failures.append(f"documents {len(results)} below minimum {thresholds.min_documents}")
    if assertions_written < thresholds.min_assertions:
        failures.append(
            f"assertions {assertions_written} below minimum {thresholds.min_assertions}"
        )
    gates = (
        evaluate_rate_gate(
            BinaryCounts(true_positives, sum(actual_counts.values())),
            {},
            RateGate(
                name="precision",
                minimum_lower_bound=thresholds.min_precision,
                minimum_samples=thresholds.min_rate_samples,
                confidence=thresholds.confidence,
            ),
        ),
        evaluate_rate_gate(
            BinaryCounts(true_positives, sum(expected_counts.values())),
            {},
            RateGate(
                name="recall",
                minimum_lower_bound=thresholds.min_recall,
                minimum_samples=thresholds.min_rate_samples,
                confidence=thresholds.confidence,
            ),
        ),
        evaluate_rate_gate(
            BinaryCounts(assertions_written, processed),
            {},
            RateGate(
                name="admission-rate",
                minimum_lower_bound=1.0 - thresholds.max_rejection_rate,
                minimum_samples=thresholds.min_rate_samples,
                confidence=thresholds.confidence,
            ),
        ),
    )
    for gate in gates:
        failures.extend(f"{gate.name}: {failure}" for failure in gate.failures)
    required_calls = len(results) * thresholds.min_model_calls_per_document
    if model_calls < required_calls:
        failures.append(f"model calls {model_calls} below required {required_calls:.1f}")

    return ExtractionQualityReport(
        documents=len(results),
        assertions_written=assertions_written,
        true_positives=true_positives,
        precision=precision,
        recall=recall,
        rejection_rate=rejection_rate,
        precision_lower=gates[0].overall.lower,
        recall_lower=gates[1].overall.lower,
        admission_lower=gates[2].overall.lower,
        model_calls=model_calls,
        failures=tuple(failures),
    )


async def main() -> int:
    uri = os.environ.get("FI_INTEL_TEST_NEO4J_URI")
    if uri is None:
        print("FI_INTEL_TEST_NEO4J_URI not set; extraction quality gate cannot run")
        return 2
    settings = Settings()
    if not settings.llm_base_url:
        print("FI_INTEL_LLM_BASE_URL not set; extraction quality gate cannot run")
        return 2

    run_id = new_run_id()
    usage_log = InMemoryModelUsageLog()
    client = GraphClient(uri, "neo4j", "fi_intel")
    await client.migrate()
    await client.delete_all()
    await client.migrate()
    try:
        extractor = build_structured_extractor(settings, usage_log, run_id)
        sink = InMemoryProposedTypeSink()
        resolution_store = InMemoryResolutionStore()
        await resolution_store.load_reference(
            [document async for document in gleif_fixture().fetch()]
        )
        pipeline = ExtractionPipeline(
            extractor,
            AssertionWriter(client),
            sink,
            EntityResolver(resolution_store),
            min_confidence=settings.min_extraction_confidence,
        )
        docs = [document async for document in synthetic_wire().fetch()]
        recorded_at = datetime.now(UTC)
        results = [await pipeline.extract_document(document, recorded_at) for document in docs]

        rows = await client.read_all_assertions_including_superseded(
            as_of=recorded_at,
            access=ACCESS,
        )
        actual: list[AssertionKey] = [
            (
                str(row["a"].get("source_doc_id")),
                str(row["a"].get("predicate")),
                str(row["s"].get("node_type")),
                str(row["s"].get("key")),
                str(row["o"].get("node_type")),
                str(row["o"].get("key")),
            )
            for row in rows
            if row["a"].get("source_doc_id") and row["a"].get("predicate")
        ]
        expected = fixture_assertion_keys(
            gulf_meridian_assertions() + northern_harbour_assertions()
        )
        report = assess_extraction_quality(
            results,
            actual,
            expected,
            len(usage_log.events),
            ExtractionQualityThresholds.from_env(),
        )

        print(
            f"documents={report.documents} assertions={report.assertions_written} "
            f"true_positives={report.true_positives} precision={report.precision:.3f} "
            f"recall={report.recall:.3f} rejection_rate={report.rejection_rate:.3f} "
            f"model_calls={report.model_calls}"
        )
        for failure in report.failures:
            print(f"FAIL: {failure}")
        print("PASS" if report.passed else "FAIL")
        return 0 if report.passed else 1
    finally:
        await client.delete_all()
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
