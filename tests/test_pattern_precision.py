from datetime import UTC, datetime
from typing import cast

import asyncpg

from fi_intel.governance.policy import trusted_test_access
from fi_intel.graph.precision import (
    PATTERN_PRECISION_SQL,
    PostgresPatternPrecisionProvider,
)


class _Pool:
    def __init__(self, successes: int, samples: int) -> None:
        self.row = {"successes": successes, "samples": samples}
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, int]:
        self.calls.append((sql, args))
        return self.row


async def test_pattern_precision_is_earned_from_latest_authorized_feedback() -> None:
    pool = _Pool(successes=24, samples=30)
    provider = PostgresPatternPrecisionProvider("unused", minimum_samples=30)
    provider._pool = cast(asyncpg.Pool, pool)  # noqa: SLF001
    as_of = datetime(2025, 1, 1, tzinfo=UTC)
    access = trusted_test_access("wire")

    estimate = await provider.estimate("programme", "3.0.0", as_of, access)

    assert estimate is not None
    assert estimate.rate == 0.8
    sql, args = pool.calls[0]
    assert sql == PATTERN_PRECISION_SQL
    assert args[:3] == ("programme", "3.0.0", as_of)
    assert "distinct on (feedback.signal_id)" in sql.lower()
    assert "needs_review" in sql


async def test_pattern_precision_is_disabled_below_feedback_floor() -> None:
    provider = PostgresPatternPrecisionProvider("unused", minimum_samples=30)
    provider._pool = cast(asyncpg.Pool, _Pool(successes=10, samples=29))  # noqa: SLF001

    assert (
        await provider.estimate(
            "programme",
            "3.0.0",
            datetime(2025, 1, 1, tzinfo=UTC),
            trusted_test_access("wire"),
        )
        is None
    )
