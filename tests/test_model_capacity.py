from datetime import UTC, datetime, timedelta

import pytest

from fi_intel.governance.model_usage import (
    InMemoryModelUsageLog,
    ModelCallEstimate,
    ModelCallEvent,
    ModelCapacityLimits,
    ModelUsageSnapshot,
)

NOW = datetime(2026, 8, 25, tzinfo=UTC)
FALLBACK = ModelCallEstimate(
    input_tokens=1_000,
    output_tokens=200,
    cost_usd=0.0,
    latency_ms=1_000.0,
)


def event(index: int, *, run_id: str = "run-1", component: str = "research") -> ModelCallEvent:
    return ModelCallEvent(
        run_id=run_id,
        component=component,
        model="reasoning-v1",
        input_tokens=1_000 + index * 100,
        output_tokens=200 + index * 10,
        cost_usd=index / 100.0,
        latency_ms=1_000.0 + index * 50.0,
        gpu_seconds=2.0 + index / 10.0,
        subject_id=f"signal-{index}",
        recorded_at=NOW + timedelta(seconds=index),
    )


def test_capacity_limits_every_measurable_axis() -> None:
    current = ModelUsageSnapshot(
        calls=1,
        input_tokens=1_000,
        output_tokens=200,
        latency_ms=1_000.0,
        gpu_seconds=2.0,
    )
    estimate = ModelCallEstimate(
        input_tokens=1_000,
        output_tokens=200,
        cost_usd=0.0,
        latency_ms=1_000.0,
        gpu_seconds=2.0,
    )
    assert ModelCapacityLimits(
        max_calls=2,
        max_total_tokens=2_400,
        max_latency_ms=2_000.0,
        max_gpu_seconds=4.0,
        cold_start_estimate=FALLBACK,
    ).allows(current, estimate)
    assert not ModelCapacityLimits(max_calls=1).allows(current, estimate)
    assert not ModelCapacityLimits(max_total_tokens=2_399).allows(current, estimate)
    assert not ModelCapacityLimits(max_latency_ms=1_999.0).allows(current, estimate)
    assert not ModelCapacityLimits(max_gpu_seconds=3.9).allows(current, estimate)


def test_gpu_budget_fails_closed_without_gpu_measurement() -> None:
    assert not ModelCapacityLimits(max_gpu_seconds=100.0).allows(ModelUsageSnapshot(), FALLBACK)


async def test_usage_snapshots_are_run_and_component_scoped() -> None:
    usage = InMemoryModelUsageLog()
    for item in [
        event(1),
        event(2),
        event(3, run_id="other-run"),
        event(4, component="extract"),
    ]:
        await usage.record(item)

    snapshot = await usage.snapshot("run-1", "research")
    assert snapshot.calls == 2
    assert snapshot.input_tokens == 2_300
    assert snapshot.output_tokens == 430
    assert snapshot.gpu_seconds == pytest.approx(4.3)


async def test_estimates_use_fallback_until_history_is_large_enough() -> None:
    usage = InMemoryModelUsageLog()
    for index in range(4):
        await usage.record(event(index))
    assert await usage.estimate("research", "reasoning-v1", FALLBACK) == FALLBACK

    await usage.record(event(9))
    estimate = await usage.estimate("research", "reasoning-v1", FALLBACK)
    assert estimate.sample_size == 5
    assert estimate.input_tokens == 1_900
    assert estimate.output_tokens == 290
    assert estimate.latency_ms == 1_450.0


def test_conservative_merge_never_releases_projected_capacity() -> None:
    projected = ModelUsageSnapshot(
        calls=2,
        input_tokens=20_000,
        output_tokens=3_000,
        cost_usd=1.0,
        latency_ms=30_000,
    )
    delayed_observation = ModelUsageSnapshot(
        calls=1,
        input_tokens=5_000,
        output_tokens=500,
        cost_usd=2.0,
        latency_ms=10_000,
    )
    merged = projected.conservative_merge(delayed_observation)
    assert merged.calls == 2
    assert merged.total_tokens == 23_000
    assert merged.cost_usd == 2.0
