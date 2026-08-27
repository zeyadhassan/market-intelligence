"""Gated opportunity-research evaluation using the configured reasoning model.

Usage: ``python -m evals.opportunity_quality``
Requires ``FI_INTEL_TEST_NEO4J_URI`` and ``FI_INTEL_LLM_BASE_URL``.
Thresholds can be overridden with ``FI_INTEL_EVAL_OPPORTUNITY_*`` variables.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from evals.backtest import ReadOnlyPatternRunner
from evals.statistics import BinaryCounts, RateGate, evaluate_rate_gate
from fi_intel.agents.opportunity_research import OpportunityResearcher
from fi_intel.agents.reasoning.openai_compatible_reasoning import build_reasoning_model
from fi_intel.config import Settings
from fi_intel.governance.audit import InMemoryAuditLog
from fi_intel.governance.model_usage import InMemoryModelUsageLog
from fi_intel.governance.policy import trusted_test_access
from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry, Signal
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ingest.store import InMemoryDocumentStore
from fi_intel.logging import new_run_id
from fi_intel.retrieval.chunking import HashingEmbedder
from fi_intel.retrieval.corpus import CorpusSearch
from fi_intel.retrieval.entitlement import Principal, Side
from fi_intel.retrieval.service import RetrievalService
from fi_intel.retrieval.store import InMemoryCorpusStore
from fi_intel.sources.base import FetchCursor
from fi_intel.sources.fixture import synthetic_wire
from fi_intel.synth.graph_fixture import gulf_meridian_assertions
from fi_intel.tools.evidence import EvidenceItem, Opportunity
from fi_intel.tools.research_tools import ResearchTools, ToolContext

AS_OF = datetime(2024, 6, 1, tzinfo=UTC)
PRINCIPAL = Principal(principal_id="eval", entitlement_group="test", side=Side.PUBLIC)
ACCESS = trusted_test_access(
    "synthetic_wire",
    side=Side.PUBLIC,
    principal_id=PRINCIPAL.principal_id,
)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@dataclass(frozen=True)
class OpportunityQualityThresholds:
    min_positive_citations: int = 1
    min_positive_claims: int = 1
    min_title_chars: int = 12
    min_summary_chars: int = 40
    min_falsifier_chars: int = 20
    min_positive_model_calls: int = 1
    min_case_lower_bound: float = 0.80
    min_rate_samples: int = 100

    @classmethod
    def from_env(cls) -> "OpportunityQualityThresholds":
        prefix = "FI_INTEL_EVAL_OPPORTUNITY_"
        return cls(
            min_positive_citations=_env_int(f"{prefix}MIN_POSITIVE_CITATIONS", 1),
            min_positive_claims=_env_int(f"{prefix}MIN_POSITIVE_CLAIMS", 1),
            min_title_chars=_env_int(f"{prefix}MIN_TITLE_CHARS", 12),
            min_summary_chars=_env_int(f"{prefix}MIN_SUMMARY_CHARS", 40),
            min_falsifier_chars=_env_int(f"{prefix}MIN_FALSIFIER_CHARS", 20),
            min_positive_model_calls=_env_int(f"{prefix}MIN_POSITIVE_MODEL_CALLS", 1),
            min_case_lower_bound=float(os.environ.get(f"{prefix}MIN_CASE_LOWER_BOUND", "0.80")),
            min_rate_samples=_env_int(f"{prefix}MIN_RATE_SAMPLES", 100),
        )


@dataclass(frozen=True)
class OpportunityQualityReport:
    positive_citations: int
    positive_model_calls: int
    abstention_model_calls: int
    compliance_lower: float
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def assess_opportunity_quality(  # noqa: C901
    positive: Opportunity,
    positive_evidence: list[EvidenceItem],
    positive_model_calls: int,
    abstention: Opportunity,
    abstention_evidence: list[EvidenceItem],
    abstention_model_calls: int,
    thresholds: OpportunityQualityThresholds,
) -> OpportunityQualityReport:
    """Apply objective publication and abstention gates to both eval cases."""
    failures: list[str] = []
    evidence_ids = {item.evidence_id for item in positive_evidence}
    if positive.insufficient_evidence:
        failures.append("positive case incorrectly abstained")
    if len(positive.evidence_ids) < thresholds.min_positive_citations:
        failures.append(
            f"positive citations {len(positive.evidence_ids)} below minimum "
            f"{thresholds.min_positive_citations}"
        )
    if len(positive.claims) < thresholds.min_positive_claims:
        failures.append(
            f"positive claims {len(positive.claims)} below minimum {thresholds.min_positive_claims}"
        )
    unavailable = set(positive.evidence_ids) - evidence_ids
    if unavailable:
        failures.append(
            f"positive case cited evidence not returned by tools: {sorted(unavailable)}"
        )
    if len(positive.evidence_ids) != len(set(positive.evidence_ids)):
        failures.append("positive case contains duplicate citations")
    claim_evidence_ids = {
        evidence_id for claim in positive.claims for evidence_id in claim.evidence_ids
    }
    if any(not claim.evidence_ids for claim in positive.claims):
        failures.append("positive case contains an uncited atomic claim")
    unsupported_claim_citations = claim_evidence_ids - evidence_ids
    if unsupported_claim_citations:
        failures.append(
            "positive atomic claims cite evidence not returned by tools: "
            f"{sorted(unsupported_claim_citations)}"
        )
    if claim_evidence_ids != set(positive.evidence_ids):
        failures.append("opportunity-level citations do not equal atomic-claim citations")
    if len(positive.title.strip()) < thresholds.min_title_chars:
        failures.append(f"positive title shorter than {thresholds.min_title_chars} characters")
    if len(positive.summary.strip()) < thresholds.min_summary_chars:
        failures.append(f"positive summary shorter than {thresholds.min_summary_chars} characters")
    if len(positive.falsifier.strip()) < thresholds.min_falsifier_chars:
        failures.append(
            f"positive falsifier shorter than {thresholds.min_falsifier_chars} characters"
        )
    if positive_model_calls < thresholds.min_positive_model_calls:
        failures.append(
            f"positive model calls {positive_model_calls} below minimum "
            f"{thresholds.min_positive_model_calls}"
        )

    positive_failed = bool(failures)
    abstention_failure_start = len(failures)

    if not abstention.insufficient_evidence:
        failures.append("no-evidence case failed to abstain")
    if abstention.evidence_ids or abstention_evidence:
        failures.append("no-evidence abstention returned citations")
    if abstention_model_calls != 0:
        failures.append(
            f"no-evidence abstention made {abstention_model_calls} model calls; expected 0"
        )
    abstention_failed = len(failures) > abstention_failure_start
    compliance = evaluate_rate_gate(
        BinaryCounts(
            successes=int(not positive_failed) + int(not abstention_failed),
            total=2,
        ),
        {},
        RateGate(
            name="opportunity-case-compliance",
            minimum_lower_bound=thresholds.min_case_lower_bound,
            minimum_samples=thresholds.min_rate_samples,
        ),
    )
    failures.extend(f"{compliance.name}: {failure}" for failure in compliance.failures)

    return OpportunityQualityReport(
        positive_citations=len(positive.evidence_ids),
        positive_model_calls=positive_model_calls,
        abstention_model_calls=abstention_model_calls,
        compliance_lower=compliance.overall.lower,
        failures=tuple(failures),
    )


async def main() -> int:
    uri = os.environ.get("FI_INTEL_TEST_NEO4J_URI")
    if uri is None:
        print("FI_INTEL_TEST_NEO4J_URI not set; opportunity quality gate cannot run")
        return 2
    settings = Settings()
    if not settings.llm_base_url:
        print("FI_INTEL_LLM_BASE_URL not set; opportunity quality gate cannot run")
        return 2

    run_id = new_run_id()
    usage_log = InMemoryModelUsageLog()
    client = GraphClient(uri, "neo4j", "fi_intel")
    await client.migrate()
    await client.delete_all()
    await client.migrate()
    try:
        writer = AssertionWriter(client)
        for assertion in gulf_meridian_assertions():
            await writer.write(assertion)

        embedder = HashingEmbedder()
        corpus = InMemoryCorpusStore(embedder)
        corpus.register_source("synthetic_wire")
        corpus.grant("test", "synthetic_wire")
        documents = [document async for document in synthetic_wire().fetch()]
        corpus.add_documents(documents)
        retrieval = RetrievalService(CorpusSearch(corpus, embedder), InMemoryAuditLog(), run_id)
        registry = PatternRegistry(client, access=ACCESS)
        tools = ResearchTools(
            retrieval,
            client,
            registry,
            ToolContext(principal=PRINCIPAL, as_of=AS_OF),
        )
        model = build_reasoning_model(settings, usage_log, run_id)
        document_store = InMemoryDocumentStore()
        await document_store.commit_batch(
            documents,
            [],
            FetchCursor(source_id="synthetic_wire", position="12", updated_at=AS_OF),
        )
        researcher = OpportunityResearcher(tools, model, document_store)

        signals = await ReadOnlyPatternRunner(client, registry).run(
            AS_OF,
            {"board_approved_issuance_programme"},
            395,
        )
        if not signals:
            print("FAIL: expected board_approved_issuance_programme to fire")
            return 1
        calls_before_positive = len(usage_log.events)
        positive, positive_evidence = await researcher.research_signal(signals[0])
        calls_after_positive = len(usage_log.events)

        orphan = Signal(
            signal_id="eval:orphan:2024-06-01",
            pattern="maturity_wall_no_refi",
            entity_key="999999NONEXISTENT000000",
            entity_name="Nonexistent Bank",
            priority=80,
            fired_at=AS_OF,
            as_of=AS_OF,
            evidence={},
        )
        calls_before_abstention = len(usage_log.events)
        abstention, abstention_evidence = await researcher.research_signal(orphan)
        calls_after_abstention = len(usage_log.events)

        report = assess_opportunity_quality(
            positive,
            positive_evidence,
            calls_after_positive - calls_before_positive,
            abstention,
            abstention_evidence,
            calls_after_abstention - calls_before_abstention,
            OpportunityQualityThresholds.from_env(),
        )
        print(
            f"positive_citations={report.positive_citations} "
            f"positive_model_calls={report.positive_model_calls} "
            f"abstention_model_calls={report.abstention_model_calls}"
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
