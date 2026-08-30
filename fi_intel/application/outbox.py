"""Bounded idempotent transactional-outbox dispatch."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from fi_intel.ledger.models import OutboxEvent
from fi_intel.ledger.repository import IntelligenceLedger
from fi_intel.logging import safe_error_summary

OutboxHandler = Callable[[OutboxEvent], Awaitable[None]]


class HandlerCheckpointStore(Protocol):
    async def completed(self, handler_name: str, event_id: UUID, payload_digest: str) -> bool: ...

    async def record(
        self,
        handler_name: str,
        event_id: UUID,
        payload_digest: str,
        completed_at: datetime,
    ) -> None: ...


class InMemoryHandlerCheckpointStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, UUID], str] = {}

    async def completed(self, handler_name: str, event_id: UUID, payload_digest: str) -> bool:
        existing = self._items.get((handler_name, event_id))
        if existing is not None and existing != payload_digest:
            raise RuntimeError("outbox checkpoint payload digest conflicts with stored content")
        return existing == payload_digest

    async def record(
        self,
        handler_name: str,
        event_id: UUID,
        payload_digest: str,
        completed_at: datetime,
    ) -> None:
        del completed_at
        key = (handler_name, event_id)
        existing = self._items.get(key)
        if existing is not None and existing != payload_digest:
            raise RuntimeError("outbox checkpoint payload digest conflicts with stored content")
        self._items.setdefault(key, payload_digest)


class PostgresHandlerCheckpointStore:
    """Durable handler checkpoint store targeting migration 0015."""

    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def completed(self, handler_name: str, event_id: UUID, payload_digest: str) -> bool:
        pool = await self._get_pool()
        existing = await pool.fetchval(
            """
            SELECT payload_digest FROM outbox_handler_checkpoint_v3
            WHERE handler_name = $1 AND event_id = $2
            """,
            handler_name,
            event_id,
        )
        stored_digest = str(existing) if existing is not None else None
        if stored_digest is not None and stored_digest != payload_digest:
            raise RuntimeError("outbox checkpoint payload digest conflicts with stored content")
        return stored_digest == payload_digest

    async def record(
        self,
        handler_name: str,
        event_id: UUID,
        payload_digest: str,
        completed_at: datetime,
    ) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO outbox_handler_checkpoint_v3 (
                handler_name, event_id, payload_digest, completed_at
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (handler_name, event_id) DO NOTHING
            """,
            handler_name,
            event_id,
            payload_digest,
            completed_at,
        )
        if not await self.completed(handler_name, event_id, payload_digest):
            raise RuntimeError("outbox checkpoint was not persisted")

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


class DeadLetter(BaseModel):
    model_config = ConfigDict(frozen=True)

    dead_letter_id: str
    event_id: UUID
    event_type: str
    retryable: bool
    attempt_count: int = Field(gt=0)
    safe_error_summary: str
    payload_digest: str
    quarantined_at: datetime


class DeadLetterSink(Protocol):
    async def record(self, item: DeadLetter) -> None: ...


class InMemoryDeadLetterSink:
    def __init__(self) -> None:
        self.items: dict[str, DeadLetter] = {}

    async def record(self, item: DeadLetter) -> None:
        existing = self.items.get(item.dead_letter_id)
        if (
            existing is not None
            and existing.model_copy(update={"quarantined_at": item.quarantined_at}) != item
        ):
            raise RuntimeError("dead-letter identity has conflicting content")
        self.items.setdefault(item.dead_letter_id, item)


class PostgresDeadLetterSink:
    """Append-only outbox quarantine targeting migration 0017."""

    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=2)
        return self._pool

    async def record(self, item: DeadLetter) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO outbox_dead_letter_v3 (
                dead_letter_id, event_id, event_type, retryable, attempt_count,
                safe_error_summary, payload_digest, quarantined_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (event_id, payload_digest) DO NOTHING
            """,
            item.dead_letter_id,
            item.event_id,
            item.event_type,
            item.retryable,
            item.attempt_count,
            item.safe_error_summary,
            item.payload_digest,
            item.quarantined_at,
        )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


class OutboxLeaseStore(Protocol):
    async def claim(self, *, limit: int) -> list[OutboxEvent]: ...

    async def release(self, event_id: UUID, *, retry_after: datetime | None = None) -> None: ...


class PostgresOutboxLeaseStore:
    """Claim pending events with SKIP LOCKED and recover expired worker leases."""

    def __init__(
        self,
        dsn: str,
        *,
        pool: asyncpg.Pool | None = None,
        worker_id: str | None = None,
        lease_seconds: float = 60.0,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("outbox lease duration must be positive")
        self._dsn = dsn
        self._worker_id = worker_id or f"outbox-{uuid4()}"
        self._lease_seconds = lease_seconds
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=2)
        return self._pool

    async def claim(self, *, limit: int) -> list[OutboxEvent]:
        if limit < 1:
            raise ValueError("outbox claim limit must be positive")
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=self._lease_seconds)
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            WITH candidates AS (
                SELECT pending.event_id
                FROM transactional_outbox pending
                WHERE pending.published_at IS NULL
                  AND pending.next_attempt_at <= $1
                  AND (
                      pending.lease_expires_at IS NULL
                      OR pending.lease_expires_at <= $1
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM transactional_outbox earlier
                      WHERE earlier.aggregate_type = pending.aggregate_type
                        AND earlier.aggregate_id = pending.aggregate_id
                        AND earlier.aggregate_version < pending.aggregate_version
                        AND earlier.published_at IS NULL
                  )
                ORDER BY pending.occurred_at, pending.event_id
                FOR UPDATE SKIP LOCKED
                LIMIT $2
            )
            UPDATE transactional_outbox claimed
            SET lease_owner = $3, lease_expires_at = $4
            FROM candidates
            WHERE claimed.event_id = candidates.event_id
            RETURNING claimed.*
            """,
            now,
            limit,
            self._worker_id,
            lease_expires_at,
        )
        return [_event_from_row(row) for row in rows]

    async def release(self, event_id: UUID, *, retry_after: datetime | None = None) -> None:
        pool = await self._get_pool()
        result = await pool.execute(
            """
            UPDATE transactional_outbox
            SET lease_owner = NULL,
                lease_expires_at = NULL,
                next_attempt_at = COALESCE($3, next_attempt_at)
            WHERE event_id = $1 AND lease_owner = $2 AND published_at IS NULL
            """,
            event_id,
            self._worker_id,
            retry_after,
        )
        if result not in {"UPDATE 0", "UPDATE 1"}:
            raise RuntimeError("unexpected outbox lease release result")

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


@dataclass(slots=True)
class _Circuit:
    failures: int = 0
    open_until: datetime | None = None


class OutboxDispatchReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    attempted: int = 0
    published: int = 0
    quarantined: int = 0
    deferred_by_circuit: int = 0


class OutboxDispatcher:
    def __init__(
        self,
        ledger: IntelligenceLedger,
        handlers: dict[str, OutboxHandler],
        dead_letters: DeadLetterSink,
        *,
        checkpoints: HandlerCheckpointStore | None = None,
        max_attempts: int = 3,
        handler_timeout_seconds: float = 30.0,
        circuit_failure_threshold: int = 5,
        circuit_cooldown_seconds: float = 60.0,
        leases: OutboxLeaseStore | None = None,
    ) -> None:
        self._ledger = ledger
        self._handlers = handlers
        self._dead_letters = dead_letters
        self._checkpoints = checkpoints or InMemoryHandlerCheckpointStore()
        self._max_attempts = max_attempts
        self._handler_timeout_seconds = handler_timeout_seconds
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_cooldown_seconds = circuit_cooldown_seconds
        self._leases = leases
        self._circuits: dict[str, _Circuit] = {}

    async def dispatch_pending(self, *, limit: int = 100) -> OutboxDispatchReport:  # noqa: C901
        events = (
            await self._leases.claim(limit=limit)
            if self._leases is not None
            else await self._ledger.pending_events(limit=limit)
        )
        attempted = published = quarantined = deferred = 0
        for event in events:
            handler = self._handlers.get(event.event_type)
            if handler is None:
                await self._quarantine(
                    event,
                    1,
                    RuntimeError("no registered outbox handler"),
                    False,
                )
                await self._ledger.mark_event_published(event.event_id, datetime.now(UTC))
                quarantined += 1
                continue
            circuit = self._circuits.setdefault(event.event_type, _Circuit())
            payload_digest = _event_digest(event)
            if await self._checkpoints.completed(
                event.event_type,
                event.event_id,
                payload_digest,
            ):
                await self._ledger.mark_event_published(event.event_id, datetime.now(UTC))
                published += 1
                continue
            now = datetime.now(UTC)
            if circuit.open_until is not None and circuit.open_until > now:
                if self._leases is not None:
                    await self._leases.release(event.event_id, retry_after=circuit.open_until)
                deferred += 1
                continue
            attempted += 1
            final_error: Exception | None = None
            attempts = 0
            for attempt in range(1, self._max_attempts + 1):
                attempts = attempt
                try:
                    async with asyncio.timeout(self._handler_timeout_seconds):
                        await handler(event)
                    completed_at = datetime.now(UTC)
                    await self._checkpoints.record(
                        event.event_type,
                        event.event_id,
                        payload_digest,
                        completed_at,
                    )
                    await self._ledger.mark_event_published(event.event_id, completed_at)
                    circuit.failures = 0
                    circuit.open_until = None
                    published += 1
                    final_error = None
                    break
                except Exception as exc:
                    final_error = exc
                    if isinstance(exc, (ValueError, TypeError)):
                        break
                    if attempt < self._max_attempts:
                        # Bounded deterministic jitter avoids synchronized retries.
                        jitter_ms = (
                            int.from_bytes(
                                hashlib.sha256(f"{event.event_id}:{attempt}".encode()).digest()[:2],
                                "big",
                            )
                            % 100
                        )
                        await asyncio.sleep((2 ** (attempt - 1) * 100 + jitter_ms) / 1_000)
            if final_error is not None:
                circuit.failures += 1
                if circuit.failures >= self._circuit_failure_threshold:
                    circuit.open_until = datetime.now(UTC) + timedelta(
                        seconds=self._circuit_cooldown_seconds
                    )
                retryable = not isinstance(final_error, (ValueError, TypeError))
                await self._quarantine(event, attempts, final_error, retryable)
                # A bounded dispatch has exhausted its allowed attempts. A durable
                # dead letter becomes the terminal owner of the event so a bad
                # payload cannot remain in an infinite retry loop. Operator-led
                # replay must create a new, explicitly correlated event.
                await self._ledger.mark_event_published(event.event_id, datetime.now(UTC))
                quarantined += 1
        return OutboxDispatchReport(
            attempted=attempted,
            published=published,
            quarantined=quarantined,
            deferred_by_circuit=deferred,
        )

    async def _quarantine(
        self,
        event: OutboxEvent,
        attempts: int,
        error: Exception,
        retryable: bool,
    ) -> None:
        payload_digest = _event_digest(event)
        dead_letter_id = hashlib.sha256(f"{event.event_id}|{payload_digest}".encode()).hexdigest()
        await self._dead_letters.record(
            DeadLetter(
                dead_letter_id=dead_letter_id,
                event_id=event.event_id,
                event_type=event.event_type,
                retryable=retryable,
                attempt_count=max(1, attempts),
                safe_error_summary=safe_error_summary(error),
                payload_digest=payload_digest,
                quarantined_at=datetime.now(UTC),
            )
        )


def _event_digest(event: OutboxEvent) -> str:
    return hashlib.sha256(event.model_dump_json(exclude_none=True).encode()).hexdigest()


def _event_from_row(row: asyncpg.Record) -> OutboxEvent:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return OutboxEvent(
        event_id=row["event_id"],
        event_type=row["event_type"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        aggregate_version=row["aggregate_version"],
        occurred_at=row["occurred_at"],
        correlation_id=row["correlation_id"],
        causation_id=row["causation_id"],
        policy_id=row["policy_id"],
        payload=payload,
    )


__all__ = [
    "DeadLetter",
    "DeadLetterSink",
    "HandlerCheckpointStore",
    "InMemoryHandlerCheckpointStore",
    "InMemoryDeadLetterSink",
    "OutboxLeaseStore",
    "OutboxDispatchReport",
    "OutboxDispatcher",
    "OutboxHandler",
    "PostgresHandlerCheckpointStore",
    "PostgresDeadLetterSink",
    "PostgresOutboxLeaseStore",
]
