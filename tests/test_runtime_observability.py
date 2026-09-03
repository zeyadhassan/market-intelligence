"""Unit contracts for payload-safe worker heartbeat persistence."""

import inspect
from datetime import datetime
from typing import Any, cast

import asyncpg

from fi_intel.application.observability import PostgresRuntimeMonitor
from fi_intel.application.operations import OperatorService, _latest_search_state_counts
from fi_intel.logging import safe_error_summary


class RecordingPool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, statement: str, *arguments: Any) -> str:
        self.calls.append((statement, arguments))
        return "INSERT 0 1"


class FakeDatabaseError(RuntimeError):
    sqlstate = "42804"
    schema_name = "public"
    table_name = "document_chunk"
    column_name = "embedding"
    constraint_name = "unsafe name with values"


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
    assert "cause_chain=ValueError" in summary
    assert "private source text" not in summary


def test_database_summary_exposes_only_safe_structural_diagnostics() -> None:
    summary = ""
    try:
        try:
            raise FakeDatabaseError("private database row and query")
        except FakeDatabaseError as cause:
            raise RuntimeError("index write failed") from cause
    except RuntimeError as error:
        summary = safe_error_summary(error)

    assert "reason=database_error" in summary
    assert "cause_chain=FakeDatabaseError" in summary
    assert "sqlstate=42804" in summary
    assert "schema=public" in summary
    assert "table=document_chunk" in summary
    assert "column=embedding" in summary
    assert "constraint=" not in summary
    assert "private database row and query" not in summary


def test_runtime_counts_only_latest_logical_jobs_and_detectors() -> None:
    queue_source = inspect.getsource(_latest_search_state_counts)
    dashboard_source = inspect.getsource(OperatorService.dashboard)
    events_source = inspect.getsource(OperatorService._recent_events)

    assert "DISTINCT ON" in queue_source
    assert "plan::text" in queue_source
    assert "latest_analysis_job" in dashboard_source
    assert "latest_search_job" in dashboard_source
    assert "search_error" in dashboard_source
    assert "current_detector" in dashboard_source
    assert "FROM search_job_v4" in events_source
    assert "safe_error_summary" in events_source
