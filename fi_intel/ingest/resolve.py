"""Entity resolution: organization mentions → stable LEIs.

The cascade, in strict order; a stage runs only if the previous found
nothing:

1. exact identifier (LEI, BIC, ISIN present in the document)
2. normalized-name exact against the reference table
3. blocked fuzzy: name similarity within a (jurisdiction, sector) block,
   auto-merge only above threshold AND with a unique best candidate
4. queue for human review

Every resolution records its resolver and score. Ambiguous matches are
queued for review instead of merged automatically.
"""

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from fi_intel.logging import get_logger
from fi_intel.sources.canonical import CanonicalDocument

# Calibrated against the labelled GCC name set in ``tests/test_resolve.py``.
# With the threshold unchanged, the three observed false-positive pairs score
# 20--49 while the weakest accepted true variant scores 84.  Auto-merges also
# require a five-point lead over the runner-up; lower-confidence cases remain
# visible in the resolution queue.
FUZZY_AUTO_MERGE_THRESHOLD = 80.0
FUZZY_AUTO_MERGE_MARGIN = 5.0

# Legal suffixes carry no identity signal; stripping them is what lets
# "Gulf Meridian Bank Q.P.S.C." and "Gulf Meridian Bank" compare cleanly.
# Matching is done on whole tokens after punctuation removal, so "q.p.s.c."
# becomes the tokens q, p, s, c — each listed individually below.
_LEGAL_SUFFIX_TOKENS = {
    "qpsc",
    "pjsc",
    "llc",
    "bsc",
    "ksc",
    "sa",
    "psc",
    "jsc",
    "plc",
    "ltd",
    "limited",
    "inc",
    "co",
    "company",
}
_PUNCTUATED_LEGAL_SUFFIXES = {
    ("q", "p", "s", "c"),
    ("p", "j", "s", "c"),
    ("l", "l", "c"),
    ("b", "s", "c"),
    ("k", "s", "c"),
    ("p", "s", "c"),
    ("j", "s", "c"),
    ("s", "a"),
}
_CORPORATE_FAMILY_TOKENS = frozenset({"group", "holding", "holdings"})


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
    recorded_at: AwareDatetime


class DocumentEntityLink(BaseModel):
    """A provenanced resolved identity for one document."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    doc_id: str
    lei: str
    resolver: ResolverName
    score: float = Field(ge=0.0, le=1.0)
    recorded_at: AwareDatetime


class QueuedMention(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    doc_id: str
    mention_text: str
    candidate_lei: str | None
    best_score: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, punctuation, and legal-suffix tokens."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    tokens = text.split()
    for suffix in sorted(_PUNCTUATED_LEGAL_SUFFIXES, key=len, reverse=True):
        if tuple(tokens[-len(suffix) :]) == suffix:
            tokens = tokens[: -len(suffix)]
            break
    while tokens and tokens[-1] in _LEGAL_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _corporate_family_tokens(name: str) -> frozenset[str]:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    tokens = set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())
    return frozenset(tokens & _CORPORATE_FAMILY_TOKENS)


def _family_form_differs(left: str, right: str) -> bool:
    return _corporate_family_tokens(left) != _corporate_family_tokens(right)


def token_sort_ratio(a: str, b: str) -> float:
    """SequenceMatcher ratio over token-sorted normalized names, 0–100."""
    sa, sb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    if not sa or not sb:
        return 0.0
    return 100.0 * SequenceMatcher(None, sa, sb).ratio()


def name_token_idf(reference_names: list[str]) -> dict[str, float]:
    """Return inverse-document-frequency weights for reference-name tokens.

    Common institutional words such as ``bank`` and ``national`` carry little
    identity evidence.  Rare tokens must agree before a fuzzy match can be
    merged automatically.
    """

    documents = [set(normalize_name(name).split()) for name in reference_names]
    total = len(documents)
    frequency: dict[str, int] = {}
    for tokens in documents:
        for token in tokens:
            frequency[token] = frequency.get(token, 0) + 1
    return {
        token: max(0.0, math.log((total + 1) / (count + 0.5))) for token, count in frequency.items()
    }


def weighted_token_ratio(
    a: str,
    b: str,
    idf: dict[str, float],
    default: float,
) -> float:
    """IDF-weighted Jaccard similarity over normalized name tokens, 0--100."""

    left, right = set(a.split()), set(b.split())
    if not left or not right:
        return 0.0
    union = sum(idf.get(token, default) for token in left | right)
    if union <= 0.0:
        return 0.0
    intersection = sum(idf.get(token, default) for token in left & right)
    return 100.0 * intersection / union


def merge_score(
    a: str,
    b: str,
    idf: dict[str, float],
    default: float,
) -> float:
    """Require both character similarity and rare-token agreement."""

    return min(token_sort_ratio(a, b), weighted_token_ratio(a, b, idf, default))


@runtime_checkable
class ResolutionStore(Protocol):
    """Persistence contract for reference data, resolutions, and the queue."""

    async def load_reference(self, docs: list[CanonicalDocument]) -> None: ...

    async def reference_entities(self) -> list[ReferenceEntity]: ...

    async def record_resolution(self, resolution: Resolution) -> None: ...

    async def resolution_for(
        self, source_id: str, doc_id: str, mention_text: str
    ) -> Resolution | None: ...

    async def record_queued(self, queued: QueuedMention) -> None: ...

    async def resolutions(self) -> list[Resolution]: ...

    async def document_entity_links(self) -> list[DocumentEntityLink]: ...

    async def queue(self) -> list[QueuedMention]: ...

    async def close(self) -> None: ...


class InMemoryResolutionStore:
    """Reference implementation used by unit tests."""

    def __init__(self) -> None:
        self._reference: dict[str, ReferenceEntity] = {}
        self._resolutions: list[Resolution] = []
        self._document_entity_links: list[DocumentEntityLink] = []
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
        existing = await self.resolution_for(
            resolution.source_id, resolution.doc_id, resolution.mention_text
        )
        if existing is None:
            self._resolutions.append(resolution)
            self._document_entity_links.append(
                DocumentEntityLink(
                    source_id=resolution.source_id,
                    doc_id=resolution.doc_id,
                    lei=resolution.lei,
                    resolver=resolution.resolver,
                    score=resolution.score,
                    recorded_at=resolution.recorded_at,
                )
            )

    async def resolution_for(
        self, source_id: str, doc_id: str, mention_text: str
    ) -> Resolution | None:
        normalized = normalize_name(mention_text)
        return next(
            (
                resolution
                for resolution in reversed(self._resolutions)
                if resolution.source_id == source_id
                and resolution.doc_id == doc_id
                and normalize_name(resolution.mention_text) == normalized
            ),
            None,
        )

    async def record_queued(self, queued: QueuedMention) -> None:
        if not any(
            item.source_id == queued.source_id
            and item.doc_id == queued.doc_id
            and normalize_name(item.mention_text) == normalize_name(queued.mention_text)
            for item in self._queue
        ):
            self._queue.append(queued)

    async def resolutions(self) -> list[Resolution]:
        return list(self._resolutions)

    async def document_entity_links(self) -> list[DocumentEntityLink]:
        return list(self._document_entity_links)

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

    async def resolve_document(
        self, doc: CanonicalDocument, *, recorded_at: datetime | None = None
    ) -> None:
        decision_at = recorded_at or datetime.now(tz=UTC)
        reference = await self._store.reference_entities()
        for mention in doc.mentioned_names:
            await self._resolve_mention(doc, mention, reference, decision_at)

    async def resolve_mention(
        self,
        doc: CanonicalDocument,
        mention: str,
        *,
        recorded_at: datetime | None = None,
    ) -> Resolution | None:
        """Resolve one evidenced mention and persist the decision idempotently."""
        existing = await self._store.resolution_for(doc.source_id, doc.doc_id, mention)
        if existing is not None:
            return existing
        return await self._resolve_mention(
            doc,
            mention,
            await self._store.reference_entities(),
            recorded_at or datetime.now(tz=UTC),
        )

    async def _resolve_mention(
        self,
        doc: CanonicalDocument,
        mention: str,
        reference: list[ReferenceEntity],
        recorded_at: datetime,
    ) -> Resolution | None:
        if not any(normalize_name(name) == normalize_name(mention) for name in doc.mentioned_names):
            await self._queue(doc, mention, None, None, "mention not declared by document parser")
            return None
        resolution = self._exact_identifier(doc, mention, reference, recorded_at)
        if resolution is None:
            resolution = self._normalized_name(doc, mention, reference, recorded_at)
        if resolution is None:
            resolution = await self._blocked_fuzzy(doc, mention, reference, recorded_at)
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
        return resolution

    def _exact_identifier(
        self,
        doc: CanonicalDocument,
        mention: str,
        reference: list[ReferenceEntity],
        recorded_at: datetime,
    ) -> Resolution | None:
        lei = doc.identifiers.get("lei")
        # A document-level identifier can safely resolve only a single parsed
        # organization mention and only when it exists in the entity master.
        if lei is None or len(doc.mentioned_names) != 1:
            return None
        if not any(entity.lei == lei for entity in reference):
            return None
        return Resolution(
            source_id=doc.source_id,
            doc_id=doc.doc_id,
            mention_text=mention,
            lei=lei,
            resolver=ResolverName.EXACT_IDENTIFIER,
            score=1.0,
            recorded_at=recorded_at,
        )

    def _normalized_name(
        self,
        doc: CanonicalDocument,
        mention: str,
        reference: list[ReferenceEntity],
        recorded_at: datetime,
    ) -> Resolution | None:
        target = normalize_name(mention)
        matches = [
            entity
            for entity in reference
            if entity.normalized_name == target
            and not _family_form_differs(mention, entity.legal_name)
        ]
        if len(matches) == 1:
            return Resolution(
                source_id=doc.source_id,
                doc_id=doc.doc_id,
                mention_text=mention,
                lei=matches[0].lei,
                resolver=ResolverName.NORMALIZED_NAME,
                score=1.0,
                recorded_at=recorded_at,
            )
        return None

    async def _blocked_fuzzy(
        self,
        doc: CanonicalDocument,
        mention: str,
        reference: list[ReferenceEntity],
        recorded_at: datetime,
    ) -> Resolution | None:
        target = normalize_name(mention)
        jurisdiction = str(doc.metadata.get("jurisdiction", "")).strip().casefold()
        sector = str(doc.metadata.get("sector", "")).strip().casefold()
        blocked = [
            entity
            for entity in reference
            if (not jurisdiction or entity.jurisdiction.casefold() == jurisdiction)
            and (not sector or entity.sector.casefold() == sector)
        ]
        if not blocked:
            await self._queue(
                doc,
                mention,
                None,
                None,
                "no reference candidate in jurisdiction/sector block",
            )
            return None

        idf = name_token_idf([entity.legal_name for entity in blocked])
        default_idf = math.log((len(blocked) + 1) / 0.5)
        scored = sorted(
            (
                _Candidate(
                    entity=entity,
                    score=merge_score(target, entity.normalized_name, idf, default_idf),
                )
                for entity in blocked
            ),
            key=lambda c: c.score,
            reverse=True,
        )
        candidates = [c for c in scored if c.score > 0]
        if not candidates:
            await self._queue(doc, mention, None, None, "no reference candidate")
            return None

        best = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        if _family_form_differs(mention, best.entity.legal_name):
            await self._queue(
                doc,
                mention,
                best.entity.lei,
                best.score / 100.0,
                "corporate-family form differs",
            )
            return None
        if best.score < self._threshold:
            await self._queue(
                doc,
                mention,
                best.entity.lei,
                best.score / 100.0,
                "below auto-merge threshold",
            )
            return None
        if runner_up is not None and best.score - runner_up.score < FUZZY_AUTO_MERGE_MARGIN:
            await self._queue(
                doc,
                mention,
                None,
                best.score / 100.0,
                "ambiguous: best candidate lacks required score margin",
            )
            return None
        return Resolution(
            source_id=doc.source_id,
            doc_id=doc.doc_id,
            mention_text=mention,
            lei=best.entity.lei,
            resolver=ResolverName.BLOCKED_FUZZY,
            score=best.score / 100.0,
            recorded_at=recorded_at,
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
