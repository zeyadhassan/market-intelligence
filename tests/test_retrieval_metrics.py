import pytest

from evals.retrieval_metrics import (
    RankingCase,
    RetrievalGate,
    evaluate_rankings,
)
from evals.retrieval_quality import evaluate_synthetic_retrieval


def test_document_ranking_metrics_use_graded_relevance() -> None:
    metrics = evaluate_rankings(
        [
            RankingCase(
                query_id="q1",
                ranked_document_ids=("irrelevant", "relevant-low", "relevant-high"),
                relevance={"relevant-high": 3, "relevant-low": 1},
            )
        ],
        k=2,
    )

    assert metrics.recall_at_k == 0.5
    assert metrics.mean_reciprocal_rank == 0.5
    assert metrics.ndcg_at_k < 0.5


def test_ranking_cases_reject_chunk_level_duplicates() -> None:
    with pytest.raises(ValueError, match="unique"):
        RankingCase(
            query_id="q1",
            ranked_document_ids=("doc-1", "doc-1"),
            relevance={"doc-1": 1},
        )


def test_gate_reports_each_failed_dimension() -> None:
    metrics = evaluate_rankings(
        [
            RankingCase(
                query_id="q1",
                ranked_document_ids=("wrong",),
                relevance={"right": 1},
            )
        ],
        k=1,
    )
    failures = RetrievalGate(2, 0.5, 0.5, 0.5).failures(metrics)

    assert len(failures) == 4


async def test_synthetic_retrieval_scorecard_passes_its_poc_gate() -> None:
    metrics, failures = await evaluate_synthetic_retrieval()

    assert metrics.query_count == 9
    assert not failures
