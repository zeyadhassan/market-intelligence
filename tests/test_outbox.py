"""Failure-injection tests for the bounded transactional-outbox worker."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fi_intel.application.outbox import (
    InMemoryDeadLetterSink,
    InMemoryHandlerCheckpointStore,
    OutboxDispatcher,
)
from fi_intel.ledger.models import OutboxEvent

NOW = datetime(2026, 8, 27, tzinfo=UTC)


class _Ledger:
    def __init__(self, events: list[OutboxEvent]) -> None:
        self.events = events
        self.published: dict[UUID, datetime] = {}

    async def pending_events(self, limit: int = 100) -> list[OutboxEvent]:
        return [event for event in self.events if event.event_id not in self.published][:limit]

    async def mark_event_published(self, event_id: UUID, published_at: datetime) -> None:
        self.published.setdefault(event_id, published_at)


def _event(event_type: str = "document.versioned.v1") -> OutboxEvent:
    return OutboxEvent(
        event_id=uuid4(),
        event_type=event_type,
        aggregate_type="document",
        aggregate_id=uuid4(),
        aggregate_version=1,
        occurred_at=NOW,
        correlation_id=uuid4(),
        policy_id=uuid4(),
        payload={"document_version_id": str(uuid4())},
    )


async def test_dispatch_retries_then_checkpoints_and_publishes_once() -> None:
    event = _event()
    ledger = _Ledger([event])
    dead_letters = InMemoryDeadLetterSink()
    checkpoints = InMemoryHandlerCheckpointStore()
    calls = 0

    async def handler(_: OutboxEvent) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary upstream outage")

    dispatcher = OutboxDispatcher(
        ledger,  # type: ignore[arg-type]
        {event.event_type: handler},
        dead_letters,
        checkpoints=checkpoints,
        max_attempts=2,
    )
    report = await dispatcher.dispatch_pending()

    assert report.published == 1
    assert report.quarantined == 0
    assert calls == 2
    assert event.event_id in ledger.published
    assert not dead_letters.items

    repeated = await dispatcher.dispatch_pending()
    assert repeated.attempted == 0
    assert calls == 2


async def test_exhausted_retryable_failure_is_terminally_dead_lettered() -> None:
    event = _event()
    ledger = _Ledger([event])
    dead_letters = InMemoryDeadLetterSink()

    async def handler(_: OutboxEvent) -> None:
        raise RuntimeError("password=must-not-appear-in-the-dead-letter")

    dispatcher = OutboxDispatcher(
        ledger,  # type: ignore[arg-type]
        {event.event_type: handler},
        dead_letters,
        max_attempts=1,
    )
    report = await dispatcher.dispatch_pending()

    assert report.quarantined == 1
    assert event.event_id in ledger.published
    (dead_letter,) = dead_letters.items.values()
    assert dead_letter.retryable is True
    assert dead_letter.attempt_count == 1
    assert "must-not-appear" not in dead_letter.safe_error_summary
    assert dead_letter.safe_error_summary.startswith("RuntimeError (message_sha256=")


async def test_missing_handler_is_quarantined_without_poisoning_queue() -> None:
    event = _event("unknown.event.v1")
    ledger = _Ledger([event])
    dead_letters = InMemoryDeadLetterSink()
    dispatcher = OutboxDispatcher(
        ledger,  # type: ignore[arg-type]
        {},
        dead_letters,
        max_attempts=1,
    )

    report = await dispatcher.dispatch_pending()

    assert report.quarantined == 1
    assert event.event_id in ledger.published
    (dead_letter,) = dead_letters.items.values()
    assert dead_letter.retryable is False
    assert dead_letter.attempt_count == 1
