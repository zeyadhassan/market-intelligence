"""Idempotency and server-owned completion tests for durable analysis state."""

from datetime import UTC, datetime, timedelta

import pytest

from fi_intel.application.analysis_state import (
    AnalysisCompletion,
    AnalysisRunRecord,
    AnalysisStateConflictError,
    DetectorExecutionRecord,
    PostgresAnalysisStateStore,
)

NOW = datetime(2026, 8, 27, tzinfo=UTC)


class _Pool:
    def __init__(self, row: dict) -> None:
        self.row = row

    async def execute(self, *args) -> str:  # noqa: ANN002
        return "INSERT 0 0"

    async def fetchrow(self, *args):  # noqa: ANN002, ANN202
        return self.row


def _run() -> AnalysisRunRecord:
    return AnalysisRunRecord(
        run_id="run-1",
        mode="shadow",
        principal_id="analyst-1",
        authorization_scope="policy:one",
        policy_version="policy-v1",
        temporal_pin=NOW,
        input_manifest_digest="a" * 64,
        created_at=NOW,
    )


async def test_coalesced_run_joins_first_temporal_pin() -> None:
    requested = _run().model_copy(
        update={
            "principal_id": "analyst-2",
            "temporal_pin": NOW + timedelta(seconds=1),
            "created_at": NOW + timedelta(seconds=1),
        }
    )
    stored = _run()
    store = PostgresAnalysisStateStore("unused")
    store._pool = _Pool(stored.model_dump())  # type: ignore[assignment]  # noqa: SLF001

    joined = await store.create_run(requested)

    assert joined == stored


async def test_run_id_reuse_with_different_scope_is_rejected() -> None:
    requested = _run()
    stored = _run().model_copy(update={"authorization_scope": "policy:other"})
    store = PostgresAnalysisStateStore("unused")
    store._pool = _Pool(stored.model_dump())  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(AnalysisStateConflictError, match="immutable input"):
        await store.create_run(requested)


async def test_detector_retry_cannot_hide_changed_output() -> None:
    execution = DetectorExecutionRecord(
        execution_id="b" * 64,
        job_id="c" * 64,
        pattern_name="rating_change",
        pattern_version="v1",
        state="completed",
        coverage_decision={"complete": True},
        input_digest="d" * 64,
        output_digest="e" * 64,
        started_at=NOW,
        finished_at=NOW,
    )
    row = {
        **execution.model_dump(),
        "output_digest": "f" * 64,
    }
    store = PostgresAnalysisStateStore("unused")
    store._pool = _Pool(row)  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(AnalysisStateConflictError, match="input/output"):
        await store.record_detector(execution)


def test_completion_is_fail_closed_and_content_addressed() -> None:
    incomplete = AnalysisCompletion.compute(
        run_id="run-1",
        required_source_ids={"source-a"},
        completed_source_ids=set(),
        required_job_ids={"job-a"},
        completed_job_ids={"job-a"},
        coverage_reasons=("source-a is dark",),
        computed_at=NOW,
    )
    complete = AnalysisCompletion.compute(
        run_id="run-1",
        required_source_ids={"source-a"},
        completed_source_ids={"source-a"},
        required_job_ids={"job-a"},
        completed_job_ids={"job-a"},
        computed_at=NOW,
    )

    assert incomplete.complete is False
    assert complete.complete is True
    assert incomplete.completion_id != complete.completion_id
