"""Replayable raw-to-ledger ingestion application service."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from fi_intel.application.control import (
    TERMINAL_JOB_STATUSES,
    IngestionControlStore,
    IngestJob,
    IngestRun,
    JobStatus,
    QuarantineRecord,
    RunStatus,
    SourceWatermark,
)
from fi_intel.application.raw import RawArchive, RawSourceEnvelope
from fi_intel.ledger.models import (
    AccessPolicy,
    DocumentIdentity,
    DocumentVersion,
    OutboxEvent,
    RawAsset,
    document_identity_id,
    document_version_id,
    outbox_event_id,
)
from fi_intel.ledger.repository import IntelligenceLedger
from fi_intel.logging import get_logger, safe_error_summary
from fi_intel.sources.canonical import CanonicalDocument, document_text

_APPLICATION_NAMESPACE = UUID("7038e023-3145-5ff3-962c-bbee110b6bd0")


def ingest_job_id(run_id: UUID, raw_asset_id: UUID) -> UUID:
    return uuid5(_APPLICATION_NAMESPACE, f"job:{run_id}:{raw_asset_id}")


class WatermarkToken(BaseModel):
    """Adapter-owned source progress, independent of document novelty."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    partition_key: str = "default"
    position: str = Field(min_length=1)
    sequence_number: int = Field(ge=0)
    observed_at: AwareDatetime


@runtime_checkable
class Canonicalizer(Protocol):
    async def canonicalize(self, envelope: RawSourceEnvelope) -> CanonicalDocument: ...


class IngestionDisposition(StrEnum):
    COMMITTED = "committed"
    NOT_NOVEL = "not_novel"
    QUARANTINED = "quarantined"


class IngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    job_id: UUID
    raw_asset_id: UUID
    disposition: IngestionDisposition
    document_version_id: UUID | None = None
    quarantine_id: UUID | None = None


class ReplayableIngestionService:
    """Coordinates durable raw capture, canonicalization, and ledger commits."""

    def __init__(
        self,
        archive: RawArchive,
        canonicalizer: Canonicalizer,
        ledger: IntelligenceLedger,
        control: IngestionControlStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._archive = archive
        self._canonicalizer = canonicalizer
        self._ledger = ledger
        self._control = control
        self._clock = clock or (lambda: datetime.now(UTC))
        self._log = get_logger(component="replayable-ingestion")

    async def begin_run(
        self,
        *,
        source_id: str,
        access_policy: AccessPolicy,
        requested_by: str,
        run_id: UUID | None = None,
    ) -> IngestRun:
        await self._ledger.register_policy(access_policy)
        run = IngestRun(
            run_id=run_id or uuid4(),
            source_id=source_id,
            status=RunStatus.RUNNING,
            requested_by=requested_by,
            started_at=self._now(),
            policy_id=access_policy.policy_id,
        )
        await self._control.create_run(run)
        return run

    async def finish_run(self, run_id: UUID, *, had_quarantine: bool = False) -> None:
        status = RunStatus.COMPLETED_WITH_ERRORS if had_quarantine else RunStatus.COMPLETED
        await self._control.finish_run(run_id, status, self._now())

    async def ingest(
        self,
        run_id: UUID,
        envelope: RawSourceEnvelope,
        watermark: WatermarkToken,
        *,
        attempt: int = 1,
    ) -> IngestionResult:
        """Process or resume one source revision.

        Every terminal result advances the supplied watermark, including
        non-novel and quarantined items. A retry in the same run returns its
        prior terminal result; replay into a new run reuses the archived bytes.
        """
        await self._ledger.register_policy(envelope.access_policy)
        job_id = ingest_job_id(run_id, envelope.raw_asset_id)
        job = await self._control.load_job(job_id)
        if job is None:
            started_at = self._now()
            job = IngestJob(
                job_id=job_id,
                run_id=run_id,
                raw_asset_id=envelope.raw_asset_id,
                source_id=envelope.source_id,
                external_id=envelope.external_id,
                source_revision=envelope.source_revision,
                content_hash=envelope.content_hash,
                media_type=envelope.media_type,
                headers=envelope.headers,
                fetched_at=envelope.fetched_at,
                source_published_at=envelope.source_published_at,
                access_policy=envelope.access_policy,
                status=JobStatus.RECEIVED,
                attempt=attempt,
                started_at=started_at,
                updated_at=started_at,
            )
            await self._control.create_job(job)
        else:
            self._validate_replay_envelope(job, envelope)
        if job.status in TERMINAL_JOB_STATUSES:
            return await self._terminal_result(job)

        stage = "archive_raw"
        try:
            job = await self._ensure_raw_archived(job, envelope)
            stage = "commit_raw"
            await self._commit_raw_asset(job, envelope)
            stage = "canonicalize"
            canonical = await self._canonicalizer.canonicalize(envelope)
            self._validate_canonical(envelope, canonical)
            stage = "archive_normalized"
            normalized_uri = await self._archive_normalized(canonical)
            if job.status is JobStatus.RAW_ARCHIVED:
                job = await self._control.transition_job(
                    job.job_id,
                    JobStatus.RAW_ARCHIVED,
                    JobStatus.CANONICALIZED,
                    self._after(job.updated_at),
                    detail="canonical document validated",
                )
            stage = "commit_document"
            disposition, result_version_id = await self._commit_canonical(
                job, envelope, canonical, normalized_uri
            )
            terminal_status = (
                JobStatus.COMMITTED
                if disposition is IngestionDisposition.COMMITTED
                else JobStatus.NOT_NOVEL
            )
            source_watermark = self._watermark(job, watermark)
            job = await self._control.complete_job(
                job.job_id,
                JobStatus.CANONICALIZED,
                terminal_status,
                source_watermark,
                self._after(job.updated_at),
                result_version_id,
            )
            return IngestionResult(
                run_id=run_id,
                job_id=job.job_id,
                raw_asset_id=job.raw_asset_id,
                disposition=disposition,
                document_version_id=result_version_id,
            )
        except Exception as exc:
            return await self._quarantine(job.job_id, watermark, stage, exc)

    async def replay(
        self,
        prior_job_id: UUID,
        new_run_id: UUID,
        watermark: WatermarkToken,
    ) -> IngestionResult:
        """Replay archived bytes into a distinct run without refetching."""
        prior = await self._control.load_job(prior_job_id)
        if prior is None or prior.archive_uri is None:
            raise ValueError("replay requires a job with archived raw bytes")
        payload = await self._archive.get(prior.archive_uri)
        envelope = RawSourceEnvelope(
            source_id=prior.source_id,
            external_id=prior.external_id,
            source_revision=prior.source_revision,
            payload=payload,
            media_type=prior.media_type,
            headers=prior.headers,
            fetched_at=prior.fetched_at,
            source_published_at=prior.source_published_at,
            access_policy=prior.access_policy,
        )
        return await self.ingest(new_run_id, envelope, watermark, attempt=prior.attempt + 1)

    async def close(self) -> None:
        await self._archive.close()
        await self._ledger.close()
        await self._control.close()

    async def _ensure_raw_archived(self, job: IngestJob, envelope: RawSourceEnvelope) -> IngestJob:
        if job.status is not JobStatus.RECEIVED:
            return job
        archived = await self._archive.put_if_absent(
            key=envelope.archive_key,
            content=envelope.payload,
            content_hash=envelope.content_hash,
            media_type=envelope.media_type,
            archived_at=self._after(job.updated_at),
        )
        return await self._control.transition_job(
            job.job_id,
            JobStatus.RECEIVED,
            JobStatus.RAW_ARCHIVED,
            self._after(job.updated_at),
            archive_uri=archived.uri,
            detail="raw bytes durably archived",
        )

    async def _commit_raw_asset(self, job: IngestJob, envelope: RawSourceEnvelope) -> None:
        if job.archive_uri is None:
            raise ValueError("raw asset cannot be committed before archive")
        metadata: dict[str, JsonValue] = {
            "headers": [header.model_dump() for header in envelope.headers],
            "source_published_at": (
                envelope.source_published_at.isoformat()
                if envelope.source_published_at is not None
                else None
            ),
        }
        raw_asset = RawAsset(
            raw_asset_id=envelope.raw_asset_id,
            source_id=envelope.source_id,
            external_id=envelope.external_id,
            source_revision=envelope.source_revision,
            object_uri=job.archive_uri,
            content_hash=envelope.content_hash,
            media_type=envelope.media_type,
            fetched_at=envelope.fetched_at,
            policy_id=envelope.access_policy.policy_id,
            metadata=metadata,
        )
        event_type = "raw-asset.archived.v1"
        event = OutboxEvent(
            event_id=outbox_event_id(event_type, raw_asset.raw_asset_id, 1),
            event_type=event_type,
            aggregate_type="raw_asset",
            aggregate_id=raw_asset.raw_asset_id,
            aggregate_version=1,
            occurred_at=envelope.fetched_at,
            correlation_id=raw_asset.raw_asset_id,
            policy_id=raw_asset.policy_id,
            payload={
                "source_id": raw_asset.source_id,
                "external_id": raw_asset.external_id,
                "source_revision": raw_asset.source_revision,
                "content_hash": raw_asset.content_hash,
                "object_uri": raw_asset.object_uri,
            },
        )
        await self._ledger.commit_raw_asset(raw_asset, event)

    async def _archive_normalized(self, canonical: CanonicalDocument) -> str:
        content = document_text(canonical).encode()
        content_hash = hashlib.sha256(content).hexdigest()
        document_id = document_identity_id(canonical.source_id, canonical.doc_id)
        archived = await self._archive.put_if_absent(
            key=f"normalized/{document_id}/{content_hash}",
            content=content,
            content_hash=content_hash,
            media_type="text/plain; charset=utf-8",
            archived_at=self._now(),
        )
        return archived.uri

    async def _commit_canonical(
        self,
        job: IngestJob,
        envelope: RawSourceEnvelope,
        canonical: CanonicalDocument,
        normalized_uri: str,
    ) -> tuple[IngestionDisposition, UUID]:
        identity_id = document_identity_id(canonical.source_id, canonical.doc_id)
        head = await self._ledger.document_head(identity_id)
        normalized_hash = canonical.content_hash()
        if head is not None and head.normalized_text_hash == normalized_hash:
            return IngestionDisposition.NOT_NOVEL, head.document_version_id

        identity = await self._ledger.document_identity(identity_id)
        if identity is None:
            identity = DocumentIdentity(
                document_id=identity_id,
                source_id=canonical.source_id,
                external_id=canonical.doc_id,
                created_at=canonical.recorded_at,
            )
        version_number = 1 if head is None else head.version_number + 1
        version_id = document_version_id(
            identity.document_id, envelope.source_revision, normalized_hash
        )
        version = DocumentVersion(
            document_version_id=version_id,
            document_id=identity.document_id,
            raw_asset_id=envelope.raw_asset_id,
            version_number=version_number,
            source_revision=envelope.source_revision,
            normalized_object_uri=normalized_uri,
            normalized_text_hash=normalized_hash,
            title=canonical.title,
            language=canonical.language,
            document_class=canonical.document_class,
            published_at=canonical.published_at,
            recorded_at=canonical.recorded_at,
            parser_version="canonicalizer-v2",
            policy_id=envelope.access_policy.policy_id,
            supersedes_version_id=(head.document_version_id if head is not None else None),
        )
        raw_asset = RawAsset(
            raw_asset_id=envelope.raw_asset_id,
            source_id=envelope.source_id,
            external_id=envelope.external_id,
            source_revision=envelope.source_revision,
            object_uri=job.archive_uri or "",
            content_hash=envelope.content_hash,
            media_type=envelope.media_type,
            fetched_at=envelope.fetched_at,
            policy_id=envelope.access_policy.policy_id,
            metadata={
                "headers": [header.model_dump() for header in envelope.headers],
                "source_published_at": (
                    envelope.source_published_at.isoformat()
                    if envelope.source_published_at is not None
                    else None
                ),
            },
        )
        event_type = "document.versioned.v1"
        event = OutboxEvent(
            event_id=outbox_event_id(event_type, version.document_version_id, version_number),
            event_type=event_type,
            aggregate_type="document_version",
            aggregate_id=version.document_version_id,
            aggregate_version=version_number,
            occurred_at=canonical.recorded_at,
            correlation_id=envelope.raw_asset_id,
            causation_id=outbox_event_id("raw-asset.archived.v1", envelope.raw_asset_id, 1),
            policy_id=version.policy_id,
            payload={
                "document_id": str(identity.document_id),
                "document_version_id": str(version.document_version_id),
                "version_number": version.version_number,
                "supersedes_version_id": (
                    str(version.supersedes_version_id)
                    if version.supersedes_version_id is not None
                    else None
                ),
            },
        )
        await self._ledger.commit_document_version(raw_asset, identity, version, event)
        return IngestionDisposition.COMMITTED, version.document_version_id

    async def _quarantine(
        self,
        job_id: UUID,
        watermark: WatermarkToken,
        stage: str,
        error: Exception,
    ) -> IngestionResult:
        job = await self._control.load_job(job_id)
        if job is None:
            raise error
        if job.status in TERMINAL_JOB_STATUSES:
            return await self._terminal_result(job)
        recorded_at = self._after(job.updated_at)
        error_type = type(error).__name__
        self._log.error(
            "ingestion.job.quarantined",
            run_id=str(job.run_id),
            job_id=str(job.job_id),
            raw_asset_id=str(job.raw_asset_id),
            source_id=job.source_id,
            stage=stage,
            attempt=job.attempt,
            error_type=error_type,
            safe_error_summary=safe_error_summary(error),
            error_message=str(error),
        )
        quarantine_id = uuid5(
            _APPLICATION_NAMESPACE,
            f"quarantine:{job.job_id}:{job.attempt}:{stage}:{error_type}",
        )
        record = QuarantineRecord(
            quarantine_id=quarantine_id,
            job_id=job.job_id,
            stage=stage,
            error_type=error_type,
            message=str(error) or error_type,
            retryable=stage not in {"archive_raw", "canonicalize"},
            recorded_at=recorded_at,
        )
        await self._control.quarantine_job(
            job.job_id,
            job.status,
            record,
            self._watermark(job, watermark),
        )
        return IngestionResult(
            run_id=job.run_id,
            job_id=job.job_id,
            raw_asset_id=job.raw_asset_id,
            disposition=IngestionDisposition.QUARANTINED,
            quarantine_id=record.quarantine_id,
        )

    async def _terminal_result(self, job: IngestJob) -> IngestionResult:
        if job.status is JobStatus.QUARANTINED:
            records = await self._control.list_quarantine(job.job_id)
            quarantine_id = records[-1].quarantine_id if records else None
            return IngestionResult(
                run_id=job.run_id,
                job_id=job.job_id,
                raw_asset_id=job.raw_asset_id,
                disposition=IngestionDisposition.QUARANTINED,
                quarantine_id=quarantine_id,
            )
        disposition = (
            IngestionDisposition.COMMITTED
            if job.status is JobStatus.COMMITTED
            else IngestionDisposition.NOT_NOVEL
        )
        return IngestionResult(
            run_id=job.run_id,
            job_id=job.job_id,
            raw_asset_id=job.raw_asset_id,
            disposition=disposition,
            document_version_id=job.result_document_version_id,
        )

    @staticmethod
    def _watermark(job: IngestJob, token: WatermarkToken) -> SourceWatermark:
        return SourceWatermark(
            source_id=job.source_id,
            partition_key=token.partition_key,
            position=token.position,
            sequence_number=token.sequence_number,
            observed_at=token.observed_at,
            run_id=job.run_id,
            job_id=job.job_id,
        )

    @staticmethod
    def _validate_replay_envelope(job: IngestJob, envelope: RawSourceEnvelope) -> None:
        actual = (
            envelope.raw_asset_id,
            envelope.content_hash,
            envelope.media_type,
            envelope.access_policy.policy_id,
        )
        expected = (
            job.raw_asset_id,
            job.content_hash,
            job.media_type,
            job.access_policy.policy_id,
        )
        if actual != expected:
            raise ValueError("replay envelope conflicts with the recorded ingest job")

    @staticmethod
    def _validate_canonical(envelope: RawSourceEnvelope, canonical: CanonicalDocument) -> None:
        if canonical.source_id != envelope.source_id:
            raise ValueError("canonicalizer changed source_id")
        if canonical.doc_id != envelope.external_id:
            raise ValueError("canonicalizer changed external document identity")
        if canonical.barrier_side is not envelope.access_policy.barrier_side:
            raise ValueError("canonicalizer barrier differs from the raw access policy")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ingestion clock must return timezone-aware datetimes")
        return value

    def _after(self, previous: datetime) -> datetime:
        current = self._now()
        return current if current > previous else previous + timedelta(microseconds=1)
