"""Conservative Unicode normalization for exact entity-name matching."""

from __future__ import annotations

import unicodedata


class NameNormalizationError(ValueError):
    """A name contains unsafe or non-comparable Unicode content."""


def normalize_entity_name(value: str) -> str:
    """Normalize canonical equivalents without folding scripts or punctuation.

    NFKC and case folding make ordinary casing and composed-character variants
    comparable. Format/control characters are rejected instead of discarded,
    so zero-width and bidirectional-spoofed names cannot collide silently.
    """
    normalized = unicodedata.normalize("NFKC", value)
    for char in normalized:
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Cs", "Co", "Cn"}:
            raise NameNormalizationError(
                f"entity name contains disallowed Unicode category {category}"
            )
    normalized = " ".join(normalized.split()).casefold()
    if not normalized:
        raise NameNormalizationError("entity name cannot be blank")
    return normalized


__all__ = ["NameNormalizationError", "normalize_entity_name"]
