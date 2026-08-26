"""Source reconciliation and durable-observation contracts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fi_intel.graph.coverage import CoverageRequest, SourceOperationsCoverageProvider
from fi_intel.graph.queries import CoverageScope
from fi_intel.sources.acquisition import RawSourceCursor, RawSourcePoll
from fi_intel.sources.catalog import production_source_catalog
from fi_intel.sources.operations import (
    InMemorySourceOperationsStore,
    SourceHealth,
    assess_source_poll,
)

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)


def _empty_poll(source_id: str, *, latest: datetime | None) -> RawSourcePoll:
    cursor = RawSourceCursor(
        source_id=source_id,
        sequence_number=0,
        latest_source_published_at=latest,
        updated_at=NOW,
    )
    return RawSourcePoll(
        source_id=source_id,
        polled_at=NOW,
        feed_modified=True,
        feed_content_hash="a" * 64,
        page_count=1,
        discovered_count=0,
        unchanged_count=0,
        items=(),
        next_cursor=cursor,
    )


async def test_expected_volume_and_silence_are_durable_degraded_observations() -> None:
    base = production_source_catalog().require("fed_press_releases")
    registration = base.model_copy(
        update={
            "expected_min_items": 1,
            "freshness_sla_seconds": 1_800,
            "silence_sla_seconds": 3_600,
        }
    )
    run_id = uuid4()
    observation, state = assess_source_poll(
        registration,
        _empty_poll(registration.source_id, latest=NOW - timedelta(hours=2)),
        run_id=run_id,
        policy_id=uuid4(),
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=5),
        committed_count=0,
        not_novel_count=0,
        quarantine_count=0,
    )

    assert observation.health is SourceHealth.DEGRADED
    assert observation.within_expected_volume is False
    assert observation.fresh is False
    assert observation.silent is True
    store = InMemorySourceOperationsStore()
    await store.record(observation, state)
    assert await store.load_state(registration.source_id) == state
    assert await store.list_observations(registration.source_id) == [observation]


def test_migration_declares_catalog_restart_and_slo_observation_tables() -> None:
    migration = (
        Path(__file__).parents[1] / "deploy" / "migrations" / "0007_source_operations.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS source_registration_v2" in migration
    assert "CREATE TABLE IF NOT EXISTS source_poll_state_v2" in migration
    assert "CREATE TABLE IF NOT EXISTS source_observation_v2" in migration
    assert "allowed_origins" in migration
    assert "raw_retention_days" in migration
    assert "within_expected_volume" in migration


async def test_incomplete_corpus_suppresses_computed_detector_coverage() -> None:
    operations = InMemorySourceOperationsStore()
    provider = SourceOperationsCoverageProvider(
        operations,
        required_source_ids={"maturity_wall_no_refi": frozenset({"ratings-feed"})},
        covered_entity_keys=frozenset({"BANK-LEI"}),
    )

    decision = await provider.assess(
        CoverageRequest(
            pattern_name="maturity_wall_no_refi",
            entity_key="BANK-LEI",
            as_of=NOW,
            freshness_days=180,
            allowed_source_ids=frozenset({"ratings-feed"}),
            scopes=frozenset({CoverageScope.SOURCE_OPERATIONS}),
        )
    )

    assert decision.complete is False
    assert any("no as-of observation" in reason for reason in decision.reasons)


async def test_empty_coverage_configuration_is_visible_before_detector_query() -> None:
    provider = SourceOperationsCoverageProvider(
        InMemorySourceOperationsStore(),
        required_source_ids={},
        covered_entity_keys=frozenset(),
    )

    source_gap = await provider.preflight(
        CoverageRequest(
            pattern_name="maturity_wall_no_refi",
            entity_key="",
            as_of=NOW,
            freshness_days=180,
            allowed_source_ids=frozenset(),
            scopes=frozenset({CoverageScope.SOURCE_OPERATIONS}),
        )
    )
    account_gap = await provider.preflight(
        CoverageRequest(
            pattern_name="leadership_change_treasury",
            entity_key="",
            as_of=NOW,
            freshness_days=120,
            allowed_source_ids=frozenset(),
            scopes=frozenset({CoverageScope.DESK_ACCOUNT}),
        )
    )

    assert source_gap.complete is False
    assert source_gap.reasons == ("no required source universe is configured",)
    assert account_gap.complete is False
    assert account_gap.reasons == ("no desk account coverage universe is configured",)
