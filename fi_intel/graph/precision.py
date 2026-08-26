"""Analyst-feedback-derived detector precision estimates."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from fi_intel.governance.policy import GraphAccessContext


class PatternPrecisionEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)

    rate: float = Field(ge=0.0, le=1.0)
    successes: int = Field(ge=0)
    samples: int = Field(gt=0)


@runtime_checkable
class PatternPrecisionProvider(Protocol):
    async def estimate(
        self,
        pattern: str,
        version: str,
        as_of: datetime,
        access: GraphAccessContext,
    ) -> PatternPrecisionEstimate | None: ...


class UnavailablePatternPrecisionProvider:
    async def estimate(
        self,
        pattern: str,
        version: str,
        as_of: datetime,
        access: GraphAccessContext,
    ) -> None:
        del pattern, version, as_of, access
        return None


PATTERN_PRECISION_SQL = """
WITH latest_feedback AS (
    SELECT DISTINCT ON (feedback.signal_id)
           feedback.signal_id, feedback.verdict
    FROM analyst_signal_feedback feedback
    JOIN intelligence_signal signal ON signal.signal_id = feedback.signal_id
    JOIN access_policy signal_policy ON signal_policy.policy_id = signal.policy_id
    JOIN access_policy feedback_policy ON feedback_policy.policy_id = feedback.policy_id
    WHERE signal.pattern_id = $1
      AND signal.pattern_version = $2
      AND feedback.recorded_at <= $3
      AND $4 = ANY(signal_policy.allowed_entitlement_groups)
      AND (signal_policy.barrier_side = 'public' OR $5 = 'private')
      AND $4 = ANY(feedback_policy.allowed_entitlement_groups)
      AND (feedback_policy.barrier_side = 'public' OR $5 = 'private')
      AND feedback.verdict <> 'needs_review'
    ORDER BY feedback.signal_id, feedback.recorded_at DESC, feedback.feedback_id DESC
)
SELECT count(*) FILTER (WHERE verdict IN ('approve', 'useful'))::int AS successes,
       count(*)::int AS samples
FROM latest_feedback
"""


class PostgresPatternPrecisionProvider:
    def __init__(self, dsn: str, *, minimum_samples: int = 30) -> None:
        self._dsn = dsn
        self._minimum_samples = minimum_samples
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def estimate(
        self,
        pattern: str,
        version: str,
        as_of: datetime,
        access: GraphAccessContext,
    ) -> PatternPrecisionEstimate | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            PATTERN_PRECISION_SQL,
            pattern,
            version,
            as_of,
            access.principal.entitlement_group,
            access.principal.side.value,
        )
        samples = int(row["samples"]) if row is not None else 0
        if samples < self._minimum_samples:
            return None
        successes = int(row["successes"])
        return PatternPrecisionEstimate(
            rate=successes / samples,
            successes=successes,
            samples=samples,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
