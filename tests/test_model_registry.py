from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from fi_intel.governance.model_registry import (
    InMemoryModelRegistry,
    ModelArtifact,
    ModelComponent,
    RegistryConflictError,
    RegistryInvariantError,
    ReleaseState,
    ReleaseTransition,
)

NOW = datetime(2026, 8, 25, tzinfo=UTC)
DIGEST = "a" * 64


def artifact(
    release_id: str,
    *,
    component: ModelComponent = ModelComponent.REASONING,
    passed: bool = True,
) -> ModelArtifact:
    return ModelArtifact(
        release_id=UUID(release_id),
        component=component,
        model_id=f"on-prem:{release_id[-2:]}",
        artifact_digest=DIGEST,
        prompt_version="research-v2",
        schema_version="opportunity-v2",
        evaluation_dataset_digest="b" * 64,
        evaluation_report_digest="c" * 64,
        quality_gate_passed=passed,
        evaluated_at=NOW,
        created_at=NOW + timedelta(minutes=1),
        created_by="model-risk",
    )


def transition(
    transition_id: str,
    release_id: UUID,
    previous: ReleaseState | None,
    requested: ReleaseState,
    minute: int,
    rollout: int = 0,
) -> ReleaseTransition:
    return ReleaseTransition(
        transition_id=UUID(transition_id),
        release_id=release_id,
        from_state=previous,
        to_state=requested,
        rollout_percent=rollout,
        occurred_at=NOW + timedelta(minutes=minute),
        actor="release-manager",
        reason="governed test promotion",
    )


async def register_candidate(
    registry: InMemoryModelRegistry, item: ModelArtifact, suffix: int
) -> None:
    await registry.register(
        item,
        transition(
            f"00000000-0000-0000-0000-{suffix:012d}",
            item.release_id,
            None,
            ReleaseState.CANDIDATE,
            2,
        ),
    )


async def promote_active(
    registry: InMemoryModelRegistry, item: ModelArtifact, suffix: int
) -> None:
    await registry.transition(
        transition(
            f"10000000-0000-0000-0000-{suffix:012d}",
            item.release_id,
            ReleaseState.CANDIDATE,
            ReleaseState.SHADOW,
            3,
        )
    )
    await registry.transition(
        transition(
            f"20000000-0000-0000-0000-{suffix:012d}",
            item.release_id,
            ReleaseState.SHADOW,
            ReleaseState.CANARY,
            4,
            10,
        )
    )
    await registry.transition(
        transition(
            f"30000000-0000-0000-0000-{suffix:012d}",
            item.release_id,
            ReleaseState.CANARY,
            ReleaseState.ACTIVE,
            5,
            100,
        )
    )


async def test_release_requires_ordered_evaluated_promotion() -> None:
    registry = InMemoryModelRegistry()
    item = artifact("00000000-0000-0000-0000-000000000001")
    await register_candidate(registry, item, 1)
    await promote_active(registry, item, 1)

    snapshots = await registry.current(ModelComponent.REASONING)
    assert snapshots[0].state is ReleaseState.ACTIVE
    assert await registry.route(ModelComponent.REASONING, "signal-1") == item


async def test_failed_evaluation_cannot_reach_shadow() -> None:
    registry = InMemoryModelRegistry()
    item = artifact("00000000-0000-0000-0000-000000000002", passed=False)
    await register_candidate(registry, item, 2)

    with pytest.raises(RegistryInvariantError, match="failed evaluation"):
        await registry.transition(
            transition(
                "10000000-0000-0000-0000-000000000002",
                item.release_id,
                ReleaseState.CANDIDATE,
                ReleaseState.SHADOW,
                3,
            )
        )


async def test_two_active_releases_for_one_component_are_rejected() -> None:
    registry = InMemoryModelRegistry()
    first = artifact("00000000-0000-0000-0000-000000000003")
    second = artifact("00000000-0000-0000-0000-000000000004")
    await register_candidate(registry, first, 3)
    await register_candidate(registry, second, 4)
    await promote_active(registry, first, 3)

    await registry.transition(
        transition(
            "10000000-0000-0000-0000-000000000004",
            second.release_id,
            ReleaseState.CANDIDATE,
            ReleaseState.SHADOW,
            3,
        )
    )
    await registry.transition(
        transition(
            "20000000-0000-0000-0000-000000000004",
            second.release_id,
            ReleaseState.SHADOW,
            ReleaseState.CANARY,
            4,
            10,
        )
    )
    with pytest.raises(RegistryInvariantError, match="active"):
        await registry.transition(
            transition(
                "30000000-0000-0000-0000-000000000004",
                second.release_id,
                ReleaseState.CANARY,
                ReleaseState.ACTIVE,
                5,
                100,
            )
        )


async def test_canary_routing_is_sticky_and_approximately_bounded() -> None:
    registry = InMemoryModelRegistry()
    active = artifact("00000000-0000-0000-0000-000000000005")
    canary = artifact("00000000-0000-0000-0000-000000000006")
    await register_candidate(registry, active, 5)
    await promote_active(registry, active, 5)
    await register_candidate(registry, canary, 6)
    await registry.transition(
        transition(
            "10000000-0000-0000-0000-000000000006",
            canary.release_id,
            ReleaseState.CANDIDATE,
            ReleaseState.SHADOW,
            6,
        )
    )
    await registry.transition(
        transition(
            "20000000-0000-0000-0000-000000000006",
            canary.release_id,
            ReleaseState.SHADOW,
            ReleaseState.CANARY,
            7,
            10,
        )
    )

    first = await registry.route(ModelComponent.REASONING, "stable-signal")
    assert await registry.route(ModelComponent.REASONING, "stable-signal") == first
    routed = [
        await registry.route(ModelComponent.REASONING, f"signal-{index}")
        for index in range(1_000)
    ]
    canary_count = sum(item.release_id == canary.release_id for item in routed)
    assert 70 <= canary_count <= 130


async def test_idempotent_registration_rejects_conflicting_content() -> None:
    registry = InMemoryModelRegistry()
    item = artifact("00000000-0000-0000-0000-000000000007")
    initial = transition(
        "00000000-0000-0000-0000-000000000007",
        item.release_id,
        None,
        ReleaseState.CANDIDATE,
        2,
    )
    await registry.register(item, initial)
    assert (await registry.register(item, initial)).state is ReleaseState.CANDIDATE

    conflicting = item.model_copy(update={"model_id": "other-model"})
    with pytest.raises(RegistryConflictError, match="conflicting"):
        await registry.register(conflicting, initial)


def test_rollout_and_evaluation_time_are_structurally_validated() -> None:
    item = artifact("00000000-0000-0000-0000-000000000008")
    with pytest.raises(ValidationError, match="canary rollout"):
        transition(
            "20000000-0000-0000-0000-000000000008",
            item.release_id,
            ReleaseState.SHADOW,
            ReleaseState.CANARY,
            3,
            0,
        )
    with pytest.raises(ValidationError, match="before release"):
        ModelArtifact.model_validate(
            {**item.model_dump(), "evaluated_at": NOW + timedelta(hours=1)}
        )
