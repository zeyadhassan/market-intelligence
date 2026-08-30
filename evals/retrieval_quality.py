"""Runnable retrieval scorecard over the labelled synthetic POC corpus.

Run with ``python -m evals.retrieval_quality``. This dataset is a regression
fixture; passing it demonstrates pipeline behavior, not market accuracy.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass

from evals.retrieval_metrics import (
    RankingCase,
    RetrievalGate,
    RetrievalMetrics,
    evaluate_rankings,
)
from fi_intel.governance.audit import InMemoryAuditLog
from fi_intel.retrieval.chunking import HashingEmbedder
from fi_intel.retrieval.corpus import CorpusSearch, ScoredChunk
from fi_intel.retrieval.entitlement import Principal, Side
from fi_intel.retrieval.service import RetrievalService
from fi_intel.retrieval.store import InMemoryCorpusStore
from fi_intel.sources.fixture import synthetic_wire


@dataclass(frozen=True)
class RetrievalJudgment:
    query_id: str
    query: str
    relevance: dict[str, int]


JUDGMENTS = (
    RetrievalJudgment(
        "negative-outlook",
        "outlook revised to negative",
        {"SW-2024-0001": 3, "SW-2024-0002": 2, "SW-2024-0003": 3},
    ),
    RetrievalJudgment(
        "capital-decline",
        "CET1 capital ratio decline",
        {"SW-2024-0004": 3, "SW-2024-0001": 2, "SW-2024-0003": 2},
    ),
    RetrievalJudgment("treasurer-change", "treasurer departure leadership", {"SW-2024-0005": 3}),
    RetrievalJudgment("programme-approval", "EMTN programme board approval", {"SW-2024-0006": 3}),
    RetrievalJudgment("maturity-wall", "sukuk maturity refinancing", {"SW-2024-0007": 3}),
    RetrievalJudgment("mandate-outcome", "bond mandate banks appointed", {"SW-2024-0008": 3}),
    RetrievalJudgment(
        "steady-results", "Northern Harbour steady CET1 results", {"SW-2024-0009": 3}
    ),
    RetrievalJudgment("stable-rating", "Northern Harbour stable outlook", {"SW-2024-0011": 3}),
    RetrievalJudgment(
        "similar-name-trap", "Gulf Meridian Capital private credit fund", {"SW-2024-0012": 3}
    ),
)

POC_GATE = RetrievalGate(
    minimum_queries=9,
    minimum_recall_at_k=0.85,
    minimum_mrr=0.80,
    minimum_ndcg_at_k=0.75,
)


def _unique_document_ids(results: list[ScoredChunk]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(result.doc.doc_id for result in results))


async def evaluate_synthetic_retrieval() -> tuple[RetrievalMetrics, tuple[str, ...]]:
    embedder = HashingEmbedder()
    store = InMemoryCorpusStore(embedder)
    store.register_source("synthetic_wire")
    store.grant("synthetic-eval", "synthetic_wire")
    store.add_documents([document async for document in synthetic_wire().fetch()])
    service = RetrievalService(
        CorpusSearch(store, embedder), InMemoryAuditLog(), run_id="synthetic-retrieval-eval"
    )
    principal = Principal(
        principal_id="synthetic-evaluator",
        entitlement_group="synthetic-eval",
        side=Side.PUBLIC,
    )

    cases: list[RankingCase] = []
    for judgment in JUDGMENTS:
        results = await service.search(judgment.query, principal, mode="hybrid", limit=10)
        cases.append(
            RankingCase(
                query_id=judgment.query_id,
                ranked_document_ids=_unique_document_ids(results),
                relevance=judgment.relevance,
            )
        )
    metrics = evaluate_rankings(cases, k=10)
    return metrics, POC_GATE.failures(metrics)


async def main() -> int:
    metrics, failures = await evaluate_synthetic_retrieval()
    report = {
        "dataset_tier": "regression_fixture",
        "production_eligible": False,
        "metrics": asdict(metrics),
        "failures": failures,
        "passed": not failures,
    }
    print(json.dumps(report, indent=2, sort_keys=True))  # noqa: T201
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
