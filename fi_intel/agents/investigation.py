"""Bounded, replayable investigation trajectory state."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from typing import Any, Protocol, TypeVar, cast, runtime_checkable

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from fi_intel.logging import safe_error_summary


class InvestigationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    ABSTAINED = "abstained"
    HELD = "held"
    DEFERRED = "deferred"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    PUBLISHED = "published"


class StepStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    TIMED_OUT = "timed_out"
    SKIPPED_DUPLICATE = "skipped_duplicate"


class StopReason(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    AMBIGUOUS_IDENTITY = "ambiguous_identity"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TOOL_FAILURE = "tool_failure"
    POLICY_REJECTED = "policy_rejected"


class InvestigationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = "bounded-investigation-v1"
    max_steps: int = Field(default=14, ge=1, le=50)
    max_tool_calls: int = Field(default=12, ge=1, le=30)
    max_calls_by_tool: dict[str, int] = Field(
        default_factory=lambda: {
            "graph_entry": 1,
            "entity_profile": 1,
            "entity_neighborhood": 1,
            "support_search": 1,
            "contradiction_search": 1,
            "timeseries_lookup": 1,
            "precedent_search": 1,
            "reasoning": 1,
            "entailment": 1,
            "validation": 1,
        }
    )
    per_step_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    end_to_end_timeout_seconds: float = Field(default=120.0, gt=0.0, le=1_800.0)
    max_attempts_per_step: int = Field(default=2, ge=1, le=5)
    retry_base_delay_ms: int = Field(default=100, ge=0, le=5_000)


class InvestigationStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    step_id: str
    investigation_id: str
    sequence: int = Field(ge=1)
    operation: str
    status: StepStatus
    input_payload: dict[str, Any]
    output_payload: dict[str, Any]
    input_digest: str
    output_digest: str
    started_at: AwareDatetime
    finished_at: AwareDatetime
    duration_ms: float = Field(ge=0.0)
    error_type: str | None = None
    safe_error_summary: str | None = None


class InvestigationTrajectory(BaseModel):
    model_config = ConfigDict(frozen=True)

    investigation_id: str
    run_id: str
    signal_id: str
    policy_version: str
    state: InvestigationState
    started_at: AwareDatetime
    updated_at: AwareDatetime
    steps: tuple[InvestigationStep, ...] = ()
    stop_reason: StopReason | None = None
    missing_coverage: tuple[str, ...] = ()


@runtime_checkable
class InvestigationStore(Protocol):
    async def load(self, investigation_id: str) -> InvestigationTrajectory | None: ...

    async def put(self, trajectory: InvestigationTrajectory) -> None: ...


class InvestigationConflictError(RuntimeError):
    pass


class InvestigationBudgetError(RuntimeError):
    pass


class RepeatedInvestigationStepError(RuntimeError):
    pass


class InMemoryInvestigationStore:
    def __init__(self) -> None:
        self._items: dict[str, InvestigationTrajectory] = {}

    async def load(self, investigation_id: str) -> InvestigationTrajectory | None:
        return self._items.get(investigation_id)

    async def put(self, trajectory: InvestigationTrajectory) -> None:
        existing = self._items.get(trajectory.investigation_id)
        if existing is not None:
            if existing.run_id != trajectory.run_id or existing.signal_id != trajectory.signal_id:
                raise InvestigationConflictError("investigation identity has conflicting owners")
            if len(trajectory.steps) < len(existing.steps):
                raise InvestigationConflictError("investigation steps are append-only")
            if trajectory.steps[: len(existing.steps)] != existing.steps:
                raise InvestigationConflictError(
                    "persisted investigation steps cannot be rewritten"
                )
        self._items[trajectory.investigation_id] = trajectory


class PostgresInvestigationStore:
    """Durable append-only store targeting migration 0014."""

    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def load(self, investigation_id: str) -> InvestigationTrajectory | None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            identity = await connection.fetchrow(
                """
                SELECT investigation_id, run_id, signal_id, policy_version, started_at
                FROM investigation_v3 WHERE investigation_id = $1
                """,
                investigation_id,
            )
            if identity is None:
                return None
            transition = await connection.fetchrow(
                """
                SELECT state, stop_reason, missing_coverage, occurred_at
                FROM investigation_transition_v3
                WHERE investigation_id = $1
                ORDER BY occurred_at DESC, transition_id DESC LIMIT 1
                """,
                investigation_id,
            )
            rows = await connection.fetch(
                """
                SELECT step_id, sequence, operation, status, input_payload,
                       output_payload, input_digest, output_digest, started_at,
                       finished_at, duration_ms, error_type, safe_error_summary
                FROM investigation_step_v3
                WHERE investigation_id = $1 ORDER BY sequence
                """,
                investigation_id,
            )
        steps = tuple(
            InvestigationStep(
                step_id=row["step_id"],
                investigation_id=investigation_id,
                sequence=row["sequence"],
                operation=row["operation"],
                status=row["status"],
                input_payload=_database_json(row["input_payload"]),
                output_payload=_database_json(row["output_payload"]),
                input_digest=row["input_digest"],
                output_digest=row["output_digest"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                duration_ms=row["duration_ms"],
                error_type=row["error_type"],
                safe_error_summary=row["safe_error_summary"],
            )
            for row in rows
        )
        updated_at = transition["occurred_at"] if transition is not None else identity["started_at"]
        return InvestigationTrajectory(
            investigation_id=identity["investigation_id"],
            run_id=identity["run_id"],
            signal_id=identity["signal_id"],
            policy_version=identity["policy_version"],
            state=(transition["state"] if transition is not None else InvestigationState.RUNNING),
            started_at=identity["started_at"],
            updated_at=updated_at,
            steps=steps,
            stop_reason=(transition["stop_reason"] if transition is not None else None),
            missing_coverage=(
                tuple(transition["missing_coverage"] or ()) if transition is not None else ()
            ),
        )

    async def put(self, trajectory: InvestigationTrajectory) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(
                """
                INSERT INTO investigation_v3 (
                    investigation_id, run_id, signal_id, policy_version, started_at
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (investigation_id) DO NOTHING
                """,
                trajectory.investigation_id,
                trajectory.run_id,
                trajectory.signal_id,
                trajectory.policy_version,
                trajectory.started_at,
            )
            owner = await connection.fetchrow(
                """
                SELECT run_id, signal_id, policy_version FROM investigation_v3
                WHERE investigation_id = $1
                """,
                trajectory.investigation_id,
            )
            if owner is None or (owner["run_id"], owner["signal_id"], owner["policy_version"]) != (
                trajectory.run_id,
                trajectory.signal_id,
                trajectory.policy_version,
            ):
                raise InvestigationConflictError("investigation identity has conflicting owners")
            for step in trajectory.steps:
                await connection.execute(
                    """
                    INSERT INTO investigation_step_v3 (
                        step_id, investigation_id, sequence, operation, status,
                        input_payload, output_payload, input_digest, output_digest,
                        started_at, finished_at, duration_ms, error_type, safe_error_summary
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9,
                        $10, $11, $12, $13, $14
                    ) ON CONFLICT (step_id) DO NOTHING
                    """,
                    step.step_id,
                    trajectory.investigation_id,
                    step.sequence,
                    step.operation,
                    step.status.value,
                    json.dumps(step.input_payload, sort_keys=True),
                    json.dumps(step.output_payload, sort_keys=True),
                    step.input_digest,
                    step.output_digest,
                    step.started_at,
                    step.finished_at,
                    step.duration_ms,
                    step.error_type,
                    step.safe_error_summary,
                )
            transition_payload = "|".join(
                [
                    trajectory.investigation_id,
                    trajectory.state.value,
                    trajectory.stop_reason.value if trajectory.stop_reason else "",
                    trajectory.updated_at.isoformat(),
                ]
            )
            transition_id = hashlib.sha256(transition_payload.encode()).hexdigest()
            await connection.execute(
                """
                INSERT INTO investigation_transition_v3 (
                    transition_id, investigation_id, state, stop_reason,
                    missing_coverage, occurred_at
                ) VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (transition_id) DO NOTHING
                """,
                transition_id,
                trajectory.investigation_id,
                trajectory.state.value,
                trajectory.stop_reason.value if trajectory.stop_reason else None,
                list(trajectory.missing_coverage),
                trajectory.updated_at,
            )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


def _database_json(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"unexpected JSON payload type {type(value).__name__}")


ResultT = TypeVar("ResultT")


def stable_investigation_id(run_id: str, signal_id: str, policy_version: str) -> str:
    return hashlib.sha256(f"{run_id}|{signal_id}|{policy_version}".encode()).hexdigest()


def _json_payload(value: object) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        encoded: object = value.model_dump(mode="json")
    elif isinstance(value, dict):
        encoded = value
    elif isinstance(value, (list, tuple)):
        encoded = {"items": list(value)}
    elif value is None:
        encoded = {}
    else:
        encoded = {"value": str(value)}
    parsed: object = json.loads(json.dumps(encoded, default=str, sort_keys=True))
    if not isinstance(parsed, dict):
        raise TypeError("investigation payload must serialize to an object")
    return {str(key): item for key, item in parsed.items()}


def _digest(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


class InvestigationSession:
    """Policy-owned execution and append-only trajectory recording."""

    def __init__(
        self,
        trajectory: InvestigationTrajectory,
        store: InvestigationStore,
        policy: InvestigationPolicy,
    ) -> None:
        self._trajectory = trajectory
        self._store = store
        self._policy = policy
        self._started_monotonic = monotonic()

    @classmethod
    async def start(
        cls,
        *,
        run_id: str,
        signal_id: str,
        store: InvestigationStore,
        policy: InvestigationPolicy,
    ) -> InvestigationSession:
        investigation_id = stable_investigation_id(run_id, signal_id, policy.version)
        existing = await store.load(investigation_id)
        if existing is None:
            now = datetime.now(UTC)
            existing = InvestigationTrajectory(
                investigation_id=investigation_id,
                run_id=run_id,
                signal_id=signal_id,
                policy_version=policy.version,
                state=InvestigationState.RUNNING,
                started_at=now,
                updated_at=now,
            )
            await store.put(existing)
        return cls(existing, store, policy)

    @property
    def trajectory(self) -> InvestigationTrajectory:
        return self._trajectory

    async def run_step(  # noqa: C901
        self,
        operation: str,
        input_value: object,
        call: Callable[[], Awaitable[ResultT]],
    ) -> ResultT:
        input_payload = _json_payload(input_value)
        input_digest = _digest(input_payload)
        if any(
            step.operation == operation
            and step.input_digest == input_digest
            and step.status is StepStatus.SUCCEEDED
            for step in self._trajectory.steps
        ):
            raise RepeatedInvestigationStepError(
                f"repeated identical tool call blocked for {operation}"
            )
        if self._trajectory.state is not InvestigationState.RUNNING:
            raise InvestigationConflictError(
                f"cannot append a step to {self._trajectory.state.value} investigation"
            )
        for attempt in range(1, self._policy.max_attempts_per_step + 1):
            self._check_attempt_budget(operation)
            started_at = datetime.now(UTC)
            started = monotonic()
            status = StepStatus.SUCCEEDED
            output_payload: dict[str, Any] = {}
            error_type: str | None = None
            safe_error: str | None = None
            caught: BaseException | None = None
            result: ResultT | None = None
            try:
                async with asyncio.timeout(self._policy.per_step_timeout_seconds):
                    result = await call()
                output_payload = _json_payload(result)
            except asyncio.CancelledError as exc:
                status = StepStatus.FAILED_RETRYABLE
                error_type = type(exc).__name__
                safe_error = f"{operation} was cancelled"
                caught = exc
            except TimeoutError as exc:
                status = StepStatus.TIMED_OUT
                error_type = "TimeoutError"
                safe_error = f"{operation} exceeded its governed deadline"
                caught = exc
            except (ValueError, TypeError) as exc:
                status = StepStatus.FAILED_TERMINAL
                error_type = type(exc).__name__
                safe_error = safe_error_summary(exc)
                caught = exc
            except Exception as exc:
                status = StepStatus.FAILED_RETRYABLE
                error_type = type(exc).__name__
                safe_error = safe_error_summary(exc)
                caught = exc
            await self._record_attempt(
                operation=operation,
                input_payload=input_payload,
                input_digest=input_digest,
                output_payload=output_payload,
                status=status,
                started_at=started_at,
                started_monotonic=started,
                error_type=error_type,
                safe_error=safe_error,
            )
            if caught is None:
                return cast(ResultT, result)
            if isinstance(caught, (asyncio.CancelledError, ValueError, TypeError)):
                raise caught
            if attempt == self._policy.max_attempts_per_step:
                raise caught
            await asyncio.sleep(self._retry_delay_seconds(operation, input_digest, attempt))
        raise RuntimeError("investigation retry loop terminated without a result")

    def _check_attempt_budget(self, operation: str) -> None:
        if len(self._trajectory.steps) >= self._policy.max_steps:
            raise InvestigationBudgetError("maximum investigation steps exhausted")
        if monotonic() - self._started_monotonic >= self._policy.end_to_end_timeout_seconds:
            raise InvestigationBudgetError("end-to-end investigation deadline exhausted")
        counts = Counter(step.operation for step in self._trajectory.steps)
        if sum(counts.values()) >= self._policy.max_tool_calls:
            raise InvestigationBudgetError("maximum investigation tool calls exhausted")
        allowed = self._policy.max_calls_by_tool.get(operation, 0)
        allowed_attempts = allowed * self._policy.max_attempts_per_step
        if allowed_attempts <= counts[operation]:
            raise InvestigationBudgetError(f"tool call budget exhausted for {operation}")

    async def _record_attempt(
        self,
        *,
        operation: str,
        input_payload: dict[str, Any],
        input_digest: str,
        output_payload: dict[str, Any],
        status: StepStatus,
        started_at: datetime,
        started_monotonic: float,
        error_type: str | None,
        safe_error: str | None,
    ) -> None:
        sequence = len(self._trajectory.steps) + 1
        finished_at = datetime.now(UTC)
        output_digest = _digest(output_payload)
        step_id = hashlib.sha256(
            f"{self._trajectory.investigation_id}|{sequence}|{operation}|{input_digest}".encode()
        ).hexdigest()
        step = InvestigationStep(
            step_id=step_id,
            investigation_id=self._trajectory.investigation_id,
            sequence=sequence,
            operation=operation,
            status=status,
            input_payload=input_payload,
            output_payload=output_payload,
            input_digest=input_digest,
            output_digest=output_digest,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(monotonic() - started_monotonic) * 1_000.0,
            error_type=error_type,
            safe_error_summary=safe_error,
        )
        self._trajectory = self._trajectory.model_copy(
            update={
                "steps": (*self._trajectory.steps, step),
                "updated_at": finished_at,
            }
        )
        await self._store.put(self._trajectory)

    def _retry_delay_seconds(self, operation: str, input_digest: str, attempt: int) -> float:
        jitter = int.from_bytes(
            hashlib.sha256(f"{operation}|{input_digest}|{attempt}".encode()).digest()[:2],
            "big",
        ) % max(self._policy.retry_base_delay_ms, 1)
        delay_ms: int = self._policy.retry_base_delay_ms * (2 ** (attempt - 1)) + jitter
        return delay_ms / 1_000.0

    async def finish(
        self,
        state: InvestigationState,
        reason: StopReason,
        *,
        missing_coverage: tuple[str, ...] = (),
    ) -> InvestigationTrajectory:
        if self._trajectory.state is not InvestigationState.RUNNING:
            if (
                self._trajectory.state is state
                and self._trajectory.stop_reason is reason
                and self._trajectory.missing_coverage == missing_coverage
            ):
                return self._trajectory
            raise InvestigationConflictError(
                f"cannot transition terminal investigation from "
                f"{self._trajectory.state.value} to {state.value}"
            )
        if state is InvestigationState.RUNNING:
            raise InvestigationConflictError("finish requires a terminal investigation state")
        now = datetime.now(UTC)
        self._trajectory = self._trajectory.model_copy(
            update={
                "state": state,
                "stop_reason": reason,
                "missing_coverage": missing_coverage,
                "updated_at": now,
            }
        )
        await self._store.put(self._trajectory)
        return self._trajectory


__all__ = [
    "InMemoryInvestigationStore",
    "InvestigationBudgetError",
    "InvestigationPolicy",
    "InvestigationSession",
    "InvestigationState",
    "InvestigationStep",
    "InvestigationStore",
    "InvestigationTrajectory",
    "PostgresInvestigationStore",
    "RepeatedInvestigationStepError",
    "StepStatus",
    "StopReason",
    "stable_investigation_id",
]
