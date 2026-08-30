"""Shared timezone-aware parsing and validation helpers."""

from datetime import UTC, datetime


def require_aware(value: datetime, *, name: str = "timestamp") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def parse_aware_datetime(value: str, *, name: str = "timestamp") -> datetime:
    """Parse ISO-8601 and convert the represented instant to UTC.

    Offset-aware inputs are converted, never relabelled.  Naive inputs are
    rejected because silently guessing a timezone corrupts temporal replay.
    """

    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO-8601 timestamp") from exc
    return require_aware(parsed, name=name).astimezone(UTC)


__all__ = ["parse_aware_datetime", "require_aware"]
