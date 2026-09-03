"""Read-only diagnostics and explicit recovery operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

import asyncpg
from pydantic import BaseModel, ConfigDict

from fi_intel.application.runtime_resources import RuntimeResources
from fi_intel.config import Settings
from fi_intel.sources.adapters.gcc_official import GCC_OFFICIAL_SOURCES


class DeadLetterView(BaseModel):
    model_config = ConfigDict(frozen=True)

    dead_letter_id: str
    event_id: UUID
    event_type: str
    retryable: bool
    attempt_count: int
    safe_error_summary: str
    quarantined_at: datetime
    aggregate_id: UUID
    source_id: str | None = None


class ProjectionFailureView(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_version_id: UUID
    source_id: str
    state: str
    attempt_count: int
    safe_error_summary: str | None
    updated_at: datetime


class RuntimeQueueStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    analysis_jobs: dict[str, int]
    search_jobs: dict[str, int]
    document_jobs: dict[str, int]
    outbox_pending: int
    dead_letters: int
    deliveries: dict[str, int]
    retrieval_index_status: str
    indexed_document_versions: int
    unindexed_document_versions: int
    embedding_calls_last_hour: dict[str, int]


class WorkerRuntimeView(BaseModel):
    model_config = ConfigDict(frozen=True)

    worker_type: str
    worker_id: str | None = None
    status: str
    operation: str
    heartbeat_at: datetime | None = None
    iteration_started_at: datetime | None = None
    iteration_finished_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    safe_error_summary: str | None = None


class PipelineStageRuntimeView(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: str
    label: str
    status: str
    detail: str
    active: int = 0
    pending: int = 0
    completed: int = 0
    failed: int = 0
    last_activity_at: datetime | None = None


class SourceRuntimeView(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    display_name: str
    country: str
    status: str
    run_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    discovered: int = 0
    acquired: int = 0
    committed: int = 0
    unchanged: int = 0
    quarantined: int = 0
    detail: str


class ModelRuntimeView(BaseModel):
    model_config = ConfigDict(frozen=True)

    component: str
    model: str
    status: str
    active_calls: int = 0
    calls_last_hour: int = 0
    succeeded_last_hour: int = 0
    failed_last_hour: int = 0
    input_tokens_last_hour: int = 0
    output_tokens_last_hour: int = 0
    average_latency_ms: float | None = None
    last_call_at: datetime | None = None
    last_outcome: str | None = None


class RuntimeEventView(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    occurred_at: datetime
    stage: str
    operation: str
    status: str
    message: str
    run_id: str | None = None
    worker_id: str | None = None
    duration_ms: float | None = None
    safe_error_summary: str | None = None


class RuntimeDashboardView(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    overall_status: str
    poll_interval_seconds: int = 2
    queue: RuntimeQueueStatus
    workers: tuple[WorkerRuntimeView, ...]
    stages: tuple[PipelineStageRuntimeView, ...]
    sources: tuple[SourceRuntimeView, ...]
    models: tuple[ModelRuntimeView, ...]
    events: tuple[RuntimeEventView, ...]


class OperatorService:
    def __init__(
        self,
        resources: RuntimeResources | None = None,
        *,
        settings: Settings | None = None,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        if resources is None and (settings is None or pool is None):
            raise ValueError("operator service requires runtime resources or settings and pool")
        self._resources = resources
        self._settings = resources.settings if resources is not None else settings
        self._pool = resources.postgres_pool if resources is not None else pool
        if self._settings is None or self._pool is None:
            raise RuntimeError("operator service dependencies did not initialize")

    async def dead_letters(self, *, limit: int = 100) -> list[DeadLetterView]:
        if not 1 <= limit <= 1_000:
            raise ValueError("dead-letter limit must be between 1 and 1000")
        rows = await self._pool.fetch(
            """
            SELECT dead.*, event.aggregate_id, identity.source_id
            FROM outbox_dead_letter_v3 dead
            JOIN transactional_outbox event USING (event_id)
            LEFT JOIN document_version version
              ON event.event_type='document.versioned.v1'
             AND version.document_version_id=event.aggregate_id
            LEFT JOIN document_identity identity USING (document_id)
            ORDER BY dead.quarantined_at DESC, dead.dead_letter_id DESC LIMIT $1
            """,
            limit,
        )
        return [
            DeadLetterView(
                dead_letter_id=str(row["dead_letter_id"]),
                event_id=row["event_id"],
                event_type=str(row["event_type"]),
                retryable=bool(row["retryable"]),
                attempt_count=int(row["attempt_count"]),
                safe_error_summary=str(row["safe_error_summary"]),
                quarantined_at=row["quarantined_at"],
                aggregate_id=row["aggregate_id"],
                source_id=(str(row["source_id"]) if row["source_id"] is not None else None),
            )
            for row in rows
        ]

    async def projection_failures(self, *, limit: int = 100) -> list[ProjectionFailureView]:
        """List document projection failures with their originating source."""

        if not 1 <= limit <= 1_000:
            raise ValueError("projection-failure limit must be between 1 and 1000")
        rows = await self._pool.fetch(
            """
            SELECT job.document_version_id, identity.source_id, job.state,
                   job.attempt_count, job.safe_error_summary, job.updated_at
            FROM document_processing_job_v4 job
            JOIN document_version version USING (document_version_id)
            JOIN document_identity identity USING (document_id)
            WHERE job.state <> 'complete'
            ORDER BY job.updated_at DESC, job.document_version_id DESC
            LIMIT $1
            """,
            limit,
        )
        return [
            ProjectionFailureView(
                document_version_id=row["document_version_id"],
                source_id=str(row["source_id"]),
                state=str(row["state"]),
                attempt_count=int(row["attempt_count"]),
                safe_error_summary=(
                    str(row["safe_error_summary"])
                    if row["safe_error_summary"] is not None
                    else None
                ),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def replay_dead_letter(self, dead_letter_id: str) -> UUID:
        """Create a correlated immutable replay event; never mutate history."""

        pool = self._pool
        async with pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                SELECT dead.*, event.*
                FROM outbox_dead_letter_v3 dead
                JOIN transactional_outbox event USING (event_id)
                WHERE dead.dead_letter_id=$1
                FOR UPDATE OF dead
                """,
                dead_letter_id,
            )
            if row is None:
                raise KeyError(f"unknown dead letter {dead_letter_id!r}")
            aggregate_lock = f"{row['aggregate_type']}:{row['aggregate_id']}:{row['event_type']}"
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                aggregate_lock,
            )
            replay_number = (
                int(
                    await connection.fetchval(
                        """
                    SELECT count(*) FROM transactional_outbox
                    WHERE causation_id=$1 AND event_type=$2
                    """,
                        row["event_id"],
                        row["event_type"],
                    )
                )
                + 1
            )
            replay_id = uuid5(
                NAMESPACE_URL,
                f"fi-intel:outbox-replay:{dead_letter_id}:{replay_number}",
            )
            aggregate_version = int(
                await connection.fetchval(
                    """
                    SELECT COALESCE(max(aggregate_version),0) + 1
                    FROM transactional_outbox
                    WHERE aggregate_type=$1 AND aggregate_id=$2
                    """,
                    row["aggregate_type"],
                    row["aggregate_id"],
                )
            )
            await connection.execute(
                """
                INSERT INTO transactional_outbox (
                    event_id, event_type, aggregate_type, aggregate_id,
                    aggregate_version, occurred_at, correlation_id, causation_id,
                    policy_id, payload, published_at, publish_attempts,
                    next_attempt_at, lease_owner, lease_expires_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NULL,0,$6,NULL,NULL)
                """,
                replay_id,
                row["event_type"],
                row["aggregate_type"],
                row["aggregate_id"],
                aggregate_version,
                datetime.now(UTC),
                row["correlation_id"],
                row["event_id"],
                row["policy_id"],
                row["payload"],
            )
            return replay_id

    async def replay_document_version(self, document_version_id: UUID) -> UUID:
        """Reprocess one immutable archived version without refetching its source."""

        pool = self._pool
        archive_row = await pool.fetchrow(
            """
            SELECT version.normalized_object_uri, raw.object_uri
            FROM document_version version
            JOIN raw_asset raw USING (raw_asset_id)
            WHERE version.document_version_id=$1
            """,
            document_version_id,
        )
        if archive_row is None:
            raise KeyError(f"unknown document version {document_version_id}")
        if self._resources is None:
            raise RuntimeError("archive replay requires complete runtime resources")
        await self._resources.raw_archive.get(str(archive_row["normalized_object_uri"]))
        await self._resources.raw_archive.get(str(archive_row["object_uri"]))
        async with pool.acquire() as connection, connection.transaction():
            original = await connection.fetchrow(
                """
                SELECT * FROM transactional_outbox
                WHERE aggregate_id=$1 AND event_type='document.versioned.v1'
                ORDER BY aggregate_version DESC, occurred_at DESC LIMIT 1
                FOR UPDATE
                """,
                document_version_id,
            )
            if original is None:
                raise RuntimeError("document version has no authoritative projection event")
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"document:{document_version_id}",
            )
            replay_number = (
                int(
                    await connection.fetchval(
                        "SELECT count(*) FROM transactional_outbox WHERE causation_id=$1",
                        original["event_id"],
                    )
                )
                + 1
            )
            replay_id = uuid5(
                NAMESPACE_URL,
                f"fi-intel:archive-replay:{document_version_id}:{replay_number}",
            )
            next_version = int(
                await connection.fetchval(
                    """
                    SELECT COALESCE(max(aggregate_version),0) + 1
                    FROM transactional_outbox
                    WHERE aggregate_type=$1 AND aggregate_id=$2
                    """,
                    original["aggregate_type"],
                    document_version_id,
                )
            )
            now = datetime.now(UTC)
            await connection.execute(
                """
                INSERT INTO transactional_outbox (
                    event_id, event_type, aggregate_type, aggregate_id,
                    aggregate_version, occurred_at, correlation_id, causation_id,
                    policy_id, payload, published_at, publish_attempts,
                    next_attempt_at, lease_owner, lease_expires_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NULL,0,$6,NULL,NULL)
                """,
                replay_id,
                original["event_type"],
                original["aggregate_type"],
                document_version_id,
                next_version,
                now,
                original["correlation_id"],
                original["event_id"],
                original["policy_id"],
                original["payload"],
            )
            return replay_id

    async def queue_status(self) -> RuntimeQueueStatus:
        pool = self._pool
        analysis = await _latest_analysis_state_counts(pool)
        search = await _latest_search_state_counts(pool)
        documents, deliveries = await _state_counts(
            pool,
            (
                ("document_processing_job_v4", "state"),
                ("delivery_attempt_v4", "state"),
            ),
        )
        pending = await pool.fetchval(
            "SELECT count(*) FROM transactional_outbox WHERE published_at IS NULL"
        )
        dead = await pool.fetchval("SELECT count(*) FROM outbox_dead_letter_v3")
        retrieval_status = await pool.fetchval(
            """
            SELECT status FROM retrieval_index_state
            WHERE index_name='document_chunk'
            """
        )
        retrieval_counts = await pool.fetchrow(
            """
            WITH projected AS (
                SELECT DISTINCT version.document_version_id
                FROM document_version version
                WHERE EXISTS (
                    SELECT 1 FROM document projected_document
                    WHERE projected_document.metadata->>'ledger_document_version_id'
                          = version.document_version_id::text
                )
            )
            SELECT
                count(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM document_chunk chunk
                    WHERE chunk.document_version_id=projected.document_version_id
                      AND chunk.canonical_lineage
                      AND chunk.embedding IS NOT NULL
                )) AS indexed,
                count(*) FILTER (WHERE NOT EXISTS (
                    SELECT 1 FROM document_chunk chunk
                    WHERE chunk.document_version_id=projected.document_version_id
                      AND chunk.canonical_lineage
                      AND chunk.embedding IS NOT NULL
                )) AS unindexed
            FROM projected
            """
        )
        if retrieval_counts is None:
            raise RuntimeError("retrieval projection counts query returned no row")
        embedding_rows = await pool.fetch(
            """
            SELECT status, count(*) AS total
            FROM model_call_log
            WHERE component='embedding'
              AND recorded_at >= now() - interval '1 hour'
            GROUP BY status ORDER BY status
            """
        )
        return RuntimeQueueStatus(
            analysis_jobs=analysis,
            search_jobs=search,
            document_jobs=documents,
            outbox_pending=int(pending),
            dead_letters=int(dead),
            deliveries=deliveries,
            retrieval_index_status=(
                str(retrieval_status) if retrieval_status is not None else "missing"
            ),
            indexed_document_versions=int(retrieval_counts["indexed"] or 0),
            unindexed_document_versions=int(retrieval_counts["unindexed"] or 0),
            embedding_calls_last_hour={
                str(row["status"]): int(row["total"]) for row in embedding_rows
            },
        )

    async def dashboard(self, *, event_limit: int = 200) -> RuntimeDashboardView:
        """Build one payload-safe control-room snapshot from durable state."""

        if not 1 <= event_limit <= 500:
            raise ValueError("runtime event limit must be between 1 and 500")
        pool = self._pool
        now = datetime.now(UTC)
        queue = await self.queue_status()
        workers = await self._worker_views(now)
        sources = await self._source_views()
        models = await self._model_views()
        stage_counts = await pool.fetchrow(
            """
            WITH latest_analysis_job AS (
              SELECT DISTINCT ON (authorization_scope, topic_ids)
                     run_id, topic_ids
              FROM analysis_job_v4
              ORDER BY authorization_scope, topic_ids, requested_at DESC, job_id DESC
            ),
            current_detector AS (
              SELECT detector.*
              FROM detector_execution_v3 detector
              JOIN analysis_scope_job_v3 scope USING (job_id)
              JOIN latest_analysis_job analysis
                ON analysis.run_id=scope.run_id
               AND scope.topic_id=ANY(analysis.topic_ids)
            )
            SELECT
              (SELECT count(*) FROM ingest_job_v2
               WHERE status IN ('received','raw_archived','canonicalized')) AS ingest_pending,
              (SELECT count(*) FROM ingest_job_v2
               WHERE status IN ('committed','not_novel')
                 AND updated_at >= now() - interval '24 hours') AS ingest_completed,
              (SELECT count(*) FROM ingest_job_v2
               WHERE status='quarantined'
                 AND updated_at >= now() - interval '24 hours') AS ingest_failed,
              (SELECT max(updated_at) FROM ingest_job_v2) AS ingest_last,
              (SELECT count(*) FROM analysis_job_v4
               WHERE state='running') AS analysis_active,
              (SELECT count(*) FROM analysis_job_v4
               WHERE state IN ('queued','deferred','retryable_failed')) AS analysis_pending,
              (SELECT count(*) FROM analysis_job_v4
               WHERE state='terminal_failed'
                 AND updated_at >= now() - interval '24 hours') AS analysis_failed,
              (SELECT count(*) FROM analysis_job_v4
               WHERE state IN ('complete','partial','held')
                 AND updated_at >= now() - interval '24 hours') AS analysis_completed,
              (SELECT max(updated_at) FROM analysis_job_v4) AS analysis_last,
              (SELECT max(updated_at) FROM search_job_v4) AS search_last,
              (SELECT max(updated_at) FROM delivery_attempt_v4) AS delivery_last,
              (SELECT count(*) FROM current_detector
               WHERE finished_at IS NULL) AS detector_active,
              (SELECT count(*) FROM current_detector
               WHERE state='completed'
                 AND finished_at >= now() - interval '24 hours') AS detector_completed,
              (SELECT count(*) FROM current_detector
               WHERE state LIKE 'failed%'
                 AND started_at >= now() - interval '24 hours') AS detector_failed,
              (SELECT count(*) FROM current_detector
               WHERE state IN ('held_coverage','deferred')
                 AND started_at >= now() - interval '24 hours') AS detector_attention,
              (SELECT max(COALESCE(finished_at, started_at))
               FROM current_detector) AS detector_last,
              (SELECT count(*) FROM investigation_step_v3
               WHERE status IN ('succeeded','skipped_duplicate')
                 AND started_at >= now() - interval '24 hours') AS research_completed,
              (SELECT count(*) FROM investigation_step_v3
               WHERE status IN ('failed_retryable','failed_terminal','timed_out')
                 AND started_at >= now() - interval '24 hours') AS research_failed,
              (SELECT max(finished_at) FROM investigation_step_v3) AS research_last,
              (SELECT count(*) FROM validation_decision_v3
               WHERE decided_at >= now() - interval '24 hours') AS validation_completed,
              (SELECT count(*) FROM validation_decision_v3
               WHERE status NOT IN ('supported','accepted','passed')
                 AND decided_at >= now() - interval '24 hours') AS validation_rejected,
              (SELECT count(*) FROM result_version_v3
               WHERE created_at >= now() - interval '24 hours') AS published_results,
              (SELECT max(materialized_at) FROM daily_topic_read_model_v4) AS publication_last
            """
        )
        if stage_counts is None:
            raise RuntimeError("runtime stage counts query returned no row")
        stages = self._stage_views(
            queue,
            workers,
            sources,
            models,
            stage_counts,
        )
        events = await self._recent_events(event_limit)
        overall_status = self._overall_status(workers, stages)
        return RuntimeDashboardView(
            generated_at=now,
            overall_status=overall_status,
            queue=queue,
            workers=workers,
            stages=stages,
            sources=sources,
            models=models,
            events=events,
        )

    async def _worker_views(self, now: datetime) -> tuple[WorkerRuntimeView, ...]:
        rows = await self._pool.fetch(
            """
            SELECT DISTINCT ON (worker_type)
                   worker_id, worker_type, status, operation, heartbeat_at,
                   iteration_started_at, iteration_finished_at,
                   last_success_at, last_failure_at, safe_error_summary
            FROM runtime_worker_state_v1
            ORDER BY worker_type, heartbeat_at DESC, worker_id DESC
            """
        )
        by_type = {str(row["worker_type"]): row for row in rows}
        operations = {
            "scheduler": "schedule subscribed topic analysis",
            "source": "poll configured sources",
            "projection": "project, extract, and index documents",
            "analysis": "detect and research opportunities",
            "search": "execute analyst searches",
            "delivery": "assemble and deliver digests",
        }
        stale_after = timedelta(
            seconds=max(60.0, self._settings.worker_poll_interval_seconds * 4)
        )
        views: list[WorkerRuntimeView] = []
        for worker_type, operation in operations.items():
            row = by_type.get(worker_type)
            if row is None:
                views.append(
                    WorkerRuntimeView(
                        worker_type=worker_type,
                        status="offline",
                        operation=operation,
                        safe_error_summary="No heartbeat has been recorded.",
                    )
                )
                continue
            heartbeat_at = row["heartbeat_at"]
            status = str(row["status"])
            error = (
                str(row["safe_error_summary"])
                if row["safe_error_summary"] is not None
                else None
            )
            if heartbeat_at < now - stale_after and status != "stopped":
                status = "offline"
                error = "Worker heartbeat is stale."
            views.append(
                WorkerRuntimeView(
                    worker_type=worker_type,
                    worker_id=str(row["worker_id"]),
                    status=status,
                    operation=str(row["operation"]),
                    heartbeat_at=heartbeat_at,
                    iteration_started_at=row["iteration_started_at"],
                    iteration_finished_at=row["iteration_finished_at"],
                    last_success_at=row["last_success_at"],
                    last_failure_at=row["last_failure_at"],
                    safe_error_summary=error,
                )
            )
        return tuple(views)

    async def _source_views(self) -> tuple[SourceRuntimeView, ...]:
        configured = self._settings.configured_coverage_source_ids
        source_definitions = tuple(
            source
            for source in GCC_OFFICIAL_SOURCES
            if not configured or source.source_id in configured
        )
        source_ids = [source.source_id for source in source_definitions]
        rows = await self._pool.fetch(
            """
            SELECT requested.source_id,
                   run.run_id, run.status AS run_status,
                   run.started_at AS run_started_at,
                   run.finished_at AS run_finished_at,
                   observation.run_id AS observation_run_id,
                   observation.health, observation.complete,
                   observation.fresh, observation.silent,
                   observation.within_expected_volume,
                   observation.started_at, observation.finished_at,
                   observation.discovered_count, observation.acquired_count,
                   observation.committed_count, observation.unchanged_count,
                   observation.quarantine_count, observation.error_type,
                   observation.error_message,
                   current_run.acquired_count AS current_run_acquired,
                   current_run.committed_count AS current_run_committed,
                   current_run.unchanged_count AS current_run_unchanged,
                   current_run.quarantine_count AS current_run_quarantined
            FROM unnest($1::text[]) requested(source_id)
            LEFT JOIN LATERAL (
                SELECT run_id, status, started_at, finished_at
                FROM ingest_run_v2
                WHERE source_id=requested.source_id
                ORDER BY started_at DESC, run_id DESC LIMIT 1
            ) run ON TRUE
            LEFT JOIN LATERAL (
                SELECT * FROM source_observation_v2
                WHERE source_id=requested.source_id
                ORDER BY finished_at DESC, observation_id DESC LIMIT 1
            ) observation ON TRUE
            LEFT JOIN LATERAL (
                SELECT count(*) AS acquired_count,
                       count(*) FILTER (WHERE status='committed') AS committed_count,
                       count(*) FILTER (WHERE status='not_novel') AS unchanged_count,
                       count(*) FILTER (WHERE status='quarantined') AS quarantine_count
                FROM ingest_job_v2
                WHERE run_id=run.run_id
            ) current_run ON TRUE
            ORDER BY requested.source_id
            """,
            source_ids,
        )
        by_source = {str(row["source_id"]): row for row in rows}
        views: list[SourceRuntimeView] = []
        for source in source_definitions:
            row = by_source.get(source.source_id)
            if row is None or row["run_status"] is None:
                views.append(
                    SourceRuntimeView(
                        source_id=source.source_id,
                        display_name=source.display_name,
                        country=source.country,
                        status="not_started",
                        detail="This configured source has not been polled yet.",
                    )
                )
                continue
            if row["run_status"] == "running":
                status = "working"
                detail = "Fetching and ingesting this source now."
                started_at = row["run_started_at"]
            elif row["observation_run_id"] != row["run_id"]:
                status = (
                    "failed" if row["run_status"] == "failed" else "attention"
                )
                detail = (
                    f"Latest acquisition run ended as {row['run_status']} without a "
                    "matching coverage observation."
                )
                started_at = row["run_started_at"]
            else:
                started_at = row["started_at"]
                healthy = (
                    row["health"] == "healthy"
                    and row["complete"]
                    and row["fresh"]
                    and not row["silent"]
                    and row["within_expected_volume"]
                )
                if healthy:
                    status = "complete"
                    detail = "Latest source observation passed coverage checks."
                elif row["health"] == "failed":
                    status = "failed"
                    detail = (
                        f"{row['error_type'] or 'source failure'}: "
                        f"{row['error_message'] or 'no safe error detail'}"
                    )
                else:
                    status = "attention"
                    detail = "Latest observation is incomplete, stale, silent, or out of range."
            observation_matches = row["observation_run_id"] == row["run_id"]
            views.append(
                SourceRuntimeView(
                    source_id=source.source_id,
                    display_name=source.display_name,
                    country=source.country,
                    status=status,
                    run_id=str(row["run_id"]),
                    started_at=started_at,
                    finished_at=(
                        row["finished_at"]
                        if observation_matches
                        else row["run_finished_at"]
                    ),
                    discovered=int(
                        (
                            row["discovered_count"]
                            if observation_matches
                            else row["current_run_acquired"]
                        )
                        or 0
                    ),
                    acquired=int(
                        (
                            row["acquired_count"]
                            if observation_matches
                            else row["current_run_acquired"]
                        )
                        or 0
                    ),
                    committed=int(
                        (
                            row["committed_count"]
                            if observation_matches
                            else row["current_run_committed"]
                        )
                        or 0
                    ),
                    unchanged=int(
                        (
                            row["unchanged_count"]
                            if observation_matches
                            else row["current_run_unchanged"]
                        )
                        or 0
                    ),
                    quarantined=int(
                        (
                            row["quarantine_count"]
                            if observation_matches
                            else row["current_run_quarantined"]
                        )
                        or 0
                    ),
                    detail=detail,
                )
            )
        return tuple(views)

    async def _model_views(self) -> tuple[ModelRuntimeView, ...]:
        pool = self._pool
        rows = await pool.fetch(
            """
            WITH hourly AS (
              SELECT component, model,
                     count(*) AS calls,
                     count(*) FILTER (WHERE status='succeeded') AS succeeded,
                     count(*) FILTER (WHERE status<>'succeeded') AS failed,
                     COALESCE(sum(input_tokens),0) AS input_tokens,
                     COALESCE(sum(output_tokens),0) AS output_tokens,
                     avg(latency_ms) AS average_latency_ms,
                     max(recorded_at) AS last_call_at
              FROM model_call_log
              WHERE recorded_at >= now() - interval '1 hour'
              GROUP BY component, model
            ), latest AS (
              SELECT DISTINCT ON (component, model)
                     component, model, status
              FROM model_call_log
              ORDER BY component, model, recorded_at DESC, call_id DESC
            )
            SELECT hourly.*, latest.status AS last_outcome
            FROM hourly JOIN latest USING (component, model)
            ORDER BY hourly.component, hourly.model
            """
        )
        active_rows = await pool.fetch(
            """
            WITH latest AS (
              SELECT DISTINCT ON (correlation_id)
                     correlation_id, component, status, occurred_at
              FROM runtime_event_v1
              WHERE category='model' AND correlation_id IS NOT NULL
              ORDER BY correlation_id, occurred_at DESC, event_id DESC
            )
            SELECT component, count(*) AS active_calls
            FROM latest
            WHERE status='started'
              AND occurred_at >= now() - interval '10 minutes'
            GROUP BY component
            """
        )
        active = {
            str(row["component"]): int(row["active_calls"]) for row in active_rows
        }
        configured = {
            "extract": self._settings.extraction_model,
            "embedding": self._settings.embedding_model or "not configured",
            "research": self._settings.research_model,
            "reranker": self._settings.reranker_model,
            "entailment": self._settings.entailment_model,
        }
        observed: dict[tuple[str, str], asyncpg.Record | None] = {
            (str(row["component"]), str(row["model"])): row for row in rows
        }
        for component, model in configured.items():
            observed.setdefault((component, model), None)
        views: list[ModelRuntimeView] = []
        for (component, model), row in sorted(observed.items()):
            active_calls = active.get(component, 0)
            last_outcome = str(row["last_outcome"]) if row is not None else None
            status = (
                "working"
                if active_calls
                else "failed"
                if last_outcome is not None and last_outcome != "succeeded"
                else "idle"
                if row is not None
                else "not_started"
            )
            views.append(
                ModelRuntimeView(
                    component=component,
                    model=model,
                    status=status,
                    active_calls=active_calls,
                    calls_last_hour=int(row["calls"] or 0) if row is not None else 0,
                    succeeded_last_hour=(
                        int(row["succeeded"] or 0) if row is not None else 0
                    ),
                    failed_last_hour=(
                        int(row["failed"] or 0) if row is not None else 0
                    ),
                    input_tokens_last_hour=(
                        int(row["input_tokens"] or 0) if row is not None else 0
                    ),
                    output_tokens_last_hour=(
                        int(row["output_tokens"] or 0) if row is not None else 0
                    ),
                    average_latency_ms=(
                        float(row["average_latency_ms"])
                        if row is not None and row["average_latency_ms"] is not None
                        else None
                    ),
                    last_call_at=row["last_call_at"] if row is not None else None,
                    last_outcome=last_outcome,
                )
            )
        return tuple(views)

    @staticmethod
    def _stage_views(
        queue: RuntimeQueueStatus,
        workers: tuple[WorkerRuntimeView, ...],
        sources: tuple[SourceRuntimeView, ...],
        models: tuple[ModelRuntimeView, ...],
        counts: asyncpg.Record,
    ) -> tuple[PipelineStageRuntimeView, ...]:
        worker = {item.worker_type: item for item in workers}
        model = {item.component: item for item in models}
        source_active = sum(item.status == "working" for item in sources)
        source_failed = sum(item.status == "failed" for item in sources)
        source_attention = sum(item.status == "attention" for item in sources)
        source_not_started = sum(item.status == "not_started" for item in sources)
        source_complete = sum(item.status == "complete" for item in sources)
        source_status = (
            "failed"
            if source_failed
            else "working"
            if source_active
            else "attention"
            if source_attention
            else "complete"
            if sources and source_complete == len(sources)
            else "waiting"
        )
        source_last = max(
            (
                timestamp
                for item in sources
                if (timestamp := item.finished_at or item.started_at) is not None
            ),
            default=None,
        )

        ingest_pending = int(counts["ingest_pending"] or 0)
        ingest_completed = int(counts["ingest_completed"] or 0)
        ingest_failed = int(counts["ingest_failed"] or 0)
        ingest_status = (
            "failed"
            if ingest_failed
            else "working"
            if ingest_pending
            else "complete"
            if ingest_completed
            else "waiting"
        )

        document_active = int(queue.document_jobs.get("running", 0))
        document_pending = sum(
            queue.document_jobs.get(state, 0)
            for state in ("queued", "retryable_failed")
        )
        document_blocked = int(queue.document_jobs.get("held", 0))
        document_failed = int(queue.document_jobs.get("terminal_failed", 0))
        document_completed = int(queue.document_jobs.get("complete", 0))
        projection_status = (
            "failed"
            if document_failed
            else "working"
            if document_active
            else "attention"
            if document_blocked
            else "waiting"
            if document_pending
            else "complete"
            if document_completed
            else "waiting"
        )

        embedding = model.get("embedding")
        projection_worker = worker.get("projection")
        embedding_active = embedding.active_calls if embedding is not None else 0
        worker_indexing_active = int(
            projection_worker is not None
            and projection_worker.status in {"starting", "working"}
        )
        indexing_active = max(embedding_active, worker_indexing_active)
        recent_embedding_failures = (
            embedding.failed_last_hour if embedding is not None else 0
        )
        indexing_failed = int(
            queue.unindexed_document_versions > 0
            and projection_worker is not None
            and projection_worker.status == "failed"
        )
        indexing_status = (
            "working"
            if indexing_active
            else "failed"
            if indexing_failed
            else "complete"
            if queue.retrieval_index_status == "ready"
            and queue.unindexed_document_versions == 0
            else "waiting"
        )

        analysis_active = int(queue.analysis_jobs.get("running", 0))
        analysis_pending = sum(
            queue.analysis_jobs.get(state, 0)
            for state in ("queued", "deferred", "retryable_failed")
        )
        analysis_failed = int(queue.analysis_jobs.get("terminal_failed", 0))
        analysis_completed = sum(
            queue.analysis_jobs.get(state, 0) for state in ("complete", "partial", "held")
        )
        analysis_status = (
            "failed"
            if analysis_failed
            else "working"
            if analysis_active
            else "waiting"
            if analysis_pending
            else "complete"
            if analysis_completed
            else "waiting"
        )

        detector_active = int(counts["detector_active"] or 0)
        detector_completed = int(counts["detector_completed"] or 0)
        detector_failed = int(counts["detector_failed"] or 0)
        detector_attention = int(counts["detector_attention"] or 0)
        detector_status = (
            "failed"
            if detector_failed
            else "working"
            if detector_active or analysis_active
            else "attention"
            if detector_attention
            else "complete"
            if detector_completed
            else "waiting"
        )

        agent_components = tuple(
            item for item in models if item.component in {"research", "reranker", "entailment"}
        )
        research_active = sum(item.active_calls for item in agent_components)
        research_completed = int(counts["research_completed"] or 0)
        research_failed = int(counts["research_failed"] or 0)
        research_status = (
            "failed"
            if research_failed
            else "working"
            if research_active
            else "complete"
            if research_completed
            else "waiting"
        )

        validation_completed = int(counts["validation_completed"] or 0)
        validation_rejected = int(counts["validation_rejected"] or 0)
        published_results = int(counts["published_results"] or 0)
        publication_status = (
            "working"
            if analysis_active
            else "complete"
            if counts["publication_last"] is not None or validation_completed
            else "waiting"
        )
        extraction_model = model.get("extract")
        extraction_active = (
            extraction_model.active_calls if extraction_model is not None else 0
        )
        search_active = int(queue.search_jobs.get("running", 0))
        search_pending = sum(
            queue.search_jobs.get(state, 0) for state in ("queued", "retryable_failed")
        )
        search_held = int(queue.search_jobs.get("held", 0))
        search_failed = int(queue.search_jobs.get("terminal_failed", 0))
        search_completed = int(queue.search_jobs.get("complete", 0))
        search_status = (
            "failed"
            if search_failed
            else "working"
            if search_active
            else "attention"
            if search_held
            else "waiting"
            if search_pending
            else "complete"
            if search_completed
            else "waiting"
        )
        delivery_active = int(queue.deliveries.get("sending", 0))
        delivery_pending = sum(
            queue.deliveries.get(state, 0) for state in ("queued", "retryable_failed")
        )
        delivery_failed = sum(
            queue.deliveries.get(state, 0)
            for state in ("permanent_failed", "acceptance_unknown")
        )
        delivery_completed = sum(
            queue.deliveries.get(state, 0)
            for state in ("accepted", "observed_delivered", "suppressed")
        )
        delivery_status = (
            "failed"
            if delivery_failed
            else "working"
            if delivery_active
            else "waiting"
            if delivery_pending
            else "complete"
            if delivery_completed
            else "waiting"
        )
        return (
            PipelineStageRuntimeView(
                stage="source",
                label="1. Live source acquisition",
                status=source_status,
                detail=(
                    f"{source_complete}/{len(sources)} configured sources passed their latest "
                    "coverage check."
                ),
                active=source_active,
                pending=source_attention + source_not_started,
                completed=source_complete,
                failed=source_failed,
                last_activity_at=source_last,
            ),
            PipelineStageRuntimeView(
                stage="ingestion",
                label="2. Archive and canonical ingest",
                status=ingest_status,
                detail="Raw assets are archived and converted into immutable document versions.",
                pending=ingest_pending,
                completed=ingest_completed,
                failed=ingest_failed,
                last_activity_at=counts["ingest_last"],
            ),
            PipelineStageRuntimeView(
                stage="projection",
                label="3. Extraction and projection",
                status="working" if extraction_active else projection_status,
                detail="Documents are extracted, entity-linked, and projected into governed state.",
                active=max(document_active, extraction_active),
                pending=document_pending + document_blocked,
                completed=document_completed,
                failed=document_failed,
                last_activity_at=(
                    worker["projection"].heartbeat_at
                    if worker.get("projection") is not None
                    else None
                ),
            ),
            PipelineStageRuntimeView(
                stage="indexing",
                label="4. Chunking and retrieval index",
                status=indexing_status,
                detail=(
                    f"{queue.indexed_document_versions} indexed; "
                    f"{queue.unindexed_document_versions} still missing."
                    + (
                        f" {recent_embedding_failures} embedding attempt(s) "
                        "failed in the last hour."
                        if recent_embedding_failures
                        else ""
                    )
                ),
                active=indexing_active,
                pending=queue.unindexed_document_versions,
                completed=queue.indexed_document_versions,
                failed=indexing_failed,
                last_activity_at=max(
                    (
                        timestamp
                        for timestamp in (
                            embedding.last_call_at if embedding is not None else None,
                            (
                                projection_worker.heartbeat_at
                                if projection_worker is not None
                                else None
                            ),
                        )
                        if timestamp is not None
                    ),
                    default=None,
                ),
            ),
            PipelineStageRuntimeView(
                stage="analysis",
                label="5. Analysis orchestration",
                status=analysis_status,
                detail="The durable job freezes coverage, policy, model, and index inputs.",
                active=analysis_active,
                pending=analysis_pending,
                completed=analysis_completed,
                failed=analysis_failed,
                last_activity_at=counts["analysis_last"],
            ),
            PipelineStageRuntimeView(
                stage="detection",
                label="6. Signal detection",
                status=detector_status,
                detail="Registered financial patterns identify candidates worth researching.",
                active=detector_active,
                pending=detector_attention,
                completed=detector_completed,
                failed=detector_failed,
                last_activity_at=counts["detector_last"],
            ),
            PipelineStageRuntimeView(
                stage="research",
                label="7. Agent research and model reasoning",
                status=research_status,
                detail=(
                    "Hybrid retrieval, contradiction search, reranking, reasoning, and "
                    "entailment run here."
                ),
                active=research_active,
                completed=research_completed,
                failed=research_failed,
                last_activity_at=max(
                    (
                        item.last_call_at
                        for item in agent_components
                        if item.last_call_at is not None
                    ),
                    default=counts["research_last"],
                ),
            ),
            PipelineStageRuntimeView(
                stage="publication",
                label="8. Validation and publication",
                status=publication_status,
                detail=(
                    f"{validation_completed} claim decisions and {published_results} result "
                    "versions recorded in the last 24 hours; "
                    f"{validation_rejected} claims were rejected by validation."
                ),
                active=analysis_active,
                completed=validation_completed + published_results,
                last_activity_at=counts["publication_last"],
            ),
            PipelineStageRuntimeView(
                stage="search",
                label="9. On-demand analyst search",
                status=search_status,
                detail="Interactive questions run as durable governed search jobs.",
                active=search_active,
                pending=search_pending + search_held,
                completed=search_completed,
                failed=search_failed,
                last_activity_at=counts["search_last"],
            ),
            PipelineStageRuntimeView(
                stage="delivery",
                label="10. Digest delivery",
                status=delivery_status,
                detail="Scheduled results are assembled and sent through the configured channel.",
                active=delivery_active,
                pending=delivery_pending,
                completed=delivery_completed,
                failed=delivery_failed,
                last_activity_at=counts["delivery_last"],
            ),
        )

    @staticmethod
    def _overall_status(
        workers: tuple[WorkerRuntimeView, ...],
        stages: tuple[PipelineStageRuntimeView, ...],
    ) -> str:
        if any(item.status == "failed" or item.failed > 0 for item in stages):
            return "failed"
        if any(item.status == "failed" for item in workers):
            return "failed"
        core_workers = {
            item.worker_type: item.status
            for item in workers
            if item.worker_type in {"source", "projection", "analysis"}
        }
        if any(status in {"offline", "failed", "stopped"} for status in core_workers.values()):
            return "attention"
        if any(item.status == "working" for item in stages):
            return "working"
        if any(item.status in {"attention", "waiting"} for item in stages):
            return "attention"
        return "healthy"

    async def _recent_events(self, limit: int) -> tuple[RuntimeEventView, ...]:
        rows = await self._pool.fetch(
            """
            WITH events AS (
              SELECT 'runtime:' || event_id::text AS event_id,
                     occurred_at,
                     CASE
                       WHEN category='model' AND component='extract' THEN 'projection'
                       WHEN category='model' AND component='embedding' THEN 'indexing'
                       WHEN category='model' THEN 'research'
                       ELSE component
                     END AS stage,
                     operation, status, message, run_id, worker_id,
                     duration_ms, safe_error_summary
              FROM runtime_event_v1
              WHERE occurred_at >= now() - interval '7 days'

              UNION ALL

              SELECT 'source:' || observation_id::text, finished_at, 'source',
                     source_id,
                     CASE WHEN health='failed' THEN 'failed'
                          WHEN health='degraded' THEN 'attention'
                          ELSE 'succeeded' END,
                     'Source poll finished: ' || discovered_count::text ||
                     ' discovered, ' || committed_count::text || ' committed, ' ||
                     quarantine_count::text || ' quarantined.',
                     run_id::text, NULL::text,
                     EXTRACT(EPOCH FROM (finished_at-started_at))*1000.0,
                     CASE WHEN health='failed' THEN COALESCE(
                              error_message, error_type, 'source failure'
                          )
                          ELSE NULL END
              FROM source_observation_v2
              WHERE finished_at >= now() - interval '7 days'

              UNION ALL

              SELECT 'ingest:' || transition.transition_id::text,
                     transition.occurred_at, 'ingestion',
                     'document ingest',
                     CASE WHEN transition.to_status='quarantined' THEN 'failed'
                          WHEN transition.to_status IN ('committed','not_novel')
                          THEN 'succeeded' ELSE 'working' END,
                     'Document ingest moved to ' || transition.to_status || '.',
                     job.run_id::text, NULL::text, NULL::double precision,
                     CASE WHEN transition.to_status='quarantined'
                          THEN 'Document was quarantined.' ELSE NULL END
              FROM ingest_job_transition_v2 transition
              JOIN ingest_job_v2 job USING (job_id)
              WHERE transition.occurred_at >= now() - interval '7 days'

              UNION ALL

              SELECT 'document:' || document_version_id::text,
                     updated_at, 'projection', 'document processing',
                     CASE WHEN state='complete' THEN 'succeeded'
                          WHEN state IN ('terminal_failed','retryable_failed','held')
                          THEN 'failed' ELSE 'working' END,
                     'Document processing is ' || state || '.',
                     NULL::text, lease_owner, NULL::double precision,
                     safe_error_summary
              FROM document_processing_job_v4
              WHERE updated_at >= now() - interval '7 days'

              UNION ALL

              SELECT 'analysis:' || transition_id,
                     occurred_at, 'analysis', 'analysis job',
                     CASE WHEN to_state IN ('complete','partial') THEN 'succeeded'
                          WHEN to_state IN ('terminal_failed','retryable_failed')
                          THEN 'failed'
                          WHEN to_state='held' THEN 'refused'
                          WHEN to_state IN ('queued','running') THEN 'working'
                          ELSE 'attention' END,
                     'Analysis job moved to ' || to_state || '.',
                     job.run_id, transition.worker_id, NULL::double precision,
                     transition.safe_detail
              FROM analysis_job_transition_v4 transition
              JOIN analysis_job_v4 job USING (job_id)
              WHERE transition.occurred_at >= now() - interval '7 days'

              UNION ALL

              SELECT 'analysis-run:' || transition_id,
                     occurred_at, 'analysis', 'analysis run',
                     CASE WHEN state IN ('supported','contradicted','abstained','published')
                          THEN 'succeeded'
                          WHEN state IN ('failed_retryable','failed_terminal') THEN 'failed'
                          WHEN state='held' THEN 'refused'
                          WHEN state IN ('queued','running') THEN 'working'
                          ELSE 'attention' END,
                     'Analysis run moved to ' || state || '.',
                     run_id, NULL::text, NULL::double precision,
                     safe_error_summary
              FROM analysis_run_transition_v3
              WHERE occurred_at >= now() - interval '7 days'

              UNION ALL

              SELECT 'detector:' || execution_id,
                     COALESCE(finished_at, started_at), 'detection', pattern_name,
                     CASE WHEN state LIKE 'failed%' THEN 'failed'
                          WHEN finished_at IS NULL THEN 'working'
                          WHEN state='completed' THEN 'succeeded'
                          ELSE 'attention' END,
                     'Detector ' || pattern_name || ' is ' || state || '.',
                     scope.run_id, NULL::text,
                     CASE WHEN finished_at IS NULL THEN NULL::double precision
                          ELSE EXTRACT(EPOCH FROM (finished_at-started_at))*1000.0 END,
                     NULL::text
              FROM detector_execution_v3 detector
              JOIN analysis_scope_job_v3 scope USING (job_id)
              WHERE detector.started_at >= now() - interval '7 days'

              UNION ALL

              SELECT 'agent:' || step.step_id,
                     step.finished_at, 'research', step.operation,
                     CASE WHEN step.status IN ('succeeded','skipped_duplicate')
                          THEN 'succeeded' ELSE 'failed' END,
                     'Agent step ' || step.operation || ' is ' || step.status || '.',
                     investigation.run_id, NULL::text, step.duration_ms,
                     step.safe_error_summary
              FROM investigation_step_v3 step
              JOIN investigation_v3 investigation USING (investigation_id)
              WHERE step.finished_at >= now() - interval '7 days'

              UNION ALL

              SELECT 'investigation:' || transition.transition_id,
                     transition.occurred_at, 'research', 'investigation',
                     CASE WHEN transition.state IN (
                                    'supported','contradicted','abstained','published'
                          ) THEN 'succeeded'
                          WHEN transition.state IN ('failed_retryable','failed_terminal')
                          THEN 'failed'
                          WHEN transition.state='held' THEN 'refused'
                          WHEN transition.state IN ('queued','running') THEN 'working'
                          ELSE 'attention' END,
                     'Investigation moved to ' || transition.state ||
                     COALESCE(' (' || transition.stop_reason || ').', '.'),
                     investigation.run_id, NULL::text, NULL::double precision,
                     CASE WHEN transition.state IN ('failed_retryable','failed_terminal')
                          THEN COALESCE(transition.stop_reason, 'Investigation failed.')
                          ELSE NULL END
              FROM investigation_transition_v3 transition
              JOIN investigation_v3 investigation USING (investigation_id)
              WHERE transition.occurred_at >= now() - interval '7 days'

              UNION ALL

              SELECT 'search:' || step_id,
                     occurred_at, 'search', operation,
                     CASE WHEN status IN ('succeeded','complete','completed')
                          THEN 'succeeded' ELSE 'failed' END,
                     'Search step ' || operation || ' is ' || status || '.',
                     search_id, NULL::text, NULL::double precision, NULL::text
              FROM search_step_v4
              WHERE occurred_at >= now() - interval '7 days'

              UNION ALL

              SELECT 'dead:' || dead_letter_id,
                     quarantined_at, 'outbox', event.event_type, 'failed',
                     'Outbox event moved to the dead-letter queue.',
                     event.correlation_id::text, NULL::text,
                     NULL::double precision, dead.safe_error_summary
              FROM outbox_dead_letter_v3 dead
              JOIN transactional_outbox event USING (event_id)
              WHERE dead.quarantined_at >= now() - interval '7 days'

              UNION ALL

              SELECT 'delivery:' || transition_id,
                     occurred_at, 'delivery', 'digest delivery',
                     CASE WHEN state IN ('accepted','observed_delivered','suppressed')
                          THEN 'succeeded'
                          WHEN state IN ('retryable_failed','permanent_failed',
                                         'acceptance_unknown')
                          THEN 'failed' ELSE 'working' END,
                     'Delivery moved to ' || state || '.',
                     attempt_id, NULL::text, NULL::double precision, safe_detail
              FROM delivery_transition_v4
              WHERE occurred_at >= now() - interval '7 days'
            )
            SELECT * FROM events
            ORDER BY occurred_at DESC, event_id DESC
            LIMIT $1
            """,
            limit,
        )
        return tuple(
            RuntimeEventView(
                event_id=str(row["event_id"]),
                occurred_at=row["occurred_at"],
                stage=str(row["stage"]),
                operation=str(row["operation"]),
                status=str(row["status"]),
                message=str(row["message"]),
                run_id=str(row["run_id"]) if row["run_id"] is not None else None,
                worker_id=(
                    str(row["worker_id"]) if row["worker_id"] is not None else None
                ),
                duration_ms=(
                    float(row["duration_ms"])
                    if row["duration_ms"] is not None
                    else None
                ),
                safe_error_summary=(
                    str(row["safe_error_summary"])
                    if row["safe_error_summary"] is not None
                    else None
                ),
            )
            for row in rows
        )


async def _state_counts(
    pool: asyncpg.Pool,
    tables: tuple[tuple[str, str], ...],
) -> list[dict[str, int]]:
    # Table and column names are a closed constant set above, never user input.
    results: list[dict[str, int]] = []
    for table, column in tables:
        rows = await pool.fetch(
            f"SELECT {column}, count(*) AS total FROM {table} GROUP BY {column}"  # noqa: S608
        )
        results.append({str(row[column]): int(row["total"]) for row in rows})
    return results


async def _latest_analysis_state_counts(pool: asyncpg.Pool) -> dict[str, int]:
    rows = await pool.fetch(
        """
        WITH latest AS (
          SELECT DISTINCT ON (authorization_scope, topic_ids) state
          FROM analysis_job_v4
          ORDER BY authorization_scope, topic_ids, requested_at DESC, job_id DESC
        )
        SELECT state, count(*) AS total FROM latest GROUP BY state
        """
    )
    return {str(row["state"]): int(row["total"]) for row in rows}


async def _latest_search_state_counts(pool: asyncpg.Pool) -> dict[str, int]:
    rows = await pool.fetch(
        """
        WITH latest AS (
          SELECT DISTINCT ON (
              principal_snapshot->>'principal_id', authorization_scope,
              plan::text
          ) state
          FROM search_job_v4
          ORDER BY principal_snapshot->>'principal_id', authorization_scope,
                   plan::text, requested_at DESC, search_id DESC
        )
        SELECT state, count(*) AS total FROM latest GROUP BY state
        """
    )
    return {str(row["state"]): int(row["total"]) for row in rows}


__all__ = [
    "DeadLetterView",
    "ModelRuntimeView",
    "OperatorService",
    "PipelineStageRuntimeView",
    "ProjectionFailureView",
    "RuntimeDashboardView",
    "RuntimeEventView",
    "RuntimeQueueStatus",
    "SourceRuntimeView",
    "WorkerRuntimeView",
]
