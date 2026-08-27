"""Deterministic coalescing tests for the one supported daily pipeline."""

from datetime import UTC, datetime

import pytest

from fi_intel.application.daily_analysis import canonical_daily_run_identity


def _identity(now: datetime, *, topics: frozenset[str] = frozenset({"rating"})) -> tuple[str, str]:
    return canonical_daily_run_identity(
        wall_now=now,
        topic_ids=topics,
        authorization_scope="policy:scope-1",
        covered_entity_leis=frozenset({"LEI-2", "LEI-1"}),
        required_source_ids={"source-b", "source-a"},
        policy_version="entitlement-v1",
        timezone_name="Europe/Berlin",
        cutoff_hour=6,
        window_version="daily-window-v1",
        analysis_mode="shadow",
    )


def test_same_business_window_and_set_order_coalesce_to_one_run() -> None:
    before_cutoff = _identity(datetime(2026, 8, 27, 3, 30, tzinfo=UTC))
    same_window = _identity(datetime(2026, 8, 27, 3, 59, tzinfo=UTC))

    assert before_cutoff == same_window
    assert len(before_cutoff[0]) == 64


def test_crossing_local_cutoff_creates_a_new_run() -> None:
    before_cutoff = _identity(datetime(2026, 8, 27, 3, 59, tzinfo=UTC))
    at_cutoff = _identity(datetime(2026, 8, 27, 4, 0, tzinfo=UTC))

    assert before_cutoff != at_cutoff


def test_material_scope_change_creates_a_new_run() -> None:
    now = datetime(2026, 8, 27, 12, tzinfo=UTC)
    assert _identity(now) != _identity(now, topics=frozenset({"rating", "capital"}))


def test_run_identity_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="aware"):
        _identity(datetime(2026, 8, 27, 12))
