"""Durable ingestion run, item, quarantine, and watermark state."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Protocol, Self, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from fi_intel.application.raw import RawHeader
from fi_intel.ledger.models import AccessPolicy
from fi_intel.sources.canonical import BarrierSide


class ControlConflictError(RuntimeError):
    """Stored run or job state conflicts with an idempotent request."""


class ControlInvariantError(RuntimeError):
    """An ingestion state transition or watermark update is invalid."""


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class JobStatus(StrEnum):
    RECEIVED = "received"
    RAW_ARCHIVED = "raw_archived"
    CANONICALIZED = "canonicalized"
    COMMITTED = "committed"
    NOT_NOVEL = "not_novel"
    QUARANTINED = "quarantined"


TERMINAL_JOB_STATUSES = frozenset({JobStatus.COMMITTED, JobStatus.NOT_NOVEL, JobStatus.QUARANTINED})

_ALLOWED_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.RECEIVED: frozenset({JobStatus.RAW_ARCHIVED, JobStatus.QUARANTINED}),
    JobStatus.RAW_ARCHIVED: frozenset({JobStatus.CANONICALIZED, JobStatus.QUARANTINED}),
    JobStatus.CANONICALIZED: frozenset(
        {JobStatus.COMMITTED, JobStatus.NOT_NOVEL, JobStatus.QUARANTINED}
    ),
    JobStatus.COMMITTED: frozenset(),
    JobStatus.NOT_NOVEL: frozenset(),
    JobStatus.QUARANTINED: frozenset(),
}


class ControlModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IngestRun(ControlModel):
    run_id: UUID
    source_id: str = Field(min_length=1)
    status: RunStatus
    requested_by: str = Field(min_length=1)
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    policy_id: UUID

    @model_validator(mode="after")
    def _finished_state_is_consistent(self) -> Self:
        if self.status is RunStatus.RUNNING and self.finished_at is not None:
            raise ValueError("a running ingest run cannot have finished_at")
        if self.status is not RunStatus.RUNNING and self.finished_at is None:
            raise ValueError("a terminal ingest run requires finished_at")
        return self


class IngestJob(ControlModel):
    job_id: UUID
    run_id: UUID
    raw_asset_id: UUID
    source_id: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1)
    headers: tuple[RawHeader, ...] = ()
    fetched_at: AwareDatetime
    source_published_at: AwareDatetime | None = None
    access_policy: AccessPolicy
    status: JobStatus
    archive_uri: str | None = None
    result_document_version_id: UUID | None = None
    attempt: int = Field(ge=1)
    started_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _archive_state_is_consistent(self) -> Self:
        needs_archive = self.status not in {JobStatus.RECEIVED, JobStatus.QUARANTINED}
        if needs_archive and self.archive_uri is None:
            raise ValueError("post-archive job state requires archive_uri")
        if self.status in {JobStatus.COMMITTED, JobStatus.NOT_NOVEL}:
            if self.result_document_version_id is None:
                raise ValueError("successful job requires result_document_version_id")
        if self.updated_at < self.started_at:
            raise ValueError("job updated_at precedes started_at")
        return self


class SourceWatermark(ControlModel):
    source_id: str = Field(min_length=1)
    partition_key: str = "default"
    position: str = Field(min_length=1)
    sequence_number: int = Field(ge=0)
    observed_at: AwareDatetime
    run_id: UUID
    job_id: UUID


class QuarantineRecord(ControlModel):
    quarantine_id: UUID
    job_id: UUID
    stage: str = Field(min_length=1)
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool
    recorded_at: AwareDatetime


@runtime_checkable
class IngestionControlStore(Protocol):
    async def create_run(self, run: IngestRun) -> None: ...

    async def finish_run(self, run_id: UUID, status: RunStatus, finished_at: datetime) -> None: ...

    async def create_job(self, job: IngestJob) -> None: ...

    async def transition_job(
        self,
        job_id: UUID,
        expected: JobStatus,
        requested: JobStatus,
        occurred_at: datetime,
        *,
        archive_uri: str | None = None,
        detail: str = "",
    ) -> IngestJob: ...

    async def complete_job(
        self,
        job_id: UUID,
        expected: JobStatus,
        requested: JobStatus,
        watermark: SourceWatermark,
        occurred_at: datetime,
        result_document_version_id: UUID,
    ) -> IngestJob: ...

    async def quarantine_job(
        self,
        job_id: UUID,
        expected: JobStatus,
        record: QuarantineRecord,
        watermark: SourceWatermark,
    ) -> IngestJob: ...

    async def load_job(self, job_id: UUID) -> IngestJob | None: ...

    async def load_watermark(
        self, source_id: str, partition_key: str = "default"
    ) -> SourceWatermark | None: ...

    async def list_quarantine(self, job_id: UUID) -> list[QuarantineRecord]: ...

    async def close(self) -> None: ...


class InMemoryIngestionControlStore:
    """Atomic service-free reference store."""

    def __init__(self) -> None:
        self._runs: dict[UUID, IngestRun] = {}
        self._jobs: dict[UUID, IngestJob] = {}
        self._watermarks: dict[tuple[str, str], SourceWatermark] = {}
        self._quarantine: dict[UUID, QuarantineRecord] = {}
        self._transitions: set[tuple[UUID, JobStatus, JobStatus]] = set()

    async def create_run(self, run: IngestRun) -> None:
        previous = self._runs.get(run.run_id)
        if previous is not None and previous != run:
            raise ControlConflictError("run ID has conflicting content")
        self._runs.setdefault(run.run_id, run)

    async def finish_run(self, run_id: UUID, status: RunStatus, finished_at: datetime) -> None:
        if status is RunStatus.RUNNING:
            raise ControlInvariantError("finish_run requires a terminal status")
        run = self._runs.get(run_id)
        if run is None:
            raise ControlInvariantError("ingest run is unknown")
        updated = run.model_copy(update={"status": status, "finished_at": finished_at})
        if run.status is not RunStatus.RUNNING and run != updated:
            raise ControlConflictError("ingest run is already finished")
        self._runs[run_id] = updated

    async def create_job(self, job: IngestJob) -> None:
        if job.status is not JobStatus.RECEIVED:
            raise ControlInvariantError("new ingest jobs must start as received")
        run = self._runs.get(job.run_id)
        if run is None or run.status is not RunStatus.RUNNING:
            raise ControlInvariantError("job requires a running ingest run")
        if run.source_id != job.source_id or run.policy_id != job.access_policy.policy_id:
            raise ControlInvariantError("job source or policy differs from its run")
        previous = self._jobs.get(job.job_id)
        if previous is not None and previous != job:
            raise ControlConflictError("job ID has conflicting content")
        self._jobs.setdefault(job.job_id, job)

    async def transition_job(
        self,
        job_id: UUID,
        expected: JobStatus,
        requested: JobStatus,
        occurred_at: datetime,
        *,
        archive_uri: str | None = None,
        detail: str = "",
    ) -> IngestJob:
        job = self._require_transition(job_id, expected, requested, occurred_at)
        resolved_uri = archive_uri if archive_uri is not None else job.archive_uri
        updated = job.model_copy(
            update={
                "status": requested,
                "archive_uri": resolved_uri,
                "updated_at": occurred_at,
            }
        )
        updated = IngestJob.model_validate(updated.model_dump())
        self._jobs[job_id] = updated
        self._transitions.add((job_id, expected, requested))
        return updated

    async def complete_job(
        self,
        job_id: UUID,
        expected: JobStatus,
        requested: JobStatus,
        watermark: SourceWatermark,
        occurred_at: datetime,
        result_document_version_id: UUID,
    ) -> IngestJob:
        if requested not in {JobStatus.COMMITTED, JobStatus.NOT_NOVEL}:
            raise ControlInvariantError("complete_job requires a success status")
        job = self._require_transition(job_id, expected, requested, occurred_at)
        self._validate_watermark(job, watermark)
        self._check_watermark_advance(watermark)
        updated = job.model_copy(
            update={
                "status": requested,
                "updated_at": occurred_at,
                "result_document_version_id": result_document_version_id,
            }
        )
        updated = IngestJob.model_validate(updated.model_dump())
        self._jobs[job_id] = updated
        self._watermarks[(watermark.source_id, watermark.partition_key)] = watermark
        self._transitions.add((job_id, expected, requested))
        return updated

    async def quarantine_job(
        self,
        job_id: UUID,
        expected: JobStatus,
        record: QuarantineRecord,
        watermark: SourceWatermark,
    ) -> IngestJob:
        if record.job_id != job_id:
            raise ControlInvariantError("quarantine record references another job")
        job = self._require_transition(job_id, expected, JobStatus.QUARANTINED, record.recorded_at)
        self._validate_watermark(job, watermark)
        self._check_watermark_advance(watermark)
        previous = self._quarantine.get(record.quarantine_id)
        if previous is not None and previous != record:
            raise ControlConflictError("quarantine ID has conflicting content")
        updated = job.model_copy(
            update={"status": JobStatus.QUARANTINED, "updated_at": record.recorded_at}
        )
        self._jobs[job_id] = updated
        self._quarantine.setdefault(record.quarantine_id, record)
        self._watermarks[(watermark.source_id, watermark.partition_key)] = watermark
        self._transitions.add((job_id, expected, JobStatus.QUARANTINED))
        return updated

    async def load_job(self, job_id: UUID) -> IngestJob | None:
        return self._jobs.get(job_id)

    async def load_watermark(
        self, source_id: str, partition_key: str = "default"
    ) -> SourceWatermark | None:
        return self._watermarks.get((source_id, partition_key))

    async def list_quarantine(self, job_id: UUID) -> list[QuarantineRecord]:
        records = [item for item in self._quarantine.values() if item.job_id == job_id]
        records.sort(key=lambda item: (item.recorded_at, str(item.quarantine_id)))
        return records

    async def close(self) -> None:
        return None

    def _require_transition(
        self,
        job_id: UUID,
        expected: JobStatus,
        requested: JobStatus,
        occurred_at: datetime,
    ) -> IngestJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise ControlInvariantError("ingest job is unknown")
        if job.status is not expected:
            raise ControlInvariantError(f"stale job state: expected {expected}, found {job.status}")
        if requested not in _ALLOWED_JOB_TRANSITIONS[expected]:
            raise ControlInvariantError(f"invalid job transition: {expected} -> {requested}")
        if occurred_at <= job.updated_at:
            raise ControlInvariantError("job transition time must increase")
        return job

    @staticmethod
    def _validate_watermark(job: IngestJob, watermark: SourceWatermark) -> None:
        if watermark.job_id != job.job_id or watermark.run_id != job.run_id:
            raise ControlInvariantError("watermark does not belong to the completed job")
        if watermark.source_id != job.source_id:
            raise ControlInvariantError("watermark source differs from its job")

    def _check_watermark_advance(self, watermark: SourceWatermark) -> None:
        key = (watermark.source_id, watermark.partition_key)
        previous = self._watermarks.get(key)
        if previous is not None:
            if watermark.sequence_number < previous.sequence_number:
                raise ControlInvariantError("watermark cannot move backwards")
            if (
                watermark.sequence_number == previous.sequence_number
                and watermark.position != previous.position
            ):
                raise ControlConflictError("watermark sequence has conflicting position")


class PostgresIngestionControlStore:
    """PostgreSQL control store targeting migration 0004."""

    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def create_run(self, run: IngestRun) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO ingest_run_v2 (
                run_id, source_id, status, requested_by, started_at, finished_at, policy_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (run_id) DO NOTHING
            """,
            run.run_id,
            run.source_id,
            run.status.value,
            run.requested_by,
            run.started_at,
            run.finished_at,
            run.policy_id,
        )

    async def finish_run(self, run_id: UUID, status: RunStatus, finished_at: datetime) -> None:
        if status is RunStatus.RUNNING:
            raise ControlInvariantError("finish_run requires a terminal status")
        pool = await self._get_pool()
        result = await pool.execute(
            """
            UPDATE ingest_run_v2 SET status = $2, finished_at = $3
            WHERE run_id = $1 AND status = 'running'
            """,
            run_id,
            status.value,
            finished_at,
        )
        if result == "UPDATE 0":
            row = await pool.fetchrow(
                "SELECT status, finished_at FROM ingest_run_v2 WHERE run_id = $1", run_id
            )
            if row is None or row["status"] != status.value or row["finished_at"] != finished_at:
                raise ControlConflictError("run is unknown or already finished differently")

    async def create_job(self, job: IngestJob) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO ingest_job_v2 (
                    job_id, run_id, raw_asset_id, source_id, external_id,
                    source_revision, content_hash, media_type, headers, fetched_at,
                    source_published_at, policy_id, status, archive_uri,
                    result_document_version_id, attempt, started_at, updated_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                ON CONFLICT (job_id) DO NOTHING
                """,
                job.job_id,
                job.run_id,
                job.raw_asset_id,
                job.source_id,
                job.external_id,
                job.source_revision,
                job.content_hash,
                job.media_type,
                json.dumps([header.model_dump() for header in job.headers]),
                job.fetched_at,
                job.source_published_at,
                job.access_policy.policy_id,
                job.status.value,
                job.archive_uri,
                job.result_document_version_id,
                job.attempt,
                job.started_at,
                job.updated_at,
            )
            await conn.execute(
                """
                INSERT INTO ingest_job_transition_v2 (
                    job_id, from_status, to_status, occurred_at, detail
                ) VALUES ($1, NULL, 'received', $2, 'item received')
                ON CONFLICT (job_id, to_status, occurred_at) DO NOTHING
                """,
                job.job_id,
                job.started_at,
            )

    async def transition_job(
        self,
        job_id: UUID,
        expected: JobStatus,
        requested: JobStatus,
        occurred_at: datetime,
        *,
        archive_uri: str | None = None,
        detail: str = "",
    ) -> IngestJob:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._transition(
                conn,
                job_id,
                expected,
                requested,
                occurred_at,
                archive_uri,
                detail,
            )
            return await self._load_job(conn, job_id)

    async def complete_job(
        self,
        job_id: UUID,
        expected: JobStatus,
        requested: JobStatus,
        watermark: SourceWatermark,
        occurred_at: datetime,
        result_document_version_id: UUID,
    ) -> IngestJob:
        if requested not in {JobStatus.COMMITTED, JobStatus.NOT_NOVEL}:
            raise ControlInvariantError("complete_job requires a success status")
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._transition(
                conn,
                job_id,
                expected,
                requested,
                occurred_at,
                None,
                "item complete",
                result_document_version_id,
            )
            await self._upsert_watermark(conn, watermark)
            return await self._load_job(conn, job_id)

    async def quarantine_job(
        self,
        job_id: UUID,
        expected: JobStatus,
        record: QuarantineRecord,
        watermark: SourceWatermark,
    ) -> IngestJob:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._transition(
                conn,
                job_id,
                expected,
                JobStatus.QUARANTINED,
                record.recorded_at,
                None,
                f"{record.stage}: {record.error_type}",
            )
            await conn.execute(
                """
                INSERT INTO ingest_quarantine_v2 (
                    quarantine_id, job_id, stage, error_type, message,
                    retryable, recorded_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (quarantine_id) DO NOTHING
                """,
                record.quarantine_id,
                record.job_id,
                record.stage,
                record.error_type,
                record.message,
                record.retryable,
                record.recorded_at,
            )
            await self._upsert_watermark(conn, watermark)
            return await self._load_job(conn, job_id)

    async def load_job(self, job_id: UUID) -> IngestJob | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await self._fetch_job_row(conn, job_id)
            return self._job_from_row(row) if row is not None else None

    async def load_watermark(
        self, source_id: str, partition_key: str = "default"
    ) -> SourceWatermark | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT * FROM ingest_watermark_v2
            WHERE source_id = $1 AND partition_key = $2
            """,
            source_id,
            partition_key,
        )
        if row is None:
            return None
        return SourceWatermark(
            source_id=row["source_id"],
            partition_key=row["partition_key"],
            position=row["position"],
            sequence_number=row["sequence_number"],
            observed_at=row["observed_at"],
            run_id=row["run_id"],
            job_id=row["job_id"],
        )

    async def list_quarantine(self, job_id: UUID) -> list[QuarantineRecord]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT * FROM ingest_quarantine_v2
            WHERE job_id = $1 ORDER BY recorded_at, quarantine_id
            """,
            job_id,
        )
        return [
            QuarantineRecord(
                quarantine_id=row["quarantine_id"],
                job_id=row["job_id"],
                stage=row["stage"],
                error_type=row["error_type"],
                message=row["message"],
                retryable=row["retryable"],
                recorded_at=row["recorded_at"],
            )
            for row in rows
        ]

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None

    async def _transition(
        self,
        conn: asyncpg.Connection,
        job_id: UUID,
        expected: JobStatus,
        requested: JobStatus,
        occurred_at: datetime,
        archive_uri: str | None,
        detail: str,
        result_document_version_id: UUID | None = None,
    ) -> None:
        if requested not in _ALLOWED_JOB_TRANSITIONS[expected]:
            raise ControlInvariantError(f"invalid job transition: {expected} -> {requested}")
        row = await conn.fetchrow(
            "SELECT status, updated_at FROM ingest_job_v2 WHERE job_id = $1 FOR UPDATE",
            job_id,
        )
        if row is None or row["status"] != expected.value:
            raise ControlInvariantError("ingest job state is unknown or stale")
        if occurred_at <= row["updated_at"]:
            raise ControlInvariantError("job transition time must increase")
        await conn.execute(
            """
            UPDATE ingest_job_v2
            SET status = $2,
                archive_uri = COALESCE($3, archive_uri),
                updated_at = $4,
                result_document_version_id = COALESCE($5, result_document_version_id)
            WHERE job_id = $1
            """,
            job_id,
            requested.value,
            archive_uri,
            occurred_at,
            result_document_version_id,
        )
        await conn.execute(
            """
            INSERT INTO ingest_job_transition_v2 (
                job_id, from_status, to_status, occurred_at, detail
            ) VALUES ($1,$2,$3,$4,$5)
            """,
            job_id,
            expected.value,
            requested.value,
            occurred_at,
            detail,
        )

    @staticmethod
    async def _upsert_watermark(conn: asyncpg.Connection, watermark: SourceWatermark) -> None:
        result = await conn.execute(
            """
            INSERT INTO ingest_watermark_v2 (
                source_id, partition_key, position, sequence_number,
                observed_at, run_id, job_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (source_id, partition_key) DO UPDATE
            SET position = EXCLUDED.position,
                sequence_number = EXCLUDED.sequence_number,
                observed_at = EXCLUDED.observed_at,
                run_id = EXCLUDED.run_id,
                job_id = EXCLUDED.job_id
            WHERE ingest_watermark_v2.sequence_number < EXCLUDED.sequence_number
               OR (ingest_watermark_v2.sequence_number = EXCLUDED.sequence_number
                   AND ingest_watermark_v2.position = EXCLUDED.position)
            """,
            watermark.source_id,
            watermark.partition_key,
            watermark.position,
            watermark.sequence_number,
            watermark.observed_at,
            watermark.run_id,
            watermark.job_id,
        )
        if result not in {"INSERT 0 1", "UPDATE 1"}:
            raise ControlConflictError("watermark regressed or conflicts at this sequence")

    async def _load_job(self, conn: asyncpg.Connection, job_id: UUID) -> IngestJob:
        row = await self._fetch_job_row(conn, job_id)
        if row is None:
            raise ControlInvariantError("ingest job is unknown")
        return self._job_from_row(row)

    @staticmethod
    async def _fetch_job_row(conn: asyncpg.Connection, job_id: UUID) -> asyncpg.Record | None:
        return await conn.fetchrow(
            """
            SELECT j.*, p.barrier_side, p.allowed_entitlement_groups, p.created_at
            FROM ingest_job_v2 j
            JOIN access_policy p USING (policy_id)
            WHERE j.job_id = $1
            """,
            job_id,
        )

    @staticmethod
    def _job_from_row(row: asyncpg.Record) -> IngestJob:
        raw_headers = json.loads(row["headers"])
        return IngestJob(
            job_id=row["job_id"],
            run_id=row["run_id"],
            raw_asset_id=row["raw_asset_id"],
            source_id=row["source_id"],
            external_id=row["external_id"],
            source_revision=row["source_revision"],
            content_hash=row["content_hash"],
            media_type=row["media_type"],
            headers=tuple(RawHeader.model_validate(item) for item in raw_headers),
            fetched_at=row["fetched_at"],
            source_published_at=row["source_published_at"],
            access_policy=AccessPolicy(
                policy_id=row["policy_id"],
                barrier_side=BarrierSide(row["barrier_side"]),
                allowed_entitlement_groups=frozenset(row["allowed_entitlement_groups"]),
                created_at=row["created_at"],
            ),
            status=row["status"],
            archive_uri=row["archive_uri"],
            result_document_version_id=row["result_document_version_id"],
            attempt=row["attempt"],
            started_at=row["started_at"],
            updated_at=row["updated_at"],
        )
