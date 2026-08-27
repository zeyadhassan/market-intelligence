"""Deterministic claim grounding before optional semantic verification."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from fi_intel.tools.evidence import EvidenceItem, FieldEvidenceMapping

_MATERIAL_LITERAL = re.compile(
    r"(?<!\w)(?:(?:USD|AED|SAR|QAR|KWD|BHD|OMR)|"
    r"\d{1,4}(?:[.,]\d+)*(?:\s*(?:%|bn|mn|million|billion))?|"
    r"\d{4}-\d{2}-\d{2})(?!\w)",
    flags=re.IGNORECASE,
)
_TOKEN = re.compile(r"[^\W_]+", flags=re.UNICODE)
_PREDICATE_STEMS = {
    "approv",
    "appoint",
    "call",
    "cancel",
    "complet",
    "downgrad",
    "issu",
    "market",
    "matur",
    "refinanc",
    "resign",
    "upgrad",
    "withdraw",
}
_STATUS_TERMS = frozenset(
    {
        "approved",
        "cancelled",
        "completed",
        "denied",
        "marketed",
        "pending",
        "rejected",
        "refinanced",
        "superseded",
        "withdrawn",
    }
)
_DIRECTION_TERMS = frozenset(
    {"declined", "decreased", "down", "negative", "stable", "up", "increased", "positive"}
)
_CAPITALIZED_ENTITY = re.compile(r"(?<![.])\b(?:[A-Z][\w&.'-]+(?:\s+|$)){1,6}", flags=re.UNICODE)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class GroundingDecision:
    supported: bool
    reasons: tuple[str, ...]
    mappings: tuple[FieldEvidenceMapping, ...]
    requires_semantic_review: bool = False


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _stem(token: str) -> str:
    normalized = token.casefold()
    for suffix in ("ing", "ed", "es", "s"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix) + 3:
            return normalized[: -len(suffix)]
    return normalized


def _present(literal: str, evidence: str) -> bool:
    candidate = _normalized(literal).replace(",", "")
    haystack = _normalized(evidence).replace(",", "")
    if candidate in haystack:
        return True
    # ISO dates may be rendered in ordinary prose.
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", candidate)
    if match:
        year, month, day = match.groups()
        alternatives = {
            f"{day}/{month}/{year}",
            f"{day}-{month}-{year}",
            f"{int(day)} {int(month)} {year}",
        }
        return any(item in haystack for item in alternatives)
    return False


def ground_claim(
    text: str,
    evidence: list[EvidenceItem],
) -> GroundingDecision:
    """Reject missing literals and unsupported high-risk predicate anchors."""

    combined = "\n".join(item.excerpt for item in evidence)
    reasons: list[str] = []
    mappings: list[FieldEvidenceMapping] = []
    for literal in dict.fromkeys(_MATERIAL_LITERAL.findall(text)):
        owners = tuple(item.evidence_id for item in evidence if _present(literal, item.excerpt))
        if not owners:
            reasons.append(f"material literal {literal!r} is absent from cited evidence")
            continue
        mappings.append(
            FieldEvidenceMapping(
                field_name=_literal_field_name(literal),
                value=literal,
                evidence_ids=owners,
            )
        )

    claim_stems = {_stem(token) for token in _TOKEN.findall(text)}
    evidence_stems = {_stem(token) for token in _TOKEN.findall(combined)}
    material_predicates = sorted(claim_stems & _PREDICATE_STEMS)
    absent_predicates = [item for item in material_predicates if item not in evidence_stems]
    if absent_predicates:
        reasons.append(
            "material predicate is absent from cited evidence: " + ", ".join(absent_predicates)
        )
    for predicate in material_predicates:
        owners = tuple(
            item.evidence_id
            for item in evidence
            if predicate in {_stem(token) for token in _TOKEN.findall(item.excerpt)}
        )
        if owners:
            mappings.append(
                FieldEvidenceMapping(
                    field_name="predicate",
                    value=predicate,
                    evidence_ids=owners,
                )
            )
    _map_terms(text, evidence, _STATUS_TERMS, "status", mappings, reasons)
    _map_terms(text, evidence, _DIRECTION_TERMS, "direction", mappings, reasons)
    for entity in dict.fromkeys(
        match.group(0).strip() for match in _CAPITALIZED_ENTITY.finditer(text)
    ):
        owners = tuple(item.evidence_id for item in evidence if _present(entity, item.excerpt))
        if owners:
            mappings.append(
                FieldEvidenceMapping(
                    field_name="entity_surface",
                    value=entity,
                    evidence_ids=owners,
                )
            )
    if not evidence:
        reasons.append("claim has no evidence")
    claim_terms = {
        _stem(token)
        for token in _TOKEN.findall(text)
        if token.casefold() not in _STOPWORDS and len(token) > 2
    }
    missing_terms = sorted(claim_terms - evidence_stems)
    requires_semantic_review = bool(missing_terms) and not reasons
    return GroundingDecision(
        supported=not reasons,
        reasons=tuple(reasons),
        mappings=tuple(_dedupe_mappings(mappings)),
        requires_semantic_review=requires_semantic_review,
    )


def _literal_field_name(literal: str) -> str:
    normalized = literal.strip().upper()
    if re.fullmatch(r"[A-Z]{3}", normalized):
        return "currency"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        return "date"
    return "amount"


def _map_terms(
    text: str,
    evidence: list[EvidenceItem],
    vocabulary: frozenset[str],
    field_name: str,
    mappings: list[FieldEvidenceMapping],
    reasons: list[str],
) -> None:
    terms = sorted({_normalized(token) for token in _TOKEN.findall(text)} & vocabulary)
    for term in terms:
        owners = tuple(item.evidence_id for item in evidence if _present(term, item.excerpt))
        if not owners:
            reasons.append(f"{field_name} {term!r} is absent from cited evidence")
            continue
        mappings.append(
            FieldEvidenceMapping(field_name=field_name, value=term, evidence_ids=owners)
        )


def _dedupe_mappings(
    mappings: list[FieldEvidenceMapping],
) -> list[FieldEvidenceMapping]:
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    result: list[FieldEvidenceMapping] = []
    for mapping in mappings:
        key = (mapping.field_name, mapping.value, mapping.evidence_ids)
        if key not in seen:
            seen.add(key)
            result.append(mapping)
    return result


def title_agrees_with_claims(title: str, claim_texts: list[str]) -> bool:
    """Guard against titles that introduce a different material event."""

    title_stems = {_stem(token) for token in _TOKEN.findall(title)}
    claim_stems = {_stem(token) for text in claim_texts for token in _TOKEN.findall(text)}
    material = title_stems & _PREDICATE_STEMS
    return not material or bool(material & claim_stems)


__all__ = ["GroundingDecision", "ground_claim", "title_agrees_with_claims"]
