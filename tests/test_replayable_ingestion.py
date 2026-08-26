"""Service-free contracts for the replayable ingestion application path."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fi_intel.application.control import (
    InMemoryIngestionControlStore,
    JobStatus,
)
from fi_intel.application.ingestion import (
    IngestionDisposition,
    ReplayableIngestionService,
    WatermarkToken,
)
from fi_intel.application.prototype_compat import (
    PrototypeCanonicalizer,
    envelope_from_v1_canonical,
)
from fi_intel.application.raw import InMemoryRawArchive, RawSourceEnvelope
from fi_intel.ledger import AccessPolicy, InMemoryIntelligenceLedger
from fi_intel.ledger.models import document_identity_id
from fi_intel.sources.canonical import (
    BarrierSide,
    CanonicalDocument,
    DocumentClass,
)

NOW = datetime(2025, 4, 8, 9, tzinfo=UTC)


class TickingClock:
    def __init__(self) -> None:
        self._value = NOW + timedelta(days=1)

    def __call__(self) -> datetime:
        self._value += timedelta(seconds=1)
        return self._value


class ArchiveAwareCanonicalizer(PrototypeCanonicalizer):
    def __init__(self, archive: InMemoryRawArchive) -> None:
        self._archive = archive
        self.raw_was_archived = False

    async def canonicalize(self, envelope: RawSourceEnvelope) -> CanonicalDocument:
        self.raw_was_archived = self._archive.object_count() >= 1
        return await super().canonicalize(envelope)


class FailingCanonicalizer:
    async def canonicalize(self, envelope: RawSourceEnvelope) -> CanonicalDocument:
        raise ValueError("malformed source payload")


def _policy() -> AccessPolicy:
    return AccessPolicy(
        policy_id=uuid4(),
        barrier_side=BarrierSide.PUBLIC,
        allowed_entitlement_groups=frozenset({"fi_public"}),
        created_at=NOW,
    )


def _document(body: str, recorded_at: datetime = NOW) -> CanonicalDocument:
    return CanonicalDocument(
        doc_id="story-17",
        source_id="synthetic_wire",
        published_at=NOW - timedelta(minutes=5),
        recorded_at=recorded_at,
        title="Example Bank capital update",
        body=body,
        document_class=DocumentClass.NEWS_WIRE,
    )


def _watermark(position: str, sequence: int) -> WatermarkToken:
    return WatermarkToken(
        position=position,
        sequence_number=sequence,
        observed_at=NOW + timedelta(minutes=sequence),
    )


async def test_raw_first_corrections_and_non_novel_watermark_progress() -> None:
    policy = _policy()
    archive = InMemoryRawArchive()
    canonicalizer = ArchiveAwareCanonicalizer(archive)
    ledger = InMemoryIntelligenceLedger()
    control = InMemoryIngestionControlStore()
    service = ReplayableIngestionService(
        archive, canonicalizer, ledger, control, clock=TickingClock()
    )
    run = await service.begin_run(
        source_id="synthetic_wire", access_policy=policy, requested_by="test"
    )

    first_envelope = envelope_from_v1_canonical(
        _document("Example Bank raised USD 500 million."),
        source_revision="revision-1",
        access_policy=policy,
    )
    first = await service.ingest(run.run_id, first_envelope, _watermark("1", 1))
    assert first.disposition is IngestionDisposition.COMMITTED
    assert canonicalizer.raw_was_archived
    assert archive.object_count() == 2  # original bytes plus normalized text

    identity_id = document_identity_id("synthetic_wire", "story-17")
    head_one = await ledger.document_head(identity_id)
    assert head_one is not None and head_one.version_number == 1

    # Retrying the same item in the same run returns the terminal job and does
    # not create a new version, archive object, or outbox event.
    first_retry = await service.ingest(
        run.run_id, first_envelope, _watermark("1", 1)
    )
    assert first_retry == first
    assert archive.object_count() == 2

    correction_envelope = envelope_from_v1_canonical(
        _document(
            "Example Bank raised USD 750 million.",
            recorded_at=NOW + timedelta(hours=1),
        ),
        source_revision="revision-2",
        access_policy=policy,
    )
    correction = await service.ingest(
        run.run_id, correction_envelope, _watermark("2", 2)
    )
    assert correction.disposition is IngestionDisposition.COMMITTED
    head_two = await ledger.document_head(identity_id)
    assert head_two is not None and head_two.version_number == 2
    assert head_two.supersedes_version_id == head_one.document_version_id

    second_run = await service.begin_run(
        source_id="synthetic_wire", access_policy=policy, requested_by="test-replay"
    )
    repeated = await service.ingest(
        second_run.run_id, correction_envelope, _watermark("3", 3)
    )
    assert repeated.disposition is IngestionDisposition.NOT_NOVEL
    assert repeated.document_version_id == head_two.document_version_id
    assert (await ledger.document_head(identity_id)) == head_two

    # Novelty did not gate source progress.
    stored_watermark = await control.load_watermark("synthetic_wire")
    assert stored_watermark is not None
    assert stored_watermark.position == "3"
    assert stored_watermark.sequence_number == 3


async def test_quarantine_advances_watermark_and_replay_uses_archived_bytes() -> None:
    policy = _policy()
    archive = InMemoryRawArchive()
    ledger = InMemoryIntelligenceLedger()
    control = InMemoryIngestionControlStore()
    failing_service = ReplayableIngestionService(
        archive, FailingCanonicalizer(), ledger, control, clock=TickingClock()
    )
    failed_run = await failing_service.begin_run(
        source_id="synthetic_wire", access_policy=policy, requested_by="test"
    )
    envelope = envelope_from_v1_canonical(
        _document("Example Bank announced a transaction."),
        source_revision="revision-bad",
        access_policy=policy,
    )
    failed = await failing_service.ingest(
        failed_run.run_id, envelope, _watermark("bad-1", 1)
    )
    assert failed.disposition is IngestionDisposition.QUARANTINED
    assert failed.quarantine_id is not None
    failed_job = await control.load_job(failed.job_id)
    assert failed_job is not None
    assert failed_job.status is JobStatus.QUARANTINED
    assert failed_job.archive_uri is not None
    assert (await control.load_watermark("synthetic_wire")).position == "bad-1"  # type: ignore[union-attr]

    replay_service = ReplayableIngestionService(
        archive, PrototypeCanonicalizer(), ledger, control, clock=TickingClock()
    )
    replay_run = await replay_service.begin_run(
        source_id="synthetic_wire", access_policy=policy, requested_by="analyst"
    )
    replayed = await replay_service.replay(
        failed.job_id, replay_run.run_id, _watermark("replayed-2", 2)
    )
    assert replayed.disposition is IngestionDisposition.COMMITTED
    replayed_job = await control.load_job(replayed.job_id)
    assert replayed_job is not None and replayed_job.attempt == 2
    assert archive.object_count() == 2
