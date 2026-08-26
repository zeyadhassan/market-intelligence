"""Document-level retrieval metrics and deterministic quality gates."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RankingCase:
    """One query result and its graded document relevance judgments."""

    query_id: str
    ranked_document_ids: tuple[str, ...]
    relevance: dict[str, int]

    def __post_init__(self) -> None:
        if not self.query_id:
            raise ValueError("query_id cannot be empty")
        if len(self.ranked_document_ids) != len(set(self.ranked_document_ids)):
            raise ValueError("ranked_document_ids must be document-level and unique")
        if not self.relevance or not any(grade > 0 for grade in self.relevance.values()):
            raise ValueError("at least one relevant document is required")
        if any(grade < 0 for grade in self.relevance.values()):
            raise ValueError("relevance grades cannot be negative")


@dataclass(frozen=True)
class QueryMetrics:
    query_id: str
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float


@dataclass(frozen=True)
class RetrievalMetrics:
    query_count: int
    k: int
    recall_at_k: float
    mean_reciprocal_rank: float
    ndcg_at_k: float
    queries: tuple[QueryMetrics, ...]


@dataclass(frozen=True)
class RetrievalGate:
    """POC regression thresholds, not a production-readiness claim."""

    minimum_queries: int
    minimum_recall_at_k: float
    minimum_mrr: float
    minimum_ndcg_at_k: float

    def __post_init__(self) -> None:
        if self.minimum_queries < 1:
            raise ValueError("minimum_queries must be positive")
        for name, value in (
            ("minimum_recall_at_k", self.minimum_recall_at_k),
            ("minimum_mrr", self.minimum_mrr),
            ("minimum_ndcg_at_k", self.minimum_ndcg_at_k),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between zero and one")

    def failures(self, metrics: RetrievalMetrics) -> tuple[str, ...]:
        failures: list[str] = []
        if metrics.query_count < self.minimum_queries:
            failures.append(
                f"query count {metrics.query_count} below minimum {self.minimum_queries}"
            )
        for label, actual, required in (
            ("Recall@K", metrics.recall_at_k, self.minimum_recall_at_k),
            ("MRR", metrics.mean_reciprocal_rank, self.minimum_mrr),
            ("nDCG@K", metrics.ndcg_at_k, self.minimum_ndcg_at_k),
        ):
            if actual < required:
                failures.append(f"{label} {actual:.4f} below minimum {required:.4f}")
        return tuple(failures)


def evaluate_rankings(cases: list[RankingCase], *, k: int) -> RetrievalMetrics:
    """Compute macro-averaged Recall@K, reciprocal rank, and nDCG@K."""

    if not cases:
        raise ValueError("at least one ranking case is required")
    if k < 1:
        raise ValueError("k must be positive")

    query_metrics = tuple(_evaluate_query(case, k) for case in cases)
    count = len(query_metrics)
    return RetrievalMetrics(
        query_count=count,
        k=k,
        recall_at_k=sum(item.recall_at_k for item in query_metrics) / count,
        mean_reciprocal_rank=sum(item.reciprocal_rank for item in query_metrics) / count,
        ndcg_at_k=sum(item.ndcg_at_k for item in query_metrics) / count,
        queries=query_metrics,
    )


def _evaluate_query(case: RankingCase, k: int) -> QueryMetrics:
    ranked = case.ranked_document_ids[:k]
    relevant = {document_id for document_id, grade in case.relevance.items() if grade > 0}
    recall = len(set(ranked) & relevant) / len(relevant)

    first_relevant_rank = next(
        (rank for rank, document_id in enumerate(case.ranked_document_ids, start=1)
         if document_id in relevant),
        None,
    )
    reciprocal_rank = 0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank

    actual_grades = [case.relevance.get(document_id, 0) for document_id in ranked]
    ideal_grades = sorted(case.relevance.values(), reverse=True)[:k]
    ideal_dcg = _dcg(ideal_grades)
    ndcg = _dcg(actual_grades) / ideal_dcg if ideal_dcg else 0.0
    return QueryMetrics(
        query_id=case.query_id,
        recall_at_k=recall,
        reciprocal_rank=reciprocal_rank,
        ndcg_at_k=ndcg,
    )


def _dcg(grades: list[int]) -> float:
    return sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(grades, start=1)
    )
