"""Hybrid corpus search: BM25 + vector, fused, recency-weighted.

Ranking pipeline, all of it deterministic and in code (business rules do
not live in prompts):
  1. candidate generation: BM25 over chunk text, cosine over embeddings
  2. per-leg relevance admission on normalized BM25 or cosine similarity
  3. reciprocal rank fusion of the admitted ranked lists
  4. multiplicative recency weight: exponential decay with
     RECENCY_HALF_LIFE_DAYS, measured against the labelled relevance set
     (tests/test_retrieval.py reports all three of BM25-only, vector-only,
     hybrid — tune against that set, never against a single query)

Entitlement and as-of filtering are NOT done here. They are applied by the
store's candidate query (SQL in production, the ported predicate in
tests). This module only ever sees documents the caller may read.
"""

import math
import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from fi_intel.retrieval.chunking import Chunk, Embedder, cosine
from fi_intel.retrieval.entitlement import Principal
from fi_intel.sources.canonical import CanonicalDocument, document_text

RRF_K = 60  # standard RRF constant; rank fusion is insensitive near it
RECENCY_HALF_LIFE_DAYS = 90.0
#: Oldest documents keep this fraction of a fresh document's weight.
RECENCY_FLOOR = 0.5
DEFAULT_LIMIT = 10
MIN_INDEXED_CANDIDATES = 50
INDEXED_CANDIDATE_MULTIPLIER = 5
MAX_INDEXED_CANDIDATES = 1000
#: Chunks whose fused rank score falls below this fraction of the top
#: result are noise, not weak matches. Without a floor, every query returns
#: `limit` results regardless of relevance — which pressures the system to
#: fill a page with irrelevant matches.
# Candidates clearing either active leg remain eligible before fusion, so
# adding or removing a leg changes rank without changing admission by itself.
MIN_NORMALIZED_BM25_SCORE = 0.10
MIN_COSINE_SIMILARITY = 0.10


class ScoredChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk: Chunk
    doc: CanonicalDocument
    score: float
    bm25_rank: int | None
    vector_rank: int | None
    bm25_score: float | None = None
    vector_score: float | None = None
    reranker_score: float | None = None


class IndexedCandidate(BaseModel):
    """One bounded SQL-generated candidate with per-leg ranks."""

    model_config = ConfigDict(frozen=True)

    chunk: Chunk
    doc: CanonicalDocument
    bm25_rank: int | None
    vector_rank: int | None
    bm25_score: float | None = None
    vector_score: float | None = None


@runtime_checkable
class CorpusStore(Protocol):
    """Candidate access with entitlement and as-of already applied."""

    async def candidates(
        self,
        principal: Principal,
        as_of: datetime | None,
        entity_lei: str | None = None,
        source_ids: set[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[tuple[CanonicalDocument, list[Chunk], list[list[float]]]]:
        """(document, its chunks, chunk embeddings) the caller may see."""
        ...


@runtime_checkable
class IndexedCorpusStore(Protocol):
    """Production candidate generation performed by database indexes."""

    async def indexed_candidates(
        self,
        query: str,
        query_embedding: list[float],
        *,
        embed_model_version: str,
        embedding_dim: int,
        principal: Principal,
        as_of: datetime | None,
        entity_lei: str | None,
        source_ids: set[str] | None,
        date_from: date | None,
        date_to: date | None,
        mode: str,
        candidate_limit: int,
    ) -> list[IndexedCandidate]: ...


@runtime_checkable
class SpanCorpusStore(Protocol):
    """Resolve one entitled canonical document for citation validation."""

    async def resolve_document(
        self,
        principal: Principal,
        source_id: str,
        doc_id: str,
        as_of: datetime | None,
    ) -> CanonicalDocument | None: ...


@runtime_checkable
class BatchSpanCorpusStore(Protocol):
    """Resolve several documents through one entitlement-safe store call."""

    async def resolve_documents(
        self,
        principal: Principal,
        document_keys: tuple[tuple[str, str], ...],
        as_of: datetime | None,
    ) -> dict[tuple[str, str], CanonicalDocument]: ...


_ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
_ARABIC_TRANSLATION: dict[str, str | int | None] = {
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",
    "ٱ": "ا",
    "ى": "ي",
    "ـ": "",
}


def normalize_retrieval_text(text: str) -> str:
    """Normalize English/Arabic text without changing stored citation offsets."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = _ARABIC_DIACRITICS.sub("", normalized)
    normalized = normalized.translate(str.maketrans(_ARABIC_TRANSLATION))
    return " ".join(re.findall(r"[\w]+(?:-[\w]+)*", normalized, flags=re.UNICODE))


def _tokenize(text: str) -> list[str]:
    return normalize_retrieval_text(text).split()


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
    def __init__(
        self,
        store: CorpusStore | IndexedCorpusStore,
        embedder: Embedder,
        *,
        min_normalized_bm25_score: float = MIN_NORMALIZED_BM25_SCORE,
        min_cosine_similarity: float = MIN_COSINE_SIMILARITY,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._min_normalized_bm25_score = min_normalized_bm25_score
        self._min_cosine_similarity = min_cosine_similarity

    @property
    def model_version(self) -> str:
        return self._embedder.model_version

    async def resolve_span(
        self,
        principal: Principal,
        source_id: str,
        doc_id: str,
        start: int,
        end: int,
        as_of: datetime | None,
    ) -> tuple[CanonicalDocument, str] | None:
        """Resolve an exact span through the same data-layer policy boundary."""
        if not isinstance(self._store, SpanCorpusStore):
            msg = "corpus store cannot resolve evidence spans"
            raise TypeError(msg)
        doc = await self._store.resolve_document(principal, source_id, doc_id, as_of)
        if doc is None:
            return None
        text = document_text(doc)
        if not 0 <= start < end <= len(text):
            return None
        return doc, text[start:end]

    async def resolve_spans(
        self,
        principal: Principal,
        spans: tuple[tuple[str, str, int, int], ...],
        as_of: datetime | None,
    ) -> dict[tuple[str, str, int, int], tuple[CanonicalDocument, str]]:
        keys = tuple(dict.fromkeys((source_id, doc_id) for source_id, doc_id, _, _ in spans))
        if isinstance(self._store, BatchSpanCorpusStore):
            documents = await self._store.resolve_documents(principal, keys, as_of)
        elif isinstance(self._store, SpanCorpusStore):
            documents = {}
            for source_id, doc_id in keys:
                document = await self._store.resolve_document(principal, source_id, doc_id, as_of)
                if document is not None:
                    documents[(source_id, doc_id)] = document
        else:
            raise TypeError("corpus store cannot resolve evidence spans")
        resolved: dict[tuple[str, str, int, int], tuple[CanonicalDocument, str]] = {}
        for span in spans:
            source_id, doc_id, start, end = span
            document = documents.get((source_id, doc_id))
            if document is None:
                continue
            text = document_text(document)
            if 0 <= start < end <= len(text):
                resolved[span] = (document, text[start:end])
        return resolved

    async def search(
        self,
        query: str,
        principal: Principal,
        as_of: datetime | None = None,
        entity_lei: str | None = None,
        source_ids: set[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = DEFAULT_LIMIT,
        mode: str = "hybrid",
    ) -> list[ScoredChunk]:
        if mode not in ("hybrid", "bm25", "vector"):
            msg = f"unknown search mode {mode!r}"
            raise ValueError(msg)
        if limit < 1:
            msg = "limit must be >= 1"
            raise ValueError(msg)

        if isinstance(self._store, IndexedCorpusStore):
            return await self._search_indexed(
                query,
                principal,
                as_of,
                entity_lei,
                source_ids,
                date_from,
                date_to,
                limit,
                mode,
            )

        return await self._search_in_memory(
            query,
            principal,
            as_of,
            entity_lei,
            source_ids,
            date_from,
            date_to,
            limit,
            mode,
        )

    async def _search_in_memory(
        self,
        query: str,
        principal: Principal,
        as_of: datetime | None,
        entity_lei: str | None,
        source_ids: set[str] | None,
        date_from: date | None,
        date_to: date | None,
        limit: int,
        mode: str,
    ) -> list[ScoredChunk]:
        if not isinstance(self._store, CorpusStore):
            msg = "in-memory search requested for a non-exhaustive store"
            raise TypeError(msg)

        candidates = await self._store.candidates(
            principal,
            as_of,
            entity_lei=entity_lei,
            source_ids=source_ids,
            date_from=date_from,
            date_to=date_to,
        )

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
        (query_embedding,) = await self._embedder.embed_batch([query], kind="query")
        vector = vector_rank(query_embedding, embeddings)

        # Zero-signal chunks (no query term present, or no positive vector
        # similarity) are excluded from ranking entirely. Without this,
        # RRF hands every chunk a nonzero score and every query returns
        # `limit` results regardless of relevance.
        bm25_positive = [(pos, s) for pos, s in bm25 if s > 0.0]
        vector_positive = [(pos, s) for pos, s in vector if s > 0.0]
        max_bm25 = max((score for _pos, score in bm25_positive), default=0.0)
        bm25_scores = {pos: score / max_bm25 for pos, score in bm25_positive if max_bm25 > 0.0}
        vector_scores = dict(vector_positive)
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
                bm25_score=bm25_scores.get(pos),
                vector_score=vector_scores.get(pos),
            )
            for pos, fused_score in fused.items()
            if self._eligible(mode, bm25_scores.get(pos), vector_scores.get(pos))
        ]
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    async def _search_indexed(
        self,
        query: str,
        principal: Principal,
        as_of: datetime | None,
        entity_lei: str | None,
        source_ids: set[str] | None,
        date_from: date | None,
        date_to: date | None,
        limit: int,
        mode: str,
    ) -> list[ScoredChunk]:
        if not isinstance(self._store, IndexedCorpusStore):
            msg = "indexed search requested for a non-indexed store"
            raise TypeError(msg)
        (query_embedding,) = await self._embedder.embed_batch([query], kind="query")
        candidate_limit = min(
            MAX_INDEXED_CANDIDATES,
            max(MIN_INDEXED_CANDIDATES, limit * INDEXED_CANDIDATE_MULTIPLIER),
        )
        indexed = await self._store.indexed_candidates(
            query,
            query_embedding,
            embed_model_version=self._embedder.model_version,
            embedding_dim=self._embedder.dim,
            principal=principal,
            as_of=as_of,
            entity_lei=entity_lei,
            source_ids=source_ids,
            date_from=date_from,
            date_to=date_to,
            mode=mode,
            candidate_limit=candidate_limit,
        )
        return self._rank_indexed(indexed, as_of, limit, mode)

    def _rank_indexed(
        self,
        candidates: list[IndexedCandidate],
        as_of: datetime | None,
        limit: int,
        mode: str,
    ) -> list[ScoredChunk]:
        results: list[ScoredChunk] = []
        for candidate in candidates:
            if not self._eligible(mode, candidate.bm25_score, candidate.vector_score):
                continue
            score = 0.0
            if mode in {"hybrid", "bm25"} and candidate.bm25_rank is not None:
                score += 1.0 / (RRF_K + candidate.bm25_rank)
            if mode in {"hybrid", "vector"} and candidate.vector_rank is not None:
                score += 1.0 / (RRF_K + candidate.vector_rank)
            if score <= 0.0:
                continue
            results.append(
                ScoredChunk(
                    chunk=candidate.chunk,
                    doc=candidate.doc,
                    score=score * recency_weight(candidate.doc.published_at, as_of),
                    bm25_rank=candidate.bm25_rank,
                    vector_rank=candidate.vector_rank,
                    bm25_score=candidate.bm25_score,
                    vector_score=candidate.vector_score,
                )
            )
        results.sort(
            key=lambda result: (
                -result.score,
                result.doc.source_id,
                result.doc.doc_id,
                result.chunk.chunk_index,
            )
        )
        return results[:limit]

    def _eligible(
        self,
        mode: str,
        bm25_score: float | None,
        vector_score: float | None,
    ) -> bool:
        lexical = (
            mode in {"hybrid", "bm25"}
            and bm25_score is not None
            and bm25_score >= self._min_normalized_bm25_score
        )
        semantic = (
            mode in {"hybrid", "vector"}
            and vector_score is not None
            and vector_score >= self._min_cosine_similarity
        )
        return lexical or semantic
