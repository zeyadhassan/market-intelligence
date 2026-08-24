"""Exact and near-duplicate detection.

Exact dedupe is content-hash equality (CanonicalDocument.content_hash).

Near-duplicate detection catches the same story carried by multiple wires
with different wording. It uses word-3-shingle Jaccard similarity over
normalized title+body. The threshold is a business rule and therefore
lives in code, not a prompt.

Threshold justification, measured on the synthetic corpus (the labelled
ground truth for this detector):
  SW-2024-0001 vs SW-2024-0003 (same story, two wires):  Jaccard = 0.684
  SW-2024-0001 vs SW-2024-0004 (different stories,
    same entity, same event window):                     Jaccard = 0.010
  Northern Harbour decoy docs, pairwise:                 Jaccard <= 0.016
0.55 sits below the positive pair and far above every negative pair.
Any change to NEAR_DUP_THRESHOLD must be re-measured against both the
positive pair AND the decoy pairs — precision is the metric that decides
whether anyone reads the second daily brief.
"""

from dataclasses import dataclass

from fi_intel.sources.canonical import CanonicalDocument

NEAR_DUP_THRESHOLD = 0.55
_SHINGLE_SIZE = 3


def _normalize(text: str) -> list[str]:
    return text.lower().split()


def _shingles(tokens: list[str], size: int = _SHINGLE_SIZE) -> frozenset[tuple[str, ...]]:
    if len(tokens) < size:
        # Very short texts: fall back to unigrams so they can still match.
        return frozenset((t,) for t in tokens)
    return frozenset(tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1))


def _doc_shingles(doc: CanonicalDocument) -> frozenset[tuple[str, ...]]:
    return _shingles(_normalize(doc.title + "\n" + doc.body))


def jaccard(a: frozenset[tuple[str, ...]], b: frozenset[tuple[str, ...]]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class DuplicateVerdict:
    """A near-duplicate call, recorded with its score for auditability."""

    doc: CanonicalDocument
    canonical: CanonicalDocument
    similarity: float


class DedupeIndex:
    """Tracks seen documents and classifies new ones.

    Exact duplicates are dropped silently (idempotent re-ingest is the
    normal case). Near-duplicates are returned as verdicts so the caller
    can persist the linkage with the similarity score — a dedupe decision
    is data and must be auditable.
    """

    def __init__(self, threshold: float = NEAR_DUP_THRESHOLD) -> None:
        self._threshold = threshold
        self._by_hash: dict[str, CanonicalDocument] = {}
        self._shingles: dict[str, frozenset[tuple[str, ...]]] = {}

    def load(self, docs: list[CanonicalDocument]) -> None:
        """Seed from already-persisted documents (resume path)."""
        for doc in docs:
            self._by_hash.setdefault(doc.content_hash(), doc)
            self._shingles.setdefault(doc.doc_id, _doc_shingles(doc))

    def classify(self, doc: CanonicalDocument) -> CanonicalDocument | DuplicateVerdict | None:
        """Return the doc if novel, a verdict if near-dup, None if exact dup."""
        content_hash = doc.content_hash()
        if content_hash in self._by_hash:
            return None

        shingles = _doc_shingles(doc)
        best: CanonicalDocument | None = None
        best_score = 0.0
        for seen_id, seen_shingles in self._shingles.items():
            score = jaccard(shingles, seen_shingles)
            if score > best_score:
                best_score = score
                best = self._by_hash_value(seen_id)
        if best is not None and best_score >= self._threshold:
            return DuplicateVerdict(doc=doc, canonical=best, similarity=best_score)

        self._by_hash[content_hash] = doc
        self._shingles[doc.doc_id] = shingles
        return doc

    def _by_hash_value(self, doc_id: str) -> CanonicalDocument | None:
        for doc in self._by_hash.values():
            if doc.doc_id == doc_id:
                return doc
        return None
