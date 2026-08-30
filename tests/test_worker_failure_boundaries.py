"""Failure injection and commit-order checks for independently restartable workers."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fi_intel.application.daily_worker import CanonicalAnalysisJobWorker
from fi_intel.application.jobs import AnalysisJob, AnalysisJobState, PrincipalSnapshot
from fi_intel.application.search import (
    CanonicalSearchWorker,
    SearchJob,
    SearchState,
    plan_search,
)
from fi_intel.config import Settings
from fi_intel.governance.access import RequestPrincipal
from fi_intel.retrieval.entitlement import Principal, Side

NOW = datetime(2026, 8, 27, 8, tzinfo=UTC)


class _Telemetry:
    def record_queue_transition(self, *_: object) -> None:
        pass


def _principal() -> RequestPrincipal:
    return RequestPrincipal(
        subject="analyst@example.test",
        principal=Principal(
            principal_id="analyst-1",
            entitlement_group="fi_gcc_public",
            side=Side.PUBLIC,
        ),
        desks=frozenset({"gcc-fi"}),
        roles=frozenset({"analyst"}),
        purposes=frozenset({"market-intelligence"}),
    )


async def test_analysis_failure_returns_job_to_the_durable_retry_queue() -> None:
    settings = Settings(worker_max_attempts=3)
    job = AnalysisJob.request(
        settings,
        _principal(),
        frozenset({"upcoming-maturities"}),
        "scope-public",
        ("source-a",),
        requested_at=NOW,
    ).model_copy(update={"state": AnalysisJobState.RUNNING, "attempt_count": 1})

    class Jobs:
        finish_called = False

        async def claim(self, *_: object) -> AnalysisJob:
            return job

        async def finish(self, *_: object, **__: object) -> AnalysisJob:
            self.finish_called = True
            return job

        async def fail(self, *_: object, **__: object) -> AnalysisJob:
            return job.model_copy(update={"state": AnalysisJobState.RETRYABLE_FAILED})

    class Analysis:
        async def run(self, _: AnalysisJob) -> None:
            raise RuntimeError("injected model outage")

    jobs = Jobs()
    worker = CanonicalAnalysisJobWorker.__new__(CanonicalAnalysisJobWorker)
    worker._resources = SimpleNamespace(settings=settings, telemetry=_Telemetry())  # type: ignore[attr-defined]
    worker._worker_id = "analysis-test"  # type: ignore[attr-defined]
    worker._jobs = jobs  # type: ignore[attr-defined]
    worker._analysis = Analysis()  # type: ignore[attr-defined]
    worker._opportunities = SimpleNamespace()  # type: ignore[attr-defined]

    result = await worker.run_once()

    assert result is not None and result.state is AnalysisJobState.RETRYABLE_FAILED
    assert not jobs.finish_called


async def test_search_failure_returns_job_to_the_durable_retry_queue() -> None:
    settings = Settings(worker_max_attempts=3)
    snapshot = PrincipalSnapshot.from_principal(_principal())
    job = SearchJob(
        search_id="a" * 64,
        idempotency_key="search:a",
        principal=snapshot,
        authorization_scope="scope-public",
        query_text="Example Bank refinancing",
        plan=plan_search("Example Bank refinancing"),
        temporal_pin=NOW,
        state=SearchState.RUNNING,
        attempt_count=1,
        requested_at=NOW,
        updated_at=NOW,
    )

    class Store:
        finished_state: SearchState | None = None

        async def claim(self, _: str) -> SearchJob:
            return job

        async def finish(
            self,
            _: str,
            __: str,
            state: SearchState,
            answer: dict[str, Any] | None,
            safe_error: str | None = None,
        ) -> SearchJob:
            self.finished_state = state
            assert answer is None
            assert safe_error is not None and "model outage" not in safe_error
            return job.model_copy(update={"state": state, "safe_error_summary": safe_error})

    async def execute(_: SearchJob) -> dict[str, Any]:
        raise RuntimeError("injected model outage")

    store = Store()
    worker = CanonicalSearchWorker.__new__(CanonicalSearchWorker)
    worker._resources = SimpleNamespace(settings=settings)  # type: ignore[attr-defined]
    worker._worker_id = "search-test"  # type: ignore[attr-defined]
    worker._store = store  # type: ignore[attr-defined]
    worker._execute = execute  # type: ignore[method-assign]

    result = await worker.run_once()

    assert result is not None and result.state is SearchState.RETRYABLE_FAILED
    assert store.finished_state is SearchState.RETRYABLE_FAILED


def test_analysis_read_model_commit_precedes_terminal_job_transition() -> None:
    source = inspect.getsource(CanonicalAnalysisJobWorker.run_once)

    assert source.index("materialize_topic") < source.index("self._jobs.finish")
    assert "process death before this line" in source
