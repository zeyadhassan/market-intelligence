"""Unit contracts for payload-safe worker heartbeat persistence."""

from datetime import datetime
from typing import Any, cast

import asyncpg

from fi_intel.application.observability import PostgresRuntimeMonitor


class RecordingPool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, statement: str, *arguments: Any) -> str:
        self.calls.append((statement, arguments))
        return "INSERT 0 1"


async def test_worker_state_upsert_supplies_concrete_timestamps() -> None:
    pool = RecordingPool()
    monitor = PostgresRuntimeMonitor(
        cast(asyncpg.Pool, pool),
        worker_id="projection-test",
        worker_type="projection",
        operation="project and index",
    )

    await monitor.iteration_started()

    statement, arguments = pool.calls[0]
    assert "CASE WHEN $7::boolean" not in statement
    assert isinstance(arguments[6], datetime)
    assert arguments[7:11] == (None, None, None, None)


async def test_worker_failure_retains_safe_root_cause_type() -> None:
    pool = RecordingPool()
    monitor = PostgresRuntimeMonitor(
        cast(asyncpg.Pool, pool),
        worker_id="projection-test",
        worker_type="projection",
        operation="project and index",
    )
    try:
        try:
            raise ValueError("private source text")
        except ValueError as cause:
            raise RuntimeError("document indexing failed") from cause
    except RuntimeError as error:
        await monitor.iteration_failed(error, 12.0)

    _, state_arguments = pool.calls[0]
    summary = str(state_arguments[10])
    assert "cause=ValueError" in summary
    assert "private source text" not in summary
