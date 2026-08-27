"""Stable daily window identity and the canonical processed-input use case.

Acquisition and projection intentionally do not live here.  Process entry
points in :mod:`fi_intel.application.workers` own those stages; durable
analysis jobs are consumed by :mod:`fi_intel.application.daily_worker`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from fi_intel.application.daily_worker import ProcessedDailyAnalysis
from fi_intel.application.jobs import stable_digest

_POLICY_NAMESPACE = UUID("689960ce-920a-596d-85c7-cb2ca788170f")


def canonical_daily_run_identity(
    *,
    wall_now: datetime,
    topic_ids: frozenset[str],
    authorization_scope: str,
    covered_entity_leis: frozenset[str],
    required_source_ids: set[str],
    policy_version: str,
    timezone_name: str,
    cutoff_hour: int,
    window_version: str,
    analysis_mode: str,
) -> tuple[str, str]:
    """Return the immutable business-window digest and coalesced run ID."""

    if wall_now.tzinfo is None or wall_now.utcoffset() is None:
        raise ValueError("daily run identity requires an aware wall-clock time")
    if not 0 <= cutoff_hour <= 23:
        raise ValueError("daily analysis cutoff hour must be in [0, 23]")
    local_now = wall_now.astimezone(ZoneInfo(timezone_name))
    business_date = (local_now - timedelta(hours=cutoff_hour)).date()
    identity = {
        "business_date": business_date.isoformat(),
        "topics": sorted(topic_ids),
        "authorization_scope": authorization_scope,
        "entities": sorted(covered_entity_leis),
        "sources": sorted(required_source_ids),
        "policy": policy_version,
        "window_version": window_version,
        "analysis_mode": analysis_mode,
    }
    digest = stable_digest(identity)
    return digest, str(uuid5(_POLICY_NAMESPACE, f"daily-analysis:{digest}"))


__all__ = ["ProcessedDailyAnalysis", "canonical_daily_run_identity"]
