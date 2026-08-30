"""Governed, append-only model release and deterministic canary routing."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Annotated, Protocol, Self, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

SHA256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ModelComponent(StrEnum):
    EXTRACTION = "extraction"
    REASONING = "reasoning"
    EMBEDDING = "embedding"
    RERANKER = "reranker"
    ENTAILMENT = "entailment"


class ReleaseState(StrEnum):
    CANDIDATE = "candidate"
    SHADOW = "shadow"
    CANARY = "canary"
    ACTIVE = "active"
    RETIRED = "retired"
    REJECTED = "rejected"


_ALLOWED_TRANSITIONS: dict[ReleaseState | None, frozenset[ReleaseState]] = {
    None: frozenset({ReleaseState.CANDIDATE}),
    ReleaseState.CANDIDATE: frozenset({ReleaseState.SHADOW, ReleaseState.REJECTED}),
    ReleaseState.SHADOW: frozenset({ReleaseState.CANARY, ReleaseState.REJECTED}),
    ReleaseState.CANARY: frozenset(
        {ReleaseState.ACTIVE, ReleaseState.SHADOW, ReleaseState.REJECTED}
    ),
    ReleaseState.ACTIVE: frozenset({ReleaseState.RETIRED}),
    ReleaseState.RETIRED: frozenset(),
    ReleaseState.REJECTED: frozenset(),
}


class RegistryInvariantError(RuntimeError):
    """A release or promotion violates a model-governance invariant."""


class RegistryConflictError(RuntimeError):
    """A stable identifier was retried with conflicting immutable content."""


class RegistryModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelArtifact(RegistryModel):
    release_id: UUID
    component: ModelComponent
    model_id: str = Field(min_length=1)
    artifact_digest: SHA256
    prompt_version: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    evaluation_dataset_digest: SHA256
    evaluation_report_digest: SHA256
    quality_gate_passed: bool
    evaluated_at: AwareDatetime
    created_at: AwareDatetime
    created_by: str = Field(min_length=1)

    @model_validator(mode="after")
    def _evaluation_cannot_follow_registration(self) -> Self:
        if self.evaluated_at > self.created_at:
            raise ValueError("model evaluation must exist before release registration")
        return self


class ReleaseTransition(RegistryModel):
    transition_id: UUID
    release_id: UUID
    from_state: ReleaseState | None
    to_state: ReleaseState
    rollout_percent: int = Field(ge=0, le=100)
    occurred_at: AwareDatetime
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _rollout_matches_state(self) -> Self:
        if self.to_state is ReleaseState.CANARY and not 1 <= self.rollout_percent <= 50:
            raise ValueError("canary rollout must be between 1 and 50 percent")
        if self.to_state is ReleaseState.ACTIVE and self.rollout_percent != 100:
            raise ValueError("active releases require 100 percent rollout")
        if self.to_state not in {ReleaseState.CANARY, ReleaseState.ACTIVE}:
            if self.rollout_percent != 0:
                raise ValueError("non-serving release states require zero rollout")
        return self


class ModelReleaseSnapshot(RegistryModel):
    artifact: ModelArtifact
    state: ReleaseState
    rollout_percent: int
    changed_at: AwareDatetime


@runtime_checkable
class ModelRegistry(Protocol):
    async def register(
        self, artifact: ModelArtifact, initial: ReleaseTransition
    ) -> ModelReleaseSnapshot: ...

    async def transition(self, transition: ReleaseTransition) -> ModelReleaseSnapshot: ...

    async def current(self, component: ModelComponent) -> list[ModelReleaseSnapshot]: ...

    async def route(self, component: ModelComponent, subject_id: str) -> ModelArtifact: ...

    async def close(self) -> None: ...


def _validate_transition(
    artifact: ModelArtifact,
    transition: ReleaseTransition,
    previous: ReleaseTransition | None,
) -> None:
    previous_state = previous.to_state if previous is not None else None
    if transition.release_id != artifact.release_id:
        raise RegistryInvariantError("transition references another release")
    if transition.from_state is not previous_state:
        raise RegistryInvariantError("transition from_state is stale")
    if transition.to_state not in _ALLOWED_TRANSITIONS[previous_state]:
        raise RegistryInvariantError(
            f"release transition {previous_state!s} -> {transition.to_state} is not allowed"
        )
    if previous is not None and transition.occurred_at <= previous.occurred_at:
        raise RegistryInvariantError("release transition time must increase")
    if (
        transition.to_state
        in {
            ReleaseState.SHADOW,
            ReleaseState.CANARY,
            ReleaseState.ACTIVE,
        }
        and not artifact.quality_gate_passed
    ):
        raise RegistryInvariantError("a failed evaluation cannot enter a serving workflow")


def _snapshot(artifact: ModelArtifact, transition: ReleaseTransition) -> ModelReleaseSnapshot:
    return ModelReleaseSnapshot(
        artifact=artifact,
        state=transition.to_state,
        rollout_percent=transition.rollout_percent,
        changed_at=transition.occurred_at,
    )


def _select_route(
    component: ModelComponent,
    subject_id: str,
    snapshots: list[ModelReleaseSnapshot],
) -> ModelArtifact:
    active = [item for item in snapshots if item.state is ReleaseState.ACTIVE]
    if len(active) != 1:
        raise RegistryInvariantError(
            f"component {component.value!r} requires exactly one active release"
        )
    canaries = [item for item in snapshots if item.state is ReleaseState.CANARY]
    if len(canaries) > 1:
        raise RegistryInvariantError(f"component {component.value!r} has multiple canaries")
    if canaries:
        canary = canaries[0]
        identity = f"{component.value}\x1f{subject_id}\x1f{canary.artifact.release_id}"
        bucket = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big") % 100
        if bucket < canary.rollout_percent:
            return canary.artifact
    return active[0].artifact


class InMemoryModelRegistry:
    def __init__(self) -> None:
        self._artifacts: dict[UUID, ModelArtifact] = {}
        self._transitions: dict[UUID, ReleaseTransition] = {}
        self._history: dict[UUID, list[ReleaseTransition]] = {}

    async def register(
        self, artifact: ModelArtifact, initial: ReleaseTransition
    ) -> ModelReleaseSnapshot:
        previous_artifact = self._artifacts.get(artifact.release_id)
        previous_transition = self._transitions.get(initial.transition_id)
        if previous_artifact is not None or previous_transition is not None:
            if previous_artifact != artifact or previous_transition != initial:
                raise RegistryConflictError("release registration has conflicting content")
            return _snapshot(artifact, initial)
        _validate_transition(artifact, initial, None)
        self._artifacts[artifact.release_id] = artifact
        self._transitions[initial.transition_id] = initial
        self._history[artifact.release_id] = [initial]
        return _snapshot(artifact, initial)

    async def transition(self, transition: ReleaseTransition) -> ModelReleaseSnapshot:
        existing = self._transitions.get(transition.transition_id)
        artifact = self._artifacts.get(transition.release_id)
        if artifact is None:
            raise RegistryInvariantError("model release is unknown")
        if existing is not None:
            if existing != transition:
                raise RegistryConflictError("transition ID has conflicting content")
            return _snapshot(artifact, existing)
        history = self._history[artifact.release_id]
        _validate_transition(artifact, transition, history[-1])
        self._validate_serving_exclusivity(artifact, transition)
        history.append(transition)
        self._transitions[transition.transition_id] = transition
        return _snapshot(artifact, transition)

    def _validate_serving_exclusivity(
        self, artifact: ModelArtifact, transition: ReleaseTransition
    ) -> None:
        if transition.to_state not in {ReleaseState.CANARY, ReleaseState.ACTIVE}:
            return
        conflicting = [
            snapshot
            for snapshot in self._current(artifact.component)
            if snapshot.artifact.release_id != artifact.release_id
            and snapshot.state is transition.to_state
        ]
        if conflicting:
            raise RegistryInvariantError(
                f"component already has a {transition.to_state.value} release"
            )

    def _current(self, component: ModelComponent) -> list[ModelReleaseSnapshot]:
        snapshots = [
            _snapshot(artifact, self._history[release_id][-1])
            for release_id, artifact in self._artifacts.items()
            if artifact.component is component
        ]
        return sorted(snapshots, key=lambda item: (item.changed_at, str(item.artifact.release_id)))

    async def current(self, component: ModelComponent) -> list[ModelReleaseSnapshot]:
        return self._current(component)

    async def route(self, component: ModelComponent, subject_id: str) -> ModelArtifact:
        if not subject_id:
            raise ValueError("subject_id cannot be empty")
        return _select_route(component, subject_id, self._current(component))

    async def close(self) -> None:
        return None


_CURRENT_RELEASES_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (release_id)
           release_id, to_state, rollout_percent, occurred_at
    FROM model_release_transition
    ORDER BY release_id, occurred_at DESC, transition_id DESC
)
SELECT r.release_id, r.component, r.model_id, r.artifact_digest,
       r.prompt_version, r.schema_version, r.evaluation_dataset_digest,
       r.evaluation_report_digest, r.quality_gate_passed, r.evaluated_at,
       r.created_at, r.created_by, latest.to_state, latest.rollout_percent,
       latest.occurred_at AS changed_at
FROM model_release r
JOIN latest USING (release_id)
WHERE r.component = $1
ORDER BY latest.occurred_at, r.release_id
"""


class PostgresModelRegistry:
    """Concurrent promotion store targeting migration 0006."""

    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def register(
        self, artifact: ModelArtifact, initial: ReleaseTransition
    ) -> ModelReleaseSnapshot:
        _validate_transition(artifact, initial, None)
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"model-registry:{artifact.component.value}",
            )
            existing_artifact = await connection.fetchrow(
                "SELECT * FROM model_release WHERE release_id = $1", artifact.release_id
            )
            existing_transition = await connection.fetchrow(
                "SELECT * FROM model_release_transition WHERE transition_id = $1",
                initial.transition_id,
            )
            if existing_artifact is not None or existing_transition is not None:
                if (
                    existing_artifact is None
                    or existing_transition is None
                    or _artifact_from_row(existing_artifact) != artifact
                    or _transition_from_row(existing_transition) != initial
                ):
                    raise RegistryConflictError("release registration has conflicting content")
                return _snapshot(artifact, initial)
            await connection.execute(
                """
                INSERT INTO model_release
                    (release_id, component, model_id, artifact_digest, prompt_version,
                     schema_version, evaluation_dataset_digest, evaluation_report_digest,
                     quality_gate_passed, evaluated_at, created_at, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                artifact.release_id,
                artifact.component.value,
                artifact.model_id,
                artifact.artifact_digest,
                artifact.prompt_version,
                artifact.schema_version,
                artifact.evaluation_dataset_digest,
                artifact.evaluation_report_digest,
                artifact.quality_gate_passed,
                artifact.evaluated_at,
                artifact.created_at,
                artifact.created_by,
            )
            await _insert_transition(connection, initial)
        return _snapshot(artifact, initial)

    async def transition(self, transition: ReleaseTransition) -> ModelReleaseSnapshot:
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                "SELECT * FROM model_release WHERE release_id = $1", transition.release_id
            )
            if row is None:
                raise RegistryInvariantError("model release is unknown")
            artifact = _artifact_from_row(row)
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"model-registry:{artifact.component.value}",
            )
            existing = await connection.fetchrow(
                "SELECT * FROM model_release_transition WHERE transition_id = $1",
                transition.transition_id,
            )
            if existing is not None:
                if _transition_from_row(existing) != transition:
                    raise RegistryConflictError("transition ID has conflicting content")
                return _snapshot(artifact, transition)
            previous_row = await connection.fetchrow(
                """
                SELECT * FROM model_release_transition
                WHERE release_id = $1
                ORDER BY occurred_at DESC, transition_id DESC
                LIMIT 1 FOR UPDATE
                """,
                transition.release_id,
            )
            previous = _transition_from_row(previous_row) if previous_row is not None else None
            _validate_transition(artifact, transition, previous)
            if transition.to_state in {ReleaseState.CANARY, ReleaseState.ACTIVE}:
                conflict = await connection.fetchval(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (t.release_id) t.release_id, t.to_state
                        FROM model_release_transition t
                        JOIN model_release r USING (release_id)
                        WHERE r.component = $1 AND t.release_id <> $2
                        ORDER BY t.release_id, t.occurred_at DESC, t.transition_id DESC
                    )
                    SELECT EXISTS (
                        SELECT 1 FROM latest WHERE to_state = $3
                    )
                    """,
                    artifact.component.value,
                    artifact.release_id,
                    transition.to_state.value,
                )
                if conflict:
                    raise RegistryInvariantError(
                        f"component already has a {transition.to_state.value} release"
                    )
            await _insert_transition(connection, transition)
        return _snapshot(artifact, transition)

    async def current(self, component: ModelComponent) -> list[ModelReleaseSnapshot]:
        pool = await self._get_pool()
        rows = await pool.fetch(_CURRENT_RELEASES_SQL, component.value)
        return [_snapshot_from_row(row) for row in rows]

    async def route(self, component: ModelComponent, subject_id: str) -> ModelArtifact:
        if not subject_id:
            raise ValueError("subject_id cannot be empty")
        return _select_route(component, subject_id, await self.current(component))

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


async def _insert_transition(connection: asyncpg.Connection, transition: ReleaseTransition) -> None:
    await connection.execute(
        """
        INSERT INTO model_release_transition
            (transition_id, release_id, from_state, to_state, rollout_percent,
             occurred_at, actor, reason)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        transition.transition_id,
        transition.release_id,
        transition.from_state.value if transition.from_state is not None else None,
        transition.to_state.value,
        transition.rollout_percent,
        transition.occurred_at,
        transition.actor,
        transition.reason,
    )


def _artifact_from_row(row: asyncpg.Record) -> ModelArtifact:
    return ModelArtifact(
        release_id=row["release_id"],
        component=ModelComponent(row["component"]),
        model_id=row["model_id"],
        artifact_digest=row["artifact_digest"],
        prompt_version=row["prompt_version"],
        schema_version=row["schema_version"],
        evaluation_dataset_digest=row["evaluation_dataset_digest"],
        evaluation_report_digest=row["evaluation_report_digest"],
        quality_gate_passed=row["quality_gate_passed"],
        evaluated_at=row["evaluated_at"],
        created_at=row["created_at"],
        created_by=row["created_by"],
    )


def _transition_from_row(row: asyncpg.Record) -> ReleaseTransition:
    from_state = row["from_state"]
    return ReleaseTransition(
        transition_id=row["transition_id"],
        release_id=row["release_id"],
        from_state=ReleaseState(from_state) if from_state is not None else None,
        to_state=ReleaseState(row["to_state"]),
        rollout_percent=row["rollout_percent"],
        occurred_at=row["occurred_at"],
        actor=row["actor"],
        reason=row["reason"],
    )


def _snapshot_from_row(row: asyncpg.Record) -> ModelReleaseSnapshot:
    return ModelReleaseSnapshot(
        artifact=_artifact_from_row(row),
        state=ReleaseState(row["to_state"]),
        rollout_percent=row["rollout_percent"],
        changed_at=row["changed_at"],
    )
