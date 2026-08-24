"""Hybrid corpus search: BM25 + vector, fused, recency-weighted.

Ranking pipeline, all of it deterministic and in code (business rules do
not live in prompts):
  1. candidate generation: BM25 over chunk text, cosine over embeddings
  2. reciprocal rank fusion of the two ranked lists
  3. multiplicative recency weight: exponential decay with
     RECENCY_HALF_LIFE_DAYS, measured against the labelled relevance set
     (tests/test_retrieval.py reports all three of BM25-only, vector-only,
     hybrid — tune against that set, never against a single query)

Entitlement and as-of filtering are NOT done here. They are applied by the
store's candidate query (SQL in production, the ported predicate in
tests). This module only ever sees documents the caller may read.
"""

import math
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from fi_intel.retrieval.chunking import Chunk, Embedder, cosine
from fi_intel.retrieval.entitlement import Principal
from fi_intel.sources.canonical import CanonicalDocument

RRF_K = 60  # standard RRF constant; rank fusion is insensitive near it
RECENCY_HALF_LIFE_DAYS = 90.0
#: Oldest documents keep this fraction of a fresh document's weight.
RECENCY_FLOOR = 0.5
DEFAULT_LIMIT = 10
#: Chunks whose fused rank score falls below this fraction of the top
#: result are noise, not weak matches. Without a floor, every query returns
#: `limit` results regardless of relevance — which pressures the system to
#: fill a page (invariant 8 applies to retrieval too).
MIN_SCORE_FRACTION = 0.5


class ScoredChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk: Chunk
    doc: CanonicalDocument
    score: float
    bm25_rank: int | None
    vector_rank: int | None


@runtime_checkable
class CorpusStore(Protocol):
    """Candidate access with entitlement and as-of already applied."""

    async def candidates(
        self, principal: Principal, as_of: datetime | None
    ) -> list[tuple[CanonicalDocument, list[Chunk], list[list[float]]]]:
        """(document, its chunks, chunk embeddings) the caller may see."""
        ...


def _tokenize(text: str) -> list[str]:
    return text.lower().split()


def bm25_rank(
    query: str, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75
) -> list[tuple[int, float]]:
    """Return (chunk_position, score) sorted by score descending.

    Ties are broken by chunk position so that equal scores get equal
    ranks deterministically (competition ranking); without this, rank
    depends on sort stability, which is an invisible relevance bug."""
    query_terms = _tokenize(query)
    docs = [_tokenize(c.text) for c in chunks]
    avg_len = sum(len(d) for d in docs) / max(len(docs), 1)

    # Document frequency across the candidate set.
    df: dict[str, int] = {}
    for tokens in docs:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    n = len(docs)

    scored: list[tuple[int, float]] = []
    for pos, tokens in enumerate(docs):
        tf: dict[str, int] = {}
        for term in tokens:
            tf[term] = tf.get(term, 0) + 1
        score = 0.0
        for term in query_terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            numerator = tf[term] * (k1 + 1)
            denominator = tf[term] + k1 * (1 - b + b * len(tokens) / max(avg_len, 1e-9))
            score += idf * numerator / denominator
        scored.append((pos, score))
    return sorted(scored, key=lambda item: (-item[1], item[0]))


def vector_rank(
    query_embedding: list[float], embeddings: list[list[float]]
) -> list[tuple[int, float]]:
    scored = [(pos, cosine(query_embedding, emb)) for pos, emb in enumerate(embeddings)]
    return sorted(scored, key=lambda item: (-item[1], item[0]))


def _competition_ranks(ranking: list[tuple[int, float]]) -> dict[int, int]:
    """Map chunk position → rank, giving equal scores the same rank."""
    ranks: dict[int, int] = {}
    for i, (pos, score) in enumerate(ranking):
        if i > 0 and score == ranking[i - 1][1]:
            ranks[pos] = ranks[ranking[i - 1][0]]
        else:
            ranks[pos] = i + 1
    return ranks


def reciprocal_rank_fusion(
    rankings: list[list[tuple[int, float]]], k: int = RRF_K
) -> dict[int, float]:
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, (pos, _score) in enumerate(ranking, start=1):
            fused[pos] = fused.get(pos, 0.0) + 1.0 / (k + rank)
    return fused


def recency_weight(published_at: datetime, as_of: datetime | None) -> float:
    """Bounded decay in [RECENCY_FLOOR, 1]: recent is preferred, old is
    discounted — but never silenced. An unbounded exponential would let a
    two-year-old perfect match lose to a week-old near-duplicate, which is
    recency bias, not recency weighting."""
    if as_of is None:
        as_of = datetime.now(tz=UTC)
    age_days = max((as_of - published_at).total_seconds() / 86400.0, 0.0)
    decay: float = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
    return RECENCY_FLOOR + (1.0 - RECENCY_FLOOR) * decay


class CorpusSearch:
    def __init__(self, store: CorpusStore, embedder: Embedder) -> None:
        self._store = store
        self._embedder = embedder

    async def search(
        self,
        query: str,
        principal: Principal,
        as_of: datetime | None = None,
        entity_lei: str | None = None,
        limit: int = DEFAULT_LIMIT,
        mode: str = "hybrid",
    ) -> list[ScoredChunk]:
        if mode not in ("hybrid", "bm25", "vector"):
            msg = f"unknown search mode {mode!r}"
            raise ValueError(msg)

        candidates = await self._store.candidates(principal, as_of)
        if entity_lei is not None:
            candidates = [c for c in candidates if c[0].identifiers.get("lei") == entity_lei]

        chunks: list[Chunk] = []
        docs: list[CanonicalDocument] = []
        embeddings: list[list[float]] = []
        for doc, doc_chunks, doc_embeddings in candidates:
            for chunk, embedding in zip(doc_chunks, doc_embeddings, strict=True):
                chunks.append(chunk)
                docs.append(doc)
                embeddings.append(embedding)
        if not chunks:
            return []

        bm25 = bm25_rank(query, chunks)
        vector = vector_rank(self._embedder.embed(query), embeddings)

        # Zero-signal chunks (no query term present, or no positive vector
        # similarity) are excluded from ranking entirely. Without this,
        # RRF hands every chunk a nonzero score and every query returns
        # `limit` results regardless of relevance — pressure to fill a
        # page, which invariant 8 forbids.
        bm25_positive = [(pos, s) for pos, s in bm25 if s > 0.0]
        vector_positive = [(pos, s) for pos, s in vector if s > 0.0]
        bm25_ranks = _competition_ranks(bm25_positive)
        vector_ranks = _competition_ranks(vector_positive)

        if mode == "bm25":
            fused = {pos: 1.0 / (RRF_K + r) for pos, r in bm25_ranks.items()}
        elif mode == "vector":
            fused = {pos: 1.0 / (RRF_K + r) for pos, r in vector_ranks.items()}
        else:
            fused = reciprocal_rank_fusion([bm25_positive, vector_positive])

        rank_of = _competition_ranks(bm25)
        vrank_of = _competition_ranks(vector)

        results = [
            ScoredChunk(
                chunk=chunks[pos],
                doc=docs[pos],
                score=fused_score * recency_weight(docs[pos].published_at, as_of),
                bm25_rank=rank_of.get(pos),
                vector_rank=vrank_of.get(pos),
            )
            for pos, fused_score in fused.items()
        ]
        results.sort(key=lambda r: r.score, reverse=True)
        if results:
            floor = results[0].score * MIN_SCORE_FRACTION
            results = [r for r in results if r.score >= floor]
        return results[:limit]
