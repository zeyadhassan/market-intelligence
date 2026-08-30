"""Source health assessments and durable poll observations."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Protocol, Self, runtime_checkable
from uuid import UUID, uuid5

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from fi_intel.logging import safe_error_summary
from fi_intel.sources.acquisition import RawSourceCursor, RawSourcePoll
from fi_intel.sources.catalog import SourceKind, SourceRegistration

_OBSERVATION_NAMESPACE = UUID("b54ecada-c7b4-5ddb-8f79-ac5adba13f02")


class SourceHealth(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


class OperationsModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceOperationalState(OperationsModel):
    source_id: str = Field(min_length=1)
    partition_key: str = "default"
    cursor: RawSourceCursor | None = None
    last_successful_poll_at: AwareDatetime | None = None
    latest_source_published_at: AwareDatetime | None = None
    consecutive_failures: int = Field(ge=0)
    updated_at: AwareDatetime


class SourceObservation(OperationsModel):
    observation_id: UUID
    run_id: UUID
    source_id: str = Field(min_length=1)
    partition_key: str = "default"
    catalog_version: str = Field(min_length=1)
    policy_id: UUID
    health: SourceHealth
    started_at: AwareDatetime
    finished_at: AwareDatetime
    feed_modified: bool
    page_count: int = Field(ge=0)
    discovered_count: int = Field(ge=0)
    acquired_count: int = Field(ge=0)
    unchanged_count: int = Field(ge=0)
    committed_count: int = Field(ge=0)
    not_novel_count: int = Field(ge=0)
    quarantine_count: int = Field(ge=0)
    complete: bool
    fresh: bool
    silent: bool
    within_expected_volume: bool
    freshness_lag_seconds: float | None = Field(default=None, ge=0)
    latest_source_published_at: AwareDatetime | None = None
    error_type: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("source observation finishes before it starts")
        terminal_count = self.committed_count + self.not_novel_count + self.quarantine_count
        if self.acquired_count != terminal_count:
            raise ValueError("source result counts do not reconcile")
        if self.health is SourceHealth.FAILED and self.error_type is None:
            raise ValueError("failed source observation requires an error")
        return self


def source_observation_id(source_id: str, run_id: UUID) -> UUID:
    return uuid5(_OBSERVATION_NAMESPACE, f"{source_id}:{run_id}")


def assess_source_poll(
    registration: SourceRegistration,
    poll: RawSourcePoll,
    *,
    run_id: UUID,
    policy_id: UUID,
    started_at: datetime,
    finished_at: datetime,
    committed_count: int,
    not_novel_count: int,
    quarantine_count: int,
) -> tuple[SourceObservation, SourceOperationalState]:
    latest = poll.next_cursor.latest_source_published_at
    freshness_anchor = poll.polled_at if registration.kind is SourceKind.REFERENCE_API else latest
    lag = (
        max((finished_at - freshness_anchor).total_seconds(), 0.0)
        if freshness_anchor is not None
        else None
    )
    fresh = lag is not None and lag <= registration.freshness_sla_seconds
    silent = lag is None or lag > registration.silence_sla_seconds
    volume_ok = (not poll.feed_modified) or (
        registration.expected_min_items <= poll.discovered_count <= registration.expected_max_items
    )
    complete = (
        poll.discovered_count == len(poll.items) + poll.unchanged_count and quarantine_count == 0
    )
    health = (
        SourceHealth.HEALTHY
        if complete and fresh and not silent and volume_ok
        else SourceHealth.DEGRADED
    )
    observation = SourceObservation(
        observation_id=source_observation_id(registration.source_id, run_id),
        run_id=run_id,
        source_id=registration.source_id,
        partition_key=poll.partition_key,
        catalog_version=registration.catalog_version,
        policy_id=policy_id,
        health=health,
        started_at=started_at,
        finished_at=finished_at,
        feed_modified=poll.feed_modified,
        page_count=poll.page_count,
        discovered_count=poll.discovered_count,
        acquired_count=len(poll.items),
        unchanged_count=poll.unchanged_count,
        committed_count=committed_count,
        not_novel_count=not_novel_count,
        quarantine_count=quarantine_count,
        complete=complete,
        fresh=fresh,
        silent=silent,
        within_expected_volume=volume_ok,
        freshness_lag_seconds=lag,
        latest_source_published_at=latest,
    )
    state = SourceOperationalState(
        source_id=registration.source_id,
        partition_key=poll.partition_key,
        cursor=poll.next_cursor,
        last_successful_poll_at=finished_at,
        latest_source_published_at=latest,
        consecutive_failures=0,
        updated_at=finished_at,
    )
    return observation, state


def failed_source_observation(
    registration: SourceRegistration,
    *,
    run_id: UUID,
    policy_id: UUID,
    started_at: datetime,
    finished_at: datetime,
    error: Exception,
    previous: SourceOperationalState | None,
) -> tuple[SourceObservation, SourceOperationalState]:
    latest = previous.latest_source_published_at if previous is not None else None
    lag = max((finished_at - latest).total_seconds(), 0.0) if latest is not None else None
    observation = SourceObservation(
        observation_id=source_observation_id(registration.source_id, run_id),
        run_id=run_id,
        source_id=registration.source_id,
        catalog_version=registration.catalog_version,
        policy_id=policy_id,
        health=SourceHealth.FAILED,
        started_at=started_at,
        finished_at=finished_at,
        feed_modified=False,
        page_count=0,
        discovered_count=0,
        acquired_count=0,
        unchanged_count=0,
        committed_count=0,
        not_novel_count=0,
        quarantine_count=0,
        complete=False,
        fresh=False,
        silent=lag is None or lag > registration.silence_sla_seconds,
        within_expected_volume=False,
        freshness_lag_seconds=lag,
        latest_source_published_at=latest,
        error_type=type(error).__name__,
        error_message=safe_error_summary(error),
    )
    state = SourceOperationalState(
        source_id=registration.source_id,
        cursor=previous.cursor if previous is not None else None,
        last_successful_poll_at=(
            previous.last_successful_poll_at if previous is not None else None
        ),
        latest_source_published_at=latest,
        consecutive_failures=(previous.consecutive_failures if previous else 0) + 1,
        updated_at=finished_at,
    )
    return observation, state


@runtime_checkable
class SourceOperationsStore(Protocol):
    async def load_state(
        self, source_id: str, partition_key: str = "default"
    ) -> SourceOperationalState | None: ...

    async def record(
        self, observation: SourceObservation, state: SourceOperationalState
    ) -> None: ...

    async def list_observations(self, source_id: str) -> list[SourceObservation]: ...

    async def close(self) -> None: ...


class InMemorySourceOperationsStore:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str], SourceOperationalState] = {}
        self._observations: dict[UUID, SourceObservation] = {}

    async def load_state(
        self, source_id: str, partition_key: str = "default"
    ) -> SourceOperationalState | None:
        return self._states.get((source_id, partition_key))

    async def record(self, observation: SourceObservation, state: SourceOperationalState) -> None:
        if (observation.source_id, observation.partition_key) != (
            state.source_id,
            state.partition_key,
        ):
            raise ValueError("source observation and state identify different partitions")
        previous_observation = self._observations.get(observation.observation_id)
        if previous_observation is not None and previous_observation != observation:
            raise ValueError("source observation ID has conflicting content")
        key = (state.source_id, state.partition_key)
        previous_state = self._states.get(key)
        if previous_state is not None and state.updated_at < previous_state.updated_at:
            raise ValueError("source operational state cannot move backwards")
        self._observations.setdefault(observation.observation_id, observation)
        self._states[key] = state

    async def list_observations(self, source_id: str) -> list[SourceObservation]:
        rows = [row for row in self._observations.values() if row.source_id == source_id]
        rows.sort(key=lambda row: (row.finished_at, str(row.observation_id)))
        return rows

    async def close(self) -> None:
        return None


class PostgresSourceOperationsStore:
    """PostgreSQL implementation targeting migration 0007."""

    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def load_state(
        self, source_id: str, partition_key: str = "default"
    ) -> SourceOperationalState | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT * FROM source_poll_state_v2
            WHERE source_id = $1 AND partition_key = $2
            """,
            source_id,
            partition_key,
        )
        if row is None:
            return None
        cursor_data = json.loads(row["cursor_json"]) if row["cursor_json"] else None
        return SourceOperationalState(
            source_id=row["source_id"],
            partition_key=row["partition_key"],
            cursor=(
                RawSourceCursor.model_validate(cursor_data) if cursor_data is not None else None
            ),
            last_successful_poll_at=row["last_successful_poll_at"],
            latest_source_published_at=row["latest_source_published_at"],
            consecutive_failures=row["consecutive_failures"],
            updated_at=row["updated_at"],
        )

    async def record(self, observation: SourceObservation, state: SourceOperationalState) -> None:
        if (observation.source_id, observation.partition_key) != (
            state.source_id,
            state.partition_key,
        ):
            raise ValueError("source observation and state identify different partitions")
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO source_observation_v2 (
                    observation_id, run_id, source_id, partition_key,
                    catalog_version, policy_id, health, started_at, finished_at,
                    feed_modified, page_count, discovered_count, acquired_count,
                    unchanged_count, committed_count, not_novel_count,
                    quarantine_count, complete, fresh, silent,
                    within_expected_volume, freshness_lag_seconds,
                    latest_source_published_at, error_type, error_message
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                    $17,$18,$19,$20,$21,$22,$23,$24,$25
                ) ON CONFLICT (observation_id) DO NOTHING
                """,
                observation.observation_id,
                observation.run_id,
                observation.source_id,
                observation.partition_key,
                observation.catalog_version,
                observation.policy_id,
                observation.health.value,
                observation.started_at,
                observation.finished_at,
                observation.feed_modified,
                observation.page_count,
                observation.discovered_count,
                observation.acquired_count,
                observation.unchanged_count,
                observation.committed_count,
                observation.not_novel_count,
                observation.quarantine_count,
                observation.complete,
                observation.fresh,
                observation.silent,
                observation.within_expected_volume,
                observation.freshness_lag_seconds,
                observation.latest_source_published_at,
                observation.error_type,
                observation.error_message,
            )
            result = await conn.execute(
                """
                INSERT INTO source_poll_state_v2 (
                    source_id, partition_key, cursor_json,
                    last_successful_poll_at, latest_source_published_at,
                    consecutive_failures, updated_at
                ) VALUES ($1,$2,$3::jsonb,$4,$5,$6,$7)
                ON CONFLICT (source_id, partition_key) DO UPDATE SET
                    cursor_json = EXCLUDED.cursor_json,
                    last_successful_poll_at = EXCLUDED.last_successful_poll_at,
                    latest_source_published_at = EXCLUDED.latest_source_published_at,
                    consecutive_failures = EXCLUDED.consecutive_failures,
                    updated_at = EXCLUDED.updated_at
                WHERE source_poll_state_v2.updated_at <= EXCLUDED.updated_at
                """,
                state.source_id,
                state.partition_key,
                state.cursor.model_dump_json() if state.cursor is not None else None,
                state.last_successful_poll_at,
                state.latest_source_published_at,
                state.consecutive_failures,
                state.updated_at,
            )
            if result not in {"INSERT 0 1", "UPDATE 1"}:
                raise ValueError("source operational state cannot move backwards")

    async def list_observations(self, source_id: str) -> list[SourceObservation]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT * FROM source_observation_v2
            WHERE source_id = $1 ORDER BY finished_at, observation_id
            """,
            source_id,
        )
        return [self._observation_from_row(row) for row in rows]

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None

    @staticmethod
    def _observation_from_row(row: asyncpg.Record) -> SourceObservation:
        return SourceObservation(
            observation_id=row["observation_id"],
            run_id=row["run_id"],
            source_id=row["source_id"],
            partition_key=row["partition_key"],
            catalog_version=row["catalog_version"],
            policy_id=row["policy_id"],
            health=row["health"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            feed_modified=row["feed_modified"],
            page_count=row["page_count"],
            discovered_count=row["discovered_count"],
            acquired_count=row["acquired_count"],
            unchanged_count=row["unchanged_count"],
            committed_count=row["committed_count"],
            not_novel_count=row["not_novel_count"],
            quarantine_count=row["quarantine_count"],
            complete=row["complete"],
            fresh=row["fresh"],
            silent=row["silent"],
            within_expected_volume=row["within_expected_volume"],
            freshness_lag_seconds=row["freshness_lag_seconds"],
            latest_source_published_at=row["latest_source_published_at"],
            error_type=row["error_type"],
            error_message=row["error_message"],
        )
