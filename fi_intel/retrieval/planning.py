"""Typed evidence-need planning, tiered fallback, and diversity policy."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from fi_intel.retrieval.corpus import ScoredChunk


class RetrievalFallbackTier(StrEnum):
    CANONICAL_ENTITY = "canonical_entity"
    RELATED_ENTITY = "related_entity"
    BROADER_CORPUS = "broader_corpus"
    ABSTAINED = "abstained"


class EvidencePolarity(StrEnum):
    SUPPORT = "support"
    CONTRADICTION = "contradiction"


class RetrievalQueryPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_name: str = Field(min_length=1)
    canonical_entity_lei: str | None = None
    aliases: tuple[str, ...] = ()
    related_entity_leis: tuple[str, ...] = ()
    instrument_keys: tuple[str, ...] = ()
    event_type: str = Field(min_length=1)
    date_from: date | None = None
    date_to: date | None = None
    source_ids: frozenset[str] | None = None
    support_terms: tuple[str, ...] = ()
    contradiction_terms: tuple[str, ...] = (
        "withdrawn",
        "cancelled",
        "denied",
        "rejected",
        "completed",
        "refinanced",
        "superseded",
        "corrected",
    )
    limit: int = Field(default=10, ge=1, le=50)

    def query(self, polarity: EvidencePolarity) -> str:
        terms = [
            self.entity_name,
            *self.aliases,
            *self.instrument_keys,
            self.event_type.replace("_", " "),
            *(
                self.support_terms
                if polarity is EvidencePolarity.SUPPORT
                else self.contradiction_terms
            ),
        ]
        return " ".join(dict.fromkeys(item.strip() for item in terms if item.strip()))


class RetrievalDiagnostics(BaseModel):
    model_config = ConfigDict(frozen=True)

    query_digest: str
    fallback_tier: RetrievalFallbackTier
    polarity: EvidencePolarity
    candidate_count: int = Field(ge=0)
    admitted_count: int = Field(ge=0)
    model_version: str
    filters: dict[str, str] = Field(default_factory=dict)


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    results: tuple[ScoredChunk, ...]
    diagnostics: RetrievalDiagnostics


def query_digest(query: str) -> str:
    return hashlib.sha256(" ".join(query.split()).encode()).hexdigest()


def normalized_text_fingerprint(text: str) -> str:
    normalized = re.sub(r"\W+", " ", text.casefold()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


def diversify_results(
    results: list[ScoredChunk],
    *,
    limit: int,
    max_chunks_per_document: int = 2,
    max_chunks_per_source: int = 5,
) -> list[ScoredChunk]:
    """Collapse normalized duplicates and cap document/source concentration."""

    document_counts: dict[tuple[str, str], int] = {}
    source_counts: dict[str, int] = {}
    seen_fingerprints: set[str] = set()
    admitted: list[ScoredChunk] = []
    for result in results:
        document_key = (result.doc.source_id, result.doc.doc_id)
        fingerprint = normalized_text_fingerprint(result.chunk.text)
        if fingerprint in seen_fingerprints:
            continue
        if document_counts.get(document_key, 0) >= max_chunks_per_document:
            continue
        if source_counts.get(result.doc.source_id, 0) >= max_chunks_per_source:
            continue
        admitted.append(result)
        seen_fingerprints.add(fingerprint)
        document_counts[document_key] = document_counts.get(document_key, 0) + 1
        source_counts[result.doc.source_id] = source_counts.get(result.doc.source_id, 0) + 1
        if len(admitted) >= limit:
            break
    return admitted


__all__ = [
    "EvidenceBundle",
    "EvidencePolarity",
    "RetrievalDiagnostics",
    "RetrievalFallbackTier",
    "RetrievalQueryPlan",
    "diversify_results",
    "normalized_text_fingerprint",
    "query_digest",
]
