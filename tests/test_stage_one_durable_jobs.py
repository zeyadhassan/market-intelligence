"""Durable request ownership contracts for the canonical Stage One service."""

import inspect
from datetime import UTC, datetime
from pathlib import Path

from fi_intel.api.auth import RequestPrincipal
from fi_intel.api.stage_one_postgres import PostgresStageOneService
from fi_intel.application.daily_worker import ProcessedDailyAnalysis
from fi_intel.application.jobs import AnalysisJob
from fi_intel.application.scheduler import CanonicalScheduler
from fi_intel.application.topics import GovernedTopic, governed_topic_revision
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


def test_refresh_identity_changes_with_source_or_projection_revision() -> None:
    requested_at = datetime(2026, 8, 27, 8, tzinfo=UTC)
    arguments = (
        Settings(),
        _principal(),
        frozenset({"upcoming-maturities"}),
        "scope-1",
        ("sa_sama_news",),
    )

    first = AnalysisJob.request(
        *arguments,
        requested_at=requested_at,
        input_revision=("sa_sama_news:observation-1",),
    )
    duplicate = AnalysisJob.request(
        *arguments,
        requested_at=requested_at,
        input_revision=("sa_sama_news:observation-1",),
    )
    updated = AnalysisJob.request(
        *arguments,
        requested_at=requested_at,
        input_revision=("sa_sama_news:observation-2",),
    )
    processed = AnalysisJob.request(
        *arguments,
        requested_at=requested_at,
        input_revision=(
            "sa_sama_news:observation-1",
            "document:version-1:complete:2026-08-27T08:01:00+00:00",
        ),
    )

    assert first.job_id == duplicate.job_id
    assert first.job_id != updated.job_id
    assert first.job_id != processed.job_id
    assert first.input_manifest["input_revision"] == ["sa_sama_news:observation-1"]


def test_governed_topic_revision_changes_daily_job_identity() -> None:
    requested_at = datetime(2026, 8, 27, 8, tzinfo=UTC)
    base = GovernedTopic(
        topic_id="upcoming-maturities",
        version="topic-v1",
        label="Upcoming maturities",
        description="Observed maturities.",
        owner="fi_gcc",
        patterns=frozenset({"upcoming_maturity_observed"}),
        required_source_ids=frozenset({"sa_sama_news"}),
        freshness_seconds=86_400,
        detector_policy_version="detector-policy-v1",
        retrieval_policy_version="daily-hybrid-v1",
        lifecycle_policy_version="opportunity-lifecycle-v1",
        display_order=10,
    )
    revised = base.model_copy(
        update={"version": "topic-v2", "detector_policy_version": "detector-policy-v2"}
    )
    arguments = (
        Settings(),
        _principal(),
        frozenset({base.topic_id}),
        "scope-1",
        ("sa_sama_news",),
    )

    first = AnalysisJob.request(
        *arguments,
        requested_at=requested_at,
        topic_revisions=(governed_topic_revision(base),),
    )
    updated = AnalysisJob.request(
        *arguments,
        requested_at=requested_at,
        topic_revisions=(governed_topic_revision(revised),),
    )

    assert first.job_id != updated.job_id
    assert first.input_manifest["topic_revisions"] == [
        "upcoming-maturities:topic-v1:detector-policy-v1:daily-hybrid-v1:"
        "opportunity-lifecycle-v1"
    ]


def test_scheduler_and_worker_preserve_governed_and_fresh_snapshot_identity() -> None:
    scheduler_source = inspect.getsource(CanonicalScheduler)
    freeze_source = inspect.getsource(ProcessedDailyAnalysis._freeze_inputs)

    assert "topic_revisions=(governed_topic_revision(topic),)" in scheduler_source
    assert "no complete fresh observation" in scheduler_source
    assert "_source_recovery_revision" in scheduler_source
    assert "registration.freshness_sla_seconds" in freeze_source
    assert "observation.health = 'healthy'" in freeze_source


def test_api_has_no_background_analysis_task_ownership() -> None:
    source = inspect.getsource(PostgresStageOneService)
    migration = Path("deploy/migrations/0022_developer_mvp_runtime.sql").read_text(encoding="utf-8")

    assert "create_task" not in source
    assert "_analysis_tasks" not in source
    assert "_jobs.enqueue" in source
    assert "source_observation_v2" in source
    assert "document_processing_job_v4" in source
    assert "Fetch failed:" in source
    assert "refresh=refresh" in source
    assert "idempotency_key           TEXT NOT NULL UNIQUE" in migration


def test_topic_migration_retires_superseded_active_versions() -> None:
    migration = Path(
        "deploy/migrations/0029_retire_superseded_maturity_topic.sql"
    ).read_text(encoding="utf-8")

    assert "SET active = FALSE" in migration
    assert "version <> 'topic-v2'" in migration
