"""Run a registered raw source through the replayable v2 ingestion service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from fi_intel.application.ingestion import (
    IngestionDisposition,
    ReplayableIngestionService,
    WatermarkToken,
)
from fi_intel.ledger.models import AccessPolicy
from fi_intel.sources.acquisition import RawSourceAdapter
from fi_intel.sources.catalog import SourceRegistration
from fi_intel.sources.operations import (
    SourceObservation,
    SourceOperationsStore,
    assess_source_poll,
    failed_source_observation,
)


class SourceRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    observation: SourceObservation


class SourceIngestionCoordinator:
    """Small durable poll workflow; scheduling remains a deployment concern."""

    def __init__(
        self,
        registration: SourceRegistration,
        access_policy: AccessPolicy,
        adapter: RawSourceAdapter,
        ingestion: ReplayableIngestionService,
        operations: SourceOperationsStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not registration.enabled:
            raise ValueError(f"source {registration.source_id!r} is disabled")
        if registration.source_id != adapter.source_id:
            raise ValueError("source registration and adapter IDs differ")
        if registration.barrier_side is not access_policy.barrier_side:
            raise ValueError("source registration and access policy barriers differ")
        if not access_policy.allowed_entitlement_groups.issubset(
            registration.allowed_entitlement_groups
        ):
            raise ValueError("source access policy exceeds its registered audience")
        self._registration = registration
        self._policy = access_policy
        self._adapter = adapter
        self._ingestion = ingestion
        self._operations = operations
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(
        self, *, requested_by: str, run_id: UUID | None = None
    ) -> SourceRunResult:
        started_at = self._now()
        previous = await self._operations.load_state(self._registration.source_id)
        run = await self._ingestion.begin_run(
            source_id=self._registration.source_id,
            access_policy=self._policy,
            requested_by=requested_by,
            run_id=run_id,
        )
        try:
            poll = await self._adapter.poll(previous.cursor if previous else None)
            committed = 0
            not_novel = 0
            quarantined = 0
            for item in poll.items:
                result = await self._ingestion.ingest(
                    run.run_id,
                    item.envelope,
                    WatermarkToken(
                        partition_key=poll.partition_key,
                        position=item.cursor_position,
                        sequence_number=item.sequence_number,
                        observed_at=max(poll.polled_at, item.envelope.fetched_at),
                    ),
                )
                if result.disposition is IngestionDisposition.COMMITTED:
                    committed += 1
                elif result.disposition is IngestionDisposition.NOT_NOVEL:
                    not_novel += 1
                else:
                    quarantined += 1
        except Exception as exc:
            finished_at = self._after(started_at)
            observation, state = failed_source_observation(
                self._registration,
                run_id=run.run_id,
                policy_id=self._policy.policy_id,
                started_at=started_at,
                finished_at=finished_at,
                error=exc,
                previous=previous,
            )
            await self._operations.record(observation, state)
            await self._ingestion.finish_run(run.run_id, had_quarantine=True)
            raise

        finished_at = self._after(started_at)
        observation, state = assess_source_poll(
            self._registration,
            poll,
            run_id=run.run_id,
            policy_id=self._policy.policy_id,
            started_at=started_at,
            finished_at=finished_at,
            committed_count=committed,
            not_novel_count=not_novel,
            quarantine_count=quarantined,
        )
        await self._operations.record(observation, state)
        await self._ingestion.finish_run(
            run.run_id, had_quarantine=quarantined > 0
        )
        return SourceRunResult(run_id=run.run_id, observation=observation)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source coordinator clock must return an aware datetime")
        return value

    def _after(self, previous: datetime) -> datetime:
        current = self._now()
        return current if current > previous else previous + timedelta(microseconds=1)
