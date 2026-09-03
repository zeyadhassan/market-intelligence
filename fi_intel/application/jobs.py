"""Durable leased job ownership for daily canonical analysis."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from fi_intel.application.runtime_resources import PostgresPoolProvider
from fi_intel.config import Settings
from fi_intel.governance.access import RequestPrincipal
from fi_intel.logging import safe_error_summary


def stable_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, default=str, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class AnalysisJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    HELD = "held"
    DEFERRED = "deferred"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"


_CLAIMABLE = {
    AnalysisJobState.QUEUED,
    AnalysisJobState.DEFERRED,
    AnalysisJobState.RETRYABLE_FAILED,
}
_TERMINAL = {
    AnalysisJobState.COMPLETE,
    AnalysisJobState.PARTIAL,
    AnalysisJobState.HELD,
    AnalysisJobState.TERMINAL_FAILED,
}


class PrincipalSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str
    principal_id: str
    entitlement_group: str
    side: str
    desks: tuple[str, ...]
    roles: tuple[str, ...]
    purposes: tuple[str, ...]

    @classmethod
    def from_principal(cls, principal: RequestPrincipal) -> PrincipalSnapshot:
        return cls(
            subject=principal.subject,
            principal_id=principal.principal.principal_id,
            entitlement_group=principal.principal.entitlement_group,
            side=principal.principal.side.value,
            desks=tuple(sorted(principal.desks)),
            roles=tuple(sorted(principal.roles)),
            purposes=tuple(sorted(principal.purposes)),
        )

    def restore(self) -> RequestPrincipal:
        from fi_intel.retrieval.entitlement import Principal, Side

        return RequestPrincipal(
            subject=self.subject,
            principal=Principal(
                principal_id=self.principal_id,
                entitlement_group=self.entitlement_group,
                side=Side(self.side),
            ),
            desks=frozenset(self.desks),
            roles=frozenset(self.roles),
            purposes=frozenset(self.purposes),
        )


class AnalysisJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str
    business_date: date
    topic_ids: tuple[str, ...] = Field(min_length=1)
    authorization_scope: str
    principal: PrincipalSnapshot
    input_manifest: dict[str, Any]
    temporal_pin: AwareDatetime
    state: AnalysisJobState
    run_id: str | None = None
    attempt_count: int = Field(ge=0)
    next_attempt_at: AwareDatetime
    lease_owner: str | None = None
    lease_expires_at: AwareDatetime | None = None
    requested_at: AwareDatetime
    updated_at: AwareDatetime
    safe_error_summary: str | None = None

    @classmethod
    def request(
        cls,
        settings: Settings,
        principal: RequestPrincipal,
        topic_ids: frozenset[str],
        authorization_scope: str,
        source_ids: tuple[str, ...],
        *,
        requested_at: datetime | None = None,
        topic_revisions: tuple[str, ...] = (),
        input_revision: tuple[str, ...] = (),
    ) -> AnalysisJob:
        if not topic_ids:
            raise ValueError("analysis job requires at least one topic")
        now = requested_at or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("analysis job request time must be timezone-aware")
        local_now = now.astimezone(ZoneInfo(settings.daily_analysis_timezone))
        business_date = (local_now - timedelta(hours=settings.daily_analysis_cutoff_hour)).date()
        topics = tuple(sorted(topic_ids))
        sources = tuple(sorted(source_ids))
        manifest: dict[str, Any] = {
            "business_date": business_date.isoformat(),
            # JSONB restores arrays as lists. Keep the in-memory request in the
            # same canonical shape so an idempotent first insert cannot appear
            # to conflict with the row that was just persisted.
            "topic_ids": list(topics),
            "authorization_scope": authorization_scope,
            "required_source_ids": list(sources),
            "covered_entity_leis": sorted(
                item.strip().upper()
                for item in settings.covered_entity_leis.split(",")
                if item.strip()
            ),
            "analysis_mode": settings.analysis_mode,
            "window_version": settings.daily_analysis_window_version,
        }
        if topic_revisions:
            # A new governed topic or policy version is new analytical work,
            # even when it is deployed inside an existing business window.
            manifest["topic_revisions"] = list(sorted(topic_revisions))
        if input_revision:
            # A forced refresh is still deterministic: it creates new work
            # only when the latest durable source observations have changed.
            manifest["input_revision"] = list(sorted(input_revision))
        idempotency_key = (
            f"daily:{business_date.isoformat()}:{authorization_scope}:{stable_digest(manifest)}"
        )
        return cls(
            job_id=stable_digest(idempotency_key),
            idempotency_key=idempotency_key,
            business_date=business_date,
            topic_ids=topics,
            authorization_scope=authorization_scope,
            principal=PrincipalSnapshot.from_principal(principal),
            input_manifest=manifest,
            temporal_pin=now,
            state=AnalysisJobState.QUEUED,
            attempt_count=0,
            next_attempt_at=now,
            requested_at=now,
            updated_at=now,
        )


class AnalysisJobConflictError(RuntimeError):
    """A deterministic job identity was reused for different inputs."""


@runtime_checkable
class AnalysisJobStore(Protocol):
    async def enqueue(self, job: AnalysisJob) -> AnalysisJob: ...

    async def get(self, job_id: str) -> AnalysisJob | None: ...

    async def latest(
        self, topic_id: str, authorization_scope: str, business_date: date | None = None
    ) -> AnalysisJob | None: ...

    async def claim(self, worker_id: str, lease_seconds: float) -> AnalysisJob | None: ...

    async def finish(
        self,
        job_id: str,
        worker_id: str,
        state: AnalysisJobState,
        *,
        run_id: str | None,
        safe_detail: str | None = None,
    ) -> AnalysisJob: ...

    async def defer(
        self,
        job_id: str,
        worker_id: str,
        *,
        safe_detail: str,
        delay_seconds: float,
    ) -> AnalysisJob: ...

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        error: Exception,
        *,
        retryable: bool,
        max_attempts: int,
    ) -> AnalysisJob: ...

    async def close(self) -> None: ...


class PostgresAnalysisJobStore:
    """Concurrent job queue with SKIP LOCKED and expired-lease recovery."""

    def __init__(
        self,
        dsn: str,
        *,
        pool: asyncpg.Pool | None = None,
        pool_provider: PostgresPoolProvider | None = None,
    ) -> None:
        self._dsn = dsn
        self._pool = pool
        self._pool_provider = pool_provider
        self._owns_pool = pool is None and pool_provider is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = (
                await self._pool_provider.get_pool()
                if self._pool_provider is not None
                else await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
            )
        return self._pool

    async def enqueue(self, job: AnalysisJob) -> AnalysisJob:
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(
                """
                INSERT INTO analysis_job_v4 (
                    job_id, idempotency_key, business_date, topic_ids,
                    authorization_scope, principal_snapshot, input_manifest,
                    temporal_pin, state, run_id, attempt_count, next_attempt_at,
                    requested_at, updated_at
                ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9,$10,$11,$12,$13,$14)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                job.job_id,
                job.idempotency_key,
                job.business_date,
                list(job.topic_ids),
                job.authorization_scope,
                job.principal.model_dump_json(),
                json.dumps(job.input_manifest, sort_keys=True),
                job.temporal_pin,
                job.state.value,
                job.run_id,
                job.attempt_count,
                job.next_attempt_at,
                job.requested_at,
                job.updated_at,
            )
            row = await connection.fetchrow(
                "SELECT * FROM analysis_job_v4 WHERE idempotency_key = $1 FOR SHARE",
                job.idempotency_key,
            )
            if row is None:
                raise RuntimeError("analysis job was not persisted")
            stored = _job_from_row(row)
            immutable = (
                "job_id",
                "business_date",
                "topic_ids",
                "authorization_scope",
                "input_manifest",
            )
            if any(getattr(stored, name) != getattr(job, name) for name in immutable):
                raise AnalysisJobConflictError(
                    "analysis idempotency key conflicts with immutable request content"
                )
            if stored.requested_at == job.requested_at:
                await self._append_transition(
                    connection,
                    stored,
                    None,
                    AnalysisJobState.QUEUED,
                    worker_id=None,
                    detail="analysis request enqueued",
                )
            return stored

    async def get(self, job_id: str) -> AnalysisJob | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM analysis_job_v4 WHERE job_id = $1", job_id)
        return _job_from_row(row) if row is not None else None

    async def latest(
        self, topic_id: str, authorization_scope: str, business_date: date | None = None
    ) -> AnalysisJob | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT * FROM analysis_job_v4
            WHERE $1 = ANY(topic_ids) AND authorization_scope = $2
              AND ($3::date IS NULL OR business_date = $3)
            ORDER BY business_date DESC, requested_at DESC, job_id DESC LIMIT 1
            """,
            topic_id,
            authorization_scope,
            business_date,
        )
        return _job_from_row(row) if row is not None else None

    async def claim(self, worker_id: str, lease_seconds: float) -> AnalysisJob | None:
        if not worker_id:
            raise ValueError("analysis worker ID cannot be empty")
        if lease_seconds <= 0:
            raise ValueError("analysis lease must be positive")
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=lease_seconds)
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                WITH candidate AS (
                    SELECT job_id FROM analysis_job_v4
                    WHERE next_attempt_at <= $1
                      AND (
                        state IN ('queued','deferred','retryable_failed')
                        OR (state = 'running' AND lease_expires_at <= $1)
                      )
                    ORDER BY next_attempt_at, requested_at, job_id
                    FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE analysis_job_v4 job
                SET state = 'running', attempt_count = attempt_count + 1,
                    lease_owner = $2, lease_expires_at = $3, updated_at = $1,
                    safe_error_summary = NULL
                FROM candidate WHERE job.job_id = candidate.job_id
                RETURNING job.*
                """,
                now,
                worker_id,
                expires,
            )
            if row is None:
                return None
            claimed = _job_from_row(row)
            await self._append_transition(
                connection,
                claimed,
                None,
                AnalysisJobState.RUNNING,
                worker_id=worker_id,
                detail="claimed or recovered expired lease",
            )
            return claimed

    async def finish(
        self,
        job_id: str,
        worker_id: str,
        state: AnalysisJobState,
        *,
        run_id: str | None,
        safe_detail: str | None = None,
    ) -> AnalysisJob:
        if state not in _TERMINAL:
            raise ValueError(f"cannot finish analysis job in {state.value!r}")
        return await self._transition_owned(
            job_id,
            worker_id,
            state,
            run_id=run_id,
            next_attempt_at=datetime.now(UTC),
            safe_detail=safe_detail,
        )

    async def defer(
        self,
        job_id: str,
        worker_id: str,
        *,
        safe_detail: str,
        delay_seconds: float,
    ) -> AnalysisJob:
        """Return an owned job to the queue while upstream projection finishes."""

        if delay_seconds <= 0:
            raise ValueError("analysis deferral delay must be positive")
        return await self._transition_owned(
            job_id,
            worker_id,
            AnalysisJobState.DEFERRED,
            run_id=None,
            next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
            safe_detail=safe_detail[:500],
        )

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        error: Exception,
        *,
        retryable: bool,
        max_attempts: int,
    ) -> AnalysisJob:
        current = await self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        exhausted = current.attempt_count >= max_attempts
        target = (
            AnalysisJobState.RETRYABLE_FAILED
            if retryable and not exhausted
            else AnalysisJobState.TERMINAL_FAILED
        )
        delay = min(3600, 2 ** max(0, current.attempt_count - 1) * 30)
        return await self._transition_owned(
            job_id,
            worker_id,
            target,
            run_id=current.run_id,
            next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay),
            safe_detail=safe_error_summary(error),
        )

    async def _transition_owned(
        self,
        job_id: str,
        worker_id: str,
        state: AnalysisJobState,
        *,
        run_id: str | None,
        next_attempt_at: datetime,
        safe_detail: str | None,
    ) -> AnalysisJob:
        now = datetime.now(UTC)
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            previous_row = await connection.fetchrow(
                "SELECT * FROM analysis_job_v4 WHERE job_id = $1 FOR UPDATE", job_id
            )
            if previous_row is None:
                raise KeyError(job_id)
            previous = _job_from_row(previous_row)
            if previous.state is not AnalysisJobState.RUNNING or previous.lease_owner != worker_id:
                raise RuntimeError("analysis job lease is not owned by this worker")
            row = await connection.fetchrow(
                """
                UPDATE analysis_job_v4
                SET state=$3, run_id=COALESCE($4,run_id), next_attempt_at=$5,
                    lease_owner=NULL, lease_expires_at=NULL, updated_at=$6,
                    safe_error_summary=$7
                WHERE job_id=$1 AND lease_owner=$2 AND state='running'
                RETURNING *
                """,
                job_id,
                worker_id,
                state.value,
                run_id,
                next_attempt_at,
                now,
                safe_detail,
            )
            if row is None:
                raise RuntimeError("analysis job lease was lost")
            updated = _job_from_row(row)
            await self._append_transition(
                connection,
                updated,
                previous.state,
                state,
                worker_id=worker_id,
                detail=safe_detail,
            )
            return updated

    @staticmethod
    async def _append_transition(
        connection: asyncpg.Connection,
        job: AnalysisJob,
        from_state: AnalysisJobState | None,
        to_state: AnalysisJobState,
        *,
        worker_id: str | None,
        detail: str | None,
    ) -> None:
        identity = stable_digest(
            [job.job_id, job.attempt_count, to_state.value, job.updated_at.isoformat()]
        )
        await connection.execute(
            """
            INSERT INTO analysis_job_transition_v4 (
                transition_id, job_id, from_state, to_state, worker_id,
                safe_detail, occurred_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING
            """,
            identity,
            job.job_id,
            from_state.value if from_state is not None else None,
            to_state.value,
            worker_id,
            detail,
            job.updated_at,
        )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


def _json(value: object) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _job_from_row(row: Any) -> AnalysisJob:
    return AnalysisJob(
        job_id=str(row["job_id"]),
        idempotency_key=str(row["idempotency_key"]),
        business_date=row["business_date"],
        topic_ids=tuple(str(item) for item in row["topic_ids"]),
        authorization_scope=str(row["authorization_scope"]),
        principal=PrincipalSnapshot.model_validate(_json(row["principal_snapshot"])),
        input_manifest=dict(_json(row["input_manifest"])),
        temporal_pin=row["temporal_pin"],
        state=AnalysisJobState(str(row["state"])),
        run_id=str(row["run_id"]) if row["run_id"] is not None else None,
        attempt_count=int(row["attempt_count"]),
        next_attempt_at=row["next_attempt_at"],
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
        lease_expires_at=row["lease_expires_at"],
        requested_at=row["requested_at"],
        updated_at=row["updated_at"],
        safe_error_summary=(
            str(row["safe_error_summary"]) if row["safe_error_summary"] is not None else None
        ),
    )


__all__ = [
    "AnalysisJob",
    "AnalysisJobConflictError",
    "AnalysisJobState",
    "AnalysisJobStore",
    "PostgresAnalysisJobStore",
    "PrincipalSnapshot",
    "stable_digest",
]
