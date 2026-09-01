"""Read-only diagnostics and explicit recovery operations."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import asyncpg
from pydantic import BaseModel, ConfigDict

from fi_intel.application.runtime_resources import RuntimeResources


class DeadLetterView(BaseModel):
    model_config = ConfigDict(frozen=True)

    dead_letter_id: str
    event_id: UUID
    event_type: str
    retryable: bool
    attempt_count: int
    safe_error_summary: str
    quarantined_at: datetime


class RuntimeQueueStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    analysis_jobs: dict[str, int]
    search_jobs: dict[str, int]
    document_jobs: dict[str, int]
    outbox_pending: int
    dead_letters: int
    deliveries: dict[str, int]


class OperatorService:
    def __init__(self, resources: RuntimeResources) -> None:
        self._resources = resources

    async def dead_letters(self, *, limit: int = 100) -> list[DeadLetterView]:
        if not 1 <= limit <= 1_000:
            raise ValueError("dead-letter limit must be between 1 and 1000")
        rows = await self._resources.postgres_pool.fetch(
            """
            SELECT * FROM outbox_dead_letter_v3
            ORDER BY quarantined_at DESC, dead_letter_id DESC LIMIT $1
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
            )
            for row in rows
        ]

    async def replay_dead_letter(self, dead_letter_id: str) -> UUID:
        """Create a correlated immutable replay event; never mutate history."""

        pool = self._resources.postgres_pool
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

        pool = self._resources.postgres_pool
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
        pool = self._resources.postgres_pool
        analysis, search, documents, deliveries = await _state_counts(
            pool,
            (
                ("analysis_job_v4", "state"),
                ("search_job_v4", "state"),
                ("document_processing_job_v4", "state"),
                ("delivery_attempt_v4", "state"),
            ),
        )
        pending = await pool.fetchval(
            "SELECT count(*) FROM transactional_outbox WHERE published_at IS NULL"
        )
        dead = await pool.fetchval("SELECT count(*) FROM outbox_dead_letter_v3")
        return RuntimeQueueStatus(
            analysis_jobs=analysis,
            search_jobs=search,
            document_jobs=documents,
            outbox_pending=int(pending),
            dead_letters=int(dead),
            deliveries=deliveries,
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


__all__ = ["DeadLetterView", "OperatorService", "RuntimeQueueStatus"]
