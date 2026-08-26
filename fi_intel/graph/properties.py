"""Governed projection of assertion attributes into typed graph properties."""

import math
from collections.abc import Callable


class TypedPropertyError(ValueError):
    """A recognized detector attribute could not be converted safely."""


def _text(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise TypedPropertyError("typed text property cannot be empty")
    return normalized


def _number(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise TypedPropertyError(f"invalid numeric detector property {value!r}") from exc
    if not math.isfinite(number):
        raise TypedPropertyError(f"non-finite numeric detector property {value!r}")
    return number


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise TypedPropertyError(f"invalid boolean detector property {value!r}")


_PROPERTY_PROJECTIONS: dict[str, tuple[str, Callable[[str], str | float | bool]]] = {
    "direction": ("fact_direction", _text),
    "outlook": ("fact_outlook", _text),
    "metric": ("fact_metric", _text),
    "rating_type": ("fact_rating_type", _text),
    "role": ("fact_role", _text),
    "class": ("fact_class", _text),
    "programme": ("fact_programme", _text),
    "currency": ("fact_currency", _text),
    "status": ("fact_status", _text),
    "value": ("fact_value", _number),
    "prior": ("fact_prior", _number),
    "limit_usd_bn": ("fact_limit_usd_bn", _number),
    "amount_usd_mn": ("fact_amount_usd_mn", _number),
    "coverage_ratio": ("fact_coverage_ratio", _number),
    "marketed": ("fact_marketed", _boolean),
}

PROJECTED_PROPERTY_NAMES = frozenset(_PROPERTY_PROJECTIONS)


def project_typed_properties(properties: dict[str, str]) -> dict[str, str | float | bool]:
    """Validate and project the governed subset used by pattern queries.

    Unrecognized attributes remain available in ``properties_json`` but cannot
    influence a detector until they are admitted to this explicit registry.
    """

    projected: dict[str, str | float | bool] = {}
    for source_name, (graph_name, converter) in _PROPERTY_PROJECTIONS.items():
        value = properties.get(source_name)
        if value is not None:
            projected[graph_name] = converter(value)
    return projected
