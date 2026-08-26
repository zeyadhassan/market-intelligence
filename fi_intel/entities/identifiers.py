"""Scheme-aware normalization and validation for entity identifiers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class IdentifierValidationError(ValueError):
    """An identifier is malformed or lacks its required scope."""


class IdentifierScheme(StrEnum):
    LEI = "lei"
    BIC = "bic"
    CIK = "cik"
    ISIN = "isin"
    TICKER = "ticker"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class NormalizedIdentifier:
    scheme: IdentifierScheme
    value: str
    scope: str = ""

    @property
    def match_key(self) -> tuple[str, str, str]:
        return self.scheme.value, self.scope, self.value


_LEI = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
_BIC = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$")
_CIK = re.compile(r"^[0-9]{1,10}$")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,31}$")
_VENUE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,15}$")
_INTERNAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


def normalize_identifier(
    scheme: IdentifierScheme | str,
    value: str,
    *,
    venue: str | None = None,
    namespace: str | None = None,
) -> NormalizedIdentifier:
    """Return a canonical identifier or fail closed.

    Schemes remain part of every match key. Tickers require a venue and
    internal identifiers require a namespace, preventing cross-domain joins.
    """
    try:
        parsed_scheme = IdentifierScheme(scheme)
    except ValueError as exc:
        raise IdentifierValidationError(f"unsupported identifier scheme {scheme!r}") from exc

    canonical = unicodedata.normalize("NFKC", value).strip()
    if not canonical:
        raise IdentifierValidationError("identifier cannot be blank")
    if not canonical.isascii():
        raise IdentifierValidationError("identifiers must contain ASCII characters only")

    if parsed_scheme in {IdentifierScheme.TICKER, IdentifierScheme.INTERNAL}:
        return _normalize_scoped_identifier(
            parsed_scheme,
            canonical,
            original=value,
            venue=venue,
            namespace=namespace,
        )
    if venue is not None or namespace is not None:
        raise IdentifierValidationError(
            f"{parsed_scheme.value} identifiers cannot carry venue or namespace scope"
        )
    return _normalize_global_identifier(parsed_scheme, canonical, original=value)


def normalize_synthetic_fixture_identifier(value: str) -> NormalizedIdentifier:
    """Keep legacy synthetic keys usable without misrepresenting them as LEIs."""
    return normalize_identifier(
        IdentifierScheme.INTERNAL,
        value,
        namespace="synthetic-fixture",
    )


def _normalize_scoped_identifier(
    scheme: IdentifierScheme,
    canonical: str,
    *,
    original: str,
    venue: str | None,
    namespace: str | None,
) -> NormalizedIdentifier:
    if scheme is IdentifierScheme.TICKER:
        if namespace is not None:
            raise IdentifierValidationError("ticker identifiers cannot use a namespace")
        normalized_venue = _normalize_scope(venue, "ticker venue", _VENUE, uppercase=True)
        normalized_value = canonical.upper()
        if _TICKER.fullmatch(normalized_value) is None:
            raise IdentifierValidationError(f"invalid ticker {original!r}")
        return NormalizedIdentifier(scheme, normalized_value, normalized_venue)
    if venue is not None:
        raise IdentifierValidationError("internal identifiers cannot use a venue")
    normalized_namespace = _normalize_scope(
        namespace,
        "internal identifier namespace",
        _NAMESPACE,
        uppercase=False,
    )
    if _INTERNAL.fullmatch(canonical) is None:
        raise IdentifierValidationError(f"invalid internal identifier {original!r}")
    return NormalizedIdentifier(scheme, canonical, normalized_namespace)


def _normalize_global_identifier(
    scheme: IdentifierScheme,
    canonical: str,
    *,
    original: str,
) -> NormalizedIdentifier:
    normalized_value = canonical.upper()
    if scheme is IdentifierScheme.LEI:
        if _LEI.fullmatch(normalized_value) is None or _mod_97(normalized_value) != 1:
            raise IdentifierValidationError(
                f"invalid LEI checksum or format {original!r}"
            )
    elif scheme is IdentifierScheme.BIC:
        if _BIC.fullmatch(normalized_value) is None:
            raise IdentifierValidationError(f"invalid BIC {original!r}")
    elif scheme is IdentifierScheme.CIK:
        normalized_value = re.sub(r"(?i)^CIK\s*", "", canonical)
        if _CIK.fullmatch(normalized_value) is None or int(normalized_value) == 0:
            raise IdentifierValidationError(f"invalid CIK {original!r}")
        normalized_value = normalized_value.zfill(10)
    elif scheme is IdentifierScheme.ISIN:
        if _ISIN.fullmatch(normalized_value) is None or not _valid_isin_checksum(
            normalized_value
        ):
            raise IdentifierValidationError(
                f"invalid ISIN checksum or format {original!r}"
            )
    return NormalizedIdentifier(scheme, normalized_value)


def _normalize_scope(
    value: str | None,
    label: str,
    pattern: re.Pattern[str],
    *,
    uppercase: bool,
) -> str:
    if value is None:
        raise IdentifierValidationError(f"{label} is required")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if uppercase:
        normalized = normalized.upper()
    if not normalized.isascii() or pattern.fullmatch(normalized) is None:
        raise IdentifierValidationError(f"invalid {label} {value!r}")
    return normalized


def _mod_97(value: str) -> int:
    remainder = 0
    for char in value:
        expanded = char if char.isdigit() else str(ord(char) - ord("A") + 10)
        for digit in expanded:
            remainder = (remainder * 10 + int(digit)) % 97
    return remainder


def _valid_isin_checksum(value: str) -> bool:
    expanded = "".join(char if char.isdigit() else str(ord(char) - ord("A") + 10) for char in value)
    total = 0
    for position, digit in enumerate(reversed(expanded)):
        number = int(digit)
        if position % 2 == 1:
            number *= 2
        total += number // 10 + number % 10
    return total % 10 == 0


__all__ = [
    "IdentifierScheme",
    "IdentifierValidationError",
    "NormalizedIdentifier",
    "normalize_identifier",
    "normalize_synthetic_fixture_identifier",
]
