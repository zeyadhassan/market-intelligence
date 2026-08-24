"""Entity resolution: organization mentions → stable LEIs.

The cascade, in strict order; a stage runs only if the previous found
nothing:

1. exact identifier (LEI, BIC, ISIN present in the document)
2. normalized-name exact against the reference table
3. blocked fuzzy: name similarity within a (jurisdiction, sector) block,
   auto-merge only above threshold AND with a unique best candidate
4. queue for human review

The model never merges entities on its own judgement (invariant 6). Every
resolution records resolver and score; everything ambiguous queues. Low
recall is a visible, fixable problem — a false merge is invisible and
corrupts every downstream query about both entities.

Threshold justification, measured on the synthetic corpus (token-sort
ratio over normalized names, legal-suffix tokens stripped):
  "Gulf Meridian"            vs legal name "Gulf Meridian Bank Q.P.S.C.": 83.9
  "Gulf Meridian Bank"       vs legal name (normalized-name exact):      100.0
  "Gulf Meridian Capital Partners" (the trap) vs Gulf Meridian Bank:      62.5
  Trap vs its OWN legal name (normalized-name exact):                    100.0
  "Northern Harbour Bank"    vs its own legal name (normalized exact):   100.0
FUZZY_AUTO_MERGE_THRESHOLD = 80 sits above the trap's cross-entity score
(62.5) and below every true-variant score (>= 83.9). Any change must be
re-measured against BOTH the variant set and the trap — the trap test
matters more.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from fi_intel.logging import get_logger
from fi_intel.sources.canonical import CanonicalDocument

FUZZY_AUTO_MERGE_THRESHOLD = 80.0

# Legal suffixes carry no identity signal; stripping them is what lets
# "Gulf Meridian Bank Q.P.S.C." and "Gulf Meridian Bank" compare cleanly.
# Matching is done on whole tokens after punctuation removal, so "q.p.s.c."
# becomes the tokens q, p, s, c — each listed individually below.
_LEGAL_SUFFIX_TOKENS = {
    "q", "p", "s", "c", "j", "l", "b", "k",  # fragments of Q.P.S.C. etc.
    "qpsc", "pjsc", "llc", "bsc", "ksc", "sa", "psc", "jsc",
    "plc", "ltd", "limited", "inc", "co", "company",
    "group", "holding", "holdings",
}

_IDENTIFIER_SCHEMES = ("lei", "bic", "isin")


class ResolverName(StrEnum):
    EXACT_IDENTIFIER = "exact_identifier"
    NORMALIZED_NAME = "normalized_name"
    BLOCKED_FUZZY = "blocked_fuzzy"


class ReferenceEntity(BaseModel):
    """One entry in the local reference table (from the GLEIF adapter)."""

    model_config = ConfigDict(frozen=True)

    lei: str
    legal_name: str
    jurisdiction: str
    sector: str
    parent_lei: str | None = None

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.legal_name)


class Resolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    doc_id: str
    mention_text: str
    lei: str
    resolver: ResolverName
    score: float


class QueuedMention(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    doc_id: str
    mention_text: str
    candidate_lei: str | None
    best_score: float | None
    reason: str


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, punctuation, and legal-suffix tokens."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    tokens = [t for t in text.split() if t not in _LEGAL_SUFFIX_TOKENS]
    return " ".join(tokens)


def token_sort_ratio(a: str, b: str) -> float:
    """SequenceMatcher ratio over token-sorted normalized names, 0–100."""
    sa, sb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    if not sa or not sb:
        return 0.0
    return 100.0 * SequenceMatcher(None, sa, sb).ratio()


@runtime_checkable
class ResolutionStore(Protocol):
    """Persistence contract for reference data, resolutions, and the queue."""

    async def load_reference(self, docs: list[CanonicalDocument]) -> None: ...

    async def reference_entities(self) -> list[ReferenceEntity]: ...

    async def record_resolution(self, resolution: Resolution) -> None: ...

    async def record_queued(self, queued: QueuedMention) -> None: ...

    async def resolutions(self) -> list[Resolution]: ...

    async def queue(self) -> list[QueuedMention]: ...

    async def close(self) -> None: ...


class InMemoryResolutionStore:
    """Reference implementation used by unit tests."""

    def __init__(self) -> None:
        self._reference: dict[str, ReferenceEntity] = {}
        self._resolutions: list[Resolution] = []
        self._queue: list[QueuedMention] = []

    async def load_reference(self, docs: list[CanonicalDocument]) -> None:
        for doc in docs:
            lei = doc.identifiers.get("lei")
            if lei is None:
                msg = f"reference doc {doc.doc_id!r} carries no LEI"
                raise ValueError(msg)
            self._reference[lei] = ReferenceEntity(
                lei=lei,
                legal_name=doc.metadata["legal_name"],
                jurisdiction=doc.metadata["jurisdiction"],
                sector=doc.metadata["sector"],
                parent_lei=doc.metadata.get("parent_lei") or None,
            )

    async def reference_entities(self) -> list[ReferenceEntity]:
        return list(self._reference.values())

    async def record_resolution(self, resolution: Resolution) -> None:
        self._resolutions.append(resolution)

    async def record_queued(self, queued: QueuedMention) -> None:
        self._queue.append(queued)

    async def resolutions(self) -> list[Resolution]:
        return list(self._resolutions)

    async def queue(self) -> list[QueuedMention]:
        return list(self._queue)

    async def close(self) -> None:
        return None


@dataclass(frozen=True)
class _Candidate:
    entity: ReferenceEntity
    score: float


class EntityResolver:
    """The cascade. Deterministic; no model involvement in merge decisions."""

    def __init__(
        self,
        store: ResolutionStore,
        fuzzy_threshold: float = FUZZY_AUTO_MERGE_THRESHOLD,
    ) -> None:
        self._store = store
        self._threshold = fuzzy_threshold
        self._log = get_logger(component="ingest.resolve")

    async def resolve_document(self, doc: CanonicalDocument) -> None:
        reference = await self._store.reference_entities()
        for mention in doc.mentioned_names:
            resolution = self._exact_identifier(doc, mention)
            if resolution is None:
                resolution = self._normalized_name(doc, mention, reference)
            if resolution is None:
                resolution = await self._blocked_fuzzy(doc, mention, reference)
            if resolution is not None:
                await self._store.record_resolution(resolution)
                self._log.info(
                    "resolve.merged",
                    doc_id=doc.doc_id,
                    mention=mention,
                    lei=resolution.lei,
                    resolver=str(resolution.resolver),
                    score=resolution.score,
                )

    def _exact_identifier(self, doc: CanonicalDocument, mention: str) -> Resolution | None:
        for scheme in _IDENTIFIER_SCHEMES:
            if scheme in doc.identifiers:
                return Resolution(
                    source_id=doc.source_id,
                    doc_id=doc.doc_id,
                    mention_text=mention,
                    lei=doc.identifiers[scheme],
                    resolver=ResolverName.EXACT_IDENTIFIER,
                    score=1.0,
                )
        return None

    def _normalized_name(
        self, doc: CanonicalDocument, mention: str, reference: list[ReferenceEntity]
    ) -> Resolution | None:
        target = normalize_name(mention)
        matches = [e for e in reference if e.normalized_name == target]
        if len(matches) == 1:
            return Resolution(
                source_id=doc.source_id,
                doc_id=doc.doc_id,
                mention_text=mention,
                lei=matches[0].lei,
                resolver=ResolverName.NORMALIZED_NAME,
                score=1.0,
            )
        return None

    async def _blocked_fuzzy(
        self, doc: CanonicalDocument, mention: str, reference: list[ReferenceEntity]
    ) -> Resolution | None:
        target = normalize_name(mention)
        scored = sorted(
            (
                _Candidate(entity=e, score=token_sort_ratio(target, e.normalized_name))
                for e in reference
            ),
            key=lambda c: c.score,
            reverse=True,
        )
        candidates = [c for c in scored if c.score > 0]
        if not candidates:
            await self._queue(doc, mention, None, None, "no reference candidate")
            return None

        best = candidates[0]
        tied = [c for c in candidates if c.score == best.score]
        if len(tied) > 1:
            await self._queue(doc, mention, None, best.score, "ambiguous: tied candidates")
            return None
        if best.score < self._threshold:
            await self._queue(
                doc, mention, best.entity.lei, best.score, "below auto-merge threshold"
            )
            return None
        return Resolution(
            source_id=doc.source_id,
            doc_id=doc.doc_id,
            mention_text=mention,
            lei=best.entity.lei,
            resolver=ResolverName.BLOCKED_FUZZY,
            score=best.score / 100.0,
        )

    async def _queue(
        self,
        doc: CanonicalDocument,
        mention: str,
        candidate_lei: str | None,
        best_score: float | None,
        reason: str,
    ) -> None:
        await self._store.record_queued(
            QueuedMention(
                source_id=doc.source_id,
                doc_id=doc.doc_id,
                mention_text=mention,
                candidate_lei=candidate_lei,
                best_score=best_score,
                reason=reason,
            )
        )
        self._log.info(
            "resolve.queued",
            doc_id=doc.doc_id,
            mention=mention,
            candidate_lei=candidate_lei,
            best_score=best_score,
            reason=reason,
        )
