"""Payload-safe worker heartbeat and runtime event persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import asyncpg

from fi_intel.logging import get_logger, safe_error_summary

_log = get_logger(component="runtime.observability")


@runtime_checkable
class RuntimeMonitor(Protocol):
    async def loop_started(self, loop_run_id: str) -> None: ...

    async def iteration_started(self) -> None: ...

    async def heartbeat(self) -> None: ...

    async def iteration_completed(self, duration_ms: float) -> None: ...

    async def iteration_failed(self, error: BaseException, duration_ms: float) -> None: ...

    async def loop_stopped(self) -> None: ...


class PostgresRuntimeMonitor:
    """Best-effort live state for one long-running worker process."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        worker_id: str,
        worker_type: str,
        operation: str,
    ) -> None:
        self._pool = pool
        self._worker_id = worker_id
        self._worker_type = worker_type
        self._operation = operation
        self._loop_run_id = "not-started"
        self._process_started_at = datetime.now(UTC)

    async def loop_started(self, loop_run_id: str) -> None:
        self._loop_run_id = loop_run_id
        await self._update("starting")
        await self._event("started", "Worker loop started.")

    async def iteration_started(self) -> None:
        await self._update("working", iteration_started=True)

    async def heartbeat(self) -> None:
        try:
            await self._pool.execute(
                """
                UPDATE runtime_worker_state_v1 SET heartbeat_at=$2
                WHERE worker_id=$1
                """,
                self._worker_id,
                datetime.now(UTC),
            )
        except Exception as exc:  # best-effort observability must not stop work
            self._report_failure(exc)

    async def iteration_completed(self, duration_ms: float) -> None:
        del duration_ms
        await self._update("idle", iteration_finished=True, success=True)

    async def iteration_failed(self, error: BaseException, duration_ms: float) -> None:
        summary = safe_error_summary(error)
        await self._update(
            "failed",
            iteration_finished=True,
            failure=True,
            error_summary=summary,
        )
        await self._event(
            "failed",
            "Worker iteration failed; the loop will retry.",
            duration_ms=duration_ms,
            error_summary=summary,
        )

    async def loop_stopped(self) -> None:
        await self._update("stopped", iteration_finished=True)
        await self._event("stopped", "Worker loop stopped.")

    async def _update(
        self,
        status: str,
        *,
        iteration_started: bool = False,
        iteration_finished: bool = False,
        success: bool = False,
        failure: bool = False,
        error_summary: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        started_at = now if iteration_started else None
        finished_at = now if iteration_finished else None
        success_at = now if success else None
        failure_at = now if failure else None
        try:
            await self._pool.execute(
                """
                INSERT INTO runtime_worker_state_v1 (
                    worker_id, worker_type, status, operation, loop_run_id,
                    process_started_at, iteration_started_at,
                    iteration_finished_at, last_success_at, last_failure_at,
                    safe_error_summary, heartbeat_at
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12
                )
                ON CONFLICT (worker_id) DO UPDATE SET
                    worker_type=EXCLUDED.worker_type,
                    status=EXCLUDED.status,
                    operation=EXCLUDED.operation,
                    loop_run_id=EXCLUDED.loop_run_id,
                    process_started_at=EXCLUDED.process_started_at,
                    iteration_started_at=COALESCE(
                        EXCLUDED.iteration_started_at,
                        runtime_worker_state_v1.iteration_started_at
                    ),
                    iteration_finished_at=CASE
                        WHEN EXCLUDED.iteration_started_at IS NOT NULL THEN NULL
                        ELSE COALESCE(
                            EXCLUDED.iteration_finished_at,
                            runtime_worker_state_v1.iteration_finished_at
                        )
                    END,
                    last_success_at=COALESCE(
                        EXCLUDED.last_success_at,
                        runtime_worker_state_v1.last_success_at
                    ),
                    last_failure_at=COALESCE(
                        EXCLUDED.last_failure_at,
                        runtime_worker_state_v1.last_failure_at
                    ),
                    safe_error_summary=EXCLUDED.safe_error_summary,
                    heartbeat_at=EXCLUDED.heartbeat_at
                """,
                self._worker_id,
                self._worker_type,
                status,
                self._operation,
                self._loop_run_id,
                self._process_started_at,
                started_at,
                finished_at,
                success_at,
                failure_at,
                error_summary,
                now,
            )
        except Exception as exc:  # best-effort observability must not stop work
            self._report_failure(exc)

    async def _event(
        self,
        status: str,
        message: str,
        *,
        duration_ms: float | None = None,
        error_summary: str | None = None,
    ) -> None:
        try:
            await self._pool.execute(
                """
                INSERT INTO runtime_event_v1 (
                    category, component, operation, status, worker_id, run_id,
                    message, duration_ms, safe_error_summary, occurred_at
                ) VALUES ('worker',$1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                self._worker_type,
                self._operation,
                status,
                self._worker_id,
                self._loop_run_id,
                message,
                duration_ms,
                error_summary,
                datetime.now(UTC),
            )
        except Exception as exc:  # best-effort observability must not stop work
            self._report_failure(exc)

    def _report_failure(self, error: BaseException) -> None:
        _log.warning(
            "runtime.observability.write_failed",
            worker_id=self._worker_id,
            worker_type=self._worker_type,
            error_type=type(error).__name__,
            safe_error_summary=safe_error_summary(error),
        )


__all__ = ["PostgresRuntimeMonitor", "RuntimeMonitor"]
