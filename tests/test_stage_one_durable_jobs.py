"""Durable request ownership contracts for the canonical Stage One service."""

import inspect
from datetime import UTC, datetime
from pathlib import Path

from fi_intel.api.auth import RequestPrincipal
from fi_intel.api.stage_one_postgres import PostgresStageOneService
from fi_intel.application.jobs import AnalysisJob
from fi_intel.config import Settings
from fi_intel.retrieval.entitlement import Principal, Side


def _principal() -> RequestPrincipal:
    return RequestPrincipal(
        subject="analyst@example.test",
        principal=Principal(
            principal_id="analyst-1",
            entitlement_group="fi_gcc_public",
            side=Side.PUBLIC,
        ),
        desks=frozenset({"fi_gcc"}),
        roles=frozenset({"analyst"}),
        purposes=frozenset({"market_intelligence"}),
    )


def test_duplicate_requests_have_one_business_window_identity() -> None:
    requested_at = datetime(2026, 8, 27, 8, tzinfo=UTC)
    arguments = (
        Settings(),
        _principal(),
        frozenset({"upcoming-maturities"}),
        "scope-1",
        ("sa_sama_news",),
    )

    first = AnalysisJob.request(*arguments, requested_at=requested_at)
    second = AnalysisJob.request(*arguments, requested_at=requested_at)

    assert first.job_id == second.job_id
    assert first.idempotency_key == second.idempotency_key


def test_api_has_no_background_analysis_task_ownership() -> None:
    source = inspect.getsource(PostgresStageOneService)
    migration = Path("deploy/migrations/0022_developer_mvp_runtime.sql").read_text(encoding="utf-8")

    assert "create_task" not in source
    assert "_analysis_tasks" not in source
    assert "_jobs.enqueue" in source
    assert "idempotency_key           TEXT NOT NULL UNIQUE" in migration
