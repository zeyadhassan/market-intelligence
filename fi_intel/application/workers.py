"""Independently restartable source and document/projection workers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict

from fi_intel.application.assertion_admission import LedgerAssertionAdmissionSink
from fi_intel.application.control import PostgresIngestionControlStore
from fi_intel.application.entity_projection import EntityReferenceProjection
from fi_intel.application.ingestion import ReplayableIngestionService
from fi_intel.application.outbox import (
    OutboxDispatcher,
    PostgresDeadLetterSink,
    PostgresHandlerCheckpointStore,
    PostgresOutboxLeaseStore,
)
from fi_intel.application.observability import RuntimeMonitor
from fi_intel.application.policies import (
    POLICY_NAMESPACE,
    public_source_policy,
    reference_source_policy,
)
from fi_intel.application.raw import RawHeader, RawSourceEnvelope
from fi_intel.application.runtime_resources import RuntimeResources
from fi_intel.application.source_ingestion import SourceIngestionCoordinator, SourceRunResult
from fi_intel.governance.model_usage import PostgresModelUsageLog
from fi_intel.governance.policy import PostgresEntitlementResolver
from fi_intel.governance.serving import ModelBundle
from fi_intel.graph.registry import PatternRegistry
from fi_intel.graph.signals import Signal
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ingest.extract_pipeline import ExtractionPipeline, PostgresProposedTypeSink
from fi_intel.ingest.resolve import EntityResolver
from fi_intel.ingest.resolve_store import PostgresResolutionStore
from fi_intel.ingest.store import PostgresDocumentStore
from fi_intel.ledger.models import OutboxEvent
from fi_intel.ledger.repository import PostgresIntelligenceLedger
from fi_intel.logging import get_logger, safe_console_error_message, safe_error_summary
from fi_intel.retrieval.entitlement import Principal, Side
from fi_intel.retrieval.store import PostgresCorpusStore
from fi_intel.sources.adapters.gcc_official import (
    GCC_OFFICIAL_SOURCES,
    GccOfficialCanonicalizer,
    GccOfficialSource,
    OfficialGccRawAdapter,
)
from fi_intel.sources.adapters.gleif import GleifDetailCanonicalizer, gleif_targeted_registered
from fi_intel.sources.adapters.government import GovernmentDetailCanonicalizer
from fi_intel.sources.base import FetchCursor
from fi_intel.sources.canonical import DocumentClass
from fi_intel.sources.catalog import production_source_catalog
from fi_intel.sources.operations import (
    PostgresSourceOperationsStore,
    SourceHealth,
    SourceOperationalState,
)

_SOURCE_FAILURE_RETRY_BASE_SECONDS = 60


def _source_next_eligible_at(
    state: SourceOperationalState,
    cadence_seconds: int,
) -> datetime:
    """Retry failed polls sooner, with backoff bounded by the normal cadence."""

    delay_seconds = cadence_seconds
    if state.consecutive_failures:
        exponent = min(state.consecutive_failures - 1, 30)
        delay_seconds = min(
            cadence_seconds,
            _SOURCE_FAILURE_RETRY_BASE_SECONDS * (2**exponent),
        )
    return state.updated_at + timedelta(seconds=delay_seconds)


class SourceWorkerReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    polled_source_ids: tuple[str, ...]
    skipped_source_ids: tuple[str, ...]
    failed_source_ids: tuple[str, ...]
    committed_document_versions: int


class ProjectionWorkerReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    attempted: int
    published: int
    quarantined: int
    deferred: int
    indexed_chunks: int


class CanonicalSourceWorker:
    """Poll registered sources and stop at the PostgreSQL/outbox boundary."""

    def __init__(self, resources: RuntimeResources) -> None:
        self._resources = resources
        self._settings = resources.settings
        self._log = get_logger(component="canonical-source-worker")

    async def run_once(  # noqa: C901 - bounded orchestration across registered sources
        self, *, force: bool = False
    ) -> SourceWorkerReport:
        settings = self._settings
        pool = self._resources.postgres_pool
        operations = PostgresSourceOperationsStore(settings.postgres_dsn, pool=pool)
        run_uuid = uuid4()
        polled: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        committed = 0
        semaphore = asyncio.Semaphore(settings.gcc_source_max_parallel_sources)
        configured_sources = settings.configured_coverage_source_ids
        active_sources = tuple(
            source
            for source in GCC_OFFICIAL_SOURCES
            if not configured_sources or source.source_id in configured_sources
        )
        self._log.info(
            "source.batch.started",
            run_id=str(run_uuid),
            force=force,
            source_count=len(active_sources),
            configured_source_filter=tuple(sorted(configured_sources)),
            max_parallel_sources=settings.gcc_source_max_parallel_sources,
            http_proxy_configured=bool(settings.source_http_proxy),
            https_proxy_configured=bool(settings.source_https_proxy),
            no_proxy_configured=bool(settings.source_no_proxy),
            tls_verify=settings.source_tls_verify,
        )

        async def poll(source: GccOfficialSource) -> None:
            nonlocal committed
            registration = production_source_catalog(settings).require(source.source_id)
            state = await operations.load_state(source.source_id)
            now = datetime.now(UTC)
            next_eligible_at = (
                _source_next_eligible_at(state, registration.cadence_seconds)
                if state is not None
                else None
            )
            if (
                not force
                and next_eligible_at is not None
                and next_eligible_at > now
            ):
                skipped.append(source.source_id)
                self._log.info(
                    "source.poll.skipped",
                    run_id=str(run_uuid),
                    source_id=source.source_id,
                    source_url=source.url,
                    reason=(
                        "failure_backoff_not_due"
                        if state is not None and state.consecutive_failures
                        else "cadence_not_due"
                    ),
                    next_eligible_at=next_eligible_at.isoformat(),
                )
                return
            async with semaphore:
                started = time.monotonic()
                self._log.info(
                    "source.poll.started",
                    run_id=str(run_uuid),
                    source_id=source.source_id,
                    source_url=source.url,
                )
                try:
                    result = await self._poll_gcc_source(source, run_uuid, operations)
                    polled.append(source.source_id)
                    committed += len(result.document_version_ids)
                    if result.observation.health is SourceHealth.FAILED:
                        failed.append(source.source_id)
                    self._log.info(
                        "source.poll.completed",
                        run_id=str(run_uuid),
                        source_run_id=str(result.run_id),
                        source_id=source.source_id,
                        source_url=source.url,
                        health=result.observation.health.value,
                        complete=result.observation.complete,
                        discovered_count=result.observation.discovered_count,
                        acquired_count=result.observation.acquired_count,
                        committed_count=result.observation.committed_count,
                        unchanged_count=result.observation.unchanged_count,
                        quarantine_count=result.observation.quarantine_count,
                        duration_ms=round((time.monotonic() - started) * 1000),
                    )
                except Exception as exc:
                    failed.append(source.source_id)
                    self._log.exception(
                        "source.poll.failed",
                        run_id=str(run_uuid),
                        source_id=source.source_id,
                        source_url=source.url,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        safe_error_summary=safe_error_summary(exc),
                        duration_ms=round((time.monotonic() - started) * 1000),
                    )

        await asyncio.gather(*(poll(source) for source in active_sources))
        leis = frozenset(
            item.strip().upper() for item in settings.covered_entity_leis.split(",") if item.strip()
        )
        if leis:
            try:
                result = await self._poll_gleif(leis, run_uuid, operations, force=force)
                if result is None:
                    skipped.append("gleif")
                else:
                    polled.append("gleif")
                    committed += len(result.document_version_ids)
            except Exception as exc:
                failed.append("gleif")
                self._log.exception(
                    "source.poll.failed",
                    run_id=str(run_uuid),
                    source_id="gleif",
                    error_type=type(exc).__name__,
                    safe_error_summary=safe_error_summary(exc),
                )
        report = SourceWorkerReport(
            polled_source_ids=tuple(sorted(set(polled))),
            skipped_source_ids=tuple(sorted(set(skipped))),
            failed_source_ids=tuple(sorted(set(failed))),
            committed_document_versions=committed,
        )
        for source_id in report.polled_source_ids:
            self._resources.telemetry.record_source_operation(source_id, "polled")
        for source_id in report.skipped_source_ids:
            self._resources.telemetry.record_source_operation(source_id, "skipped")
        for source_id in report.failed_source_ids:
            self._resources.telemetry.record_source_operation(source_id, "failed")
        self._log.info(
            "source.batch.completed",
            run_id=str(run_uuid),
            polled_source_ids=report.polled_source_ids,
            skipped_source_ids=report.skipped_source_ids,
            failed_source_ids=report.failed_source_ids,
            committed_document_versions=report.committed_document_versions,
        )
        return report

    async def _poll_gcc_source(
        self,
        source: GccOfficialSource,
        run_uuid: UUID,
        operations: PostgresSourceOperationsStore,
    ) -> SourceRunResult:
        settings = self._settings
        pool = self._resources.postgres_pool
        policy = public_source_policy()
        ledger = PostgresIntelligenceLedger(settings.postgres_dsn, pool=pool)
        ingestion = ReplayableIngestionService(
            self._resources.raw_archive,
            GccOfficialCanonicalizer(source, settings),
            ledger,
            PostgresIngestionControlStore(settings.postgres_dsn, pool=pool),
        )
        source_run_id = uuid5(run_uuid, source.source_id)
        adapter = OfficialGccRawAdapter(source, settings, policy, run_id=str(source_run_id))
        coordinator = SourceIngestionCoordinator(
            production_source_catalog(settings).require(source.source_id),
            policy,
            adapter,
            ingestion,
            operations,
        )
        try:
            return await coordinator.run(
                requested_by="canonical-source-worker",
                run_id=source_run_id,
            )
        finally:
            await adapter.close()
            await ingestion.close()

    async def _poll_gleif(
        self,
        leis: frozenset[str],
        run_uuid: UUID,
        operations: PostgresSourceOperationsStore,
        *,
        force: bool,
    ) -> SourceRunResult | None:
        settings = self._settings
        registration = production_source_catalog(settings).require("gleif")
        state = await operations.load_state("gleif")
        next_eligible_at = (
            _source_next_eligible_at(state, registration.cadence_seconds)
            if state is not None
            else None
        )
        if (
            not force
            and next_eligible_at is not None
            and next_eligible_at > datetime.now(UTC)
        ):
            return None
        pool = self._resources.postgres_pool
        policy = reference_source_policy()
        ledger = PostgresIntelligenceLedger(settings.postgres_dsn, pool=pool)
        ingestion = ReplayableIngestionService(
            self._resources.raw_archive,
            GleifDetailCanonicalizer(),
            ledger,
            PostgresIngestionControlStore(settings.postgres_dsn, pool=pool),
        )
        adapter = gleif_targeted_registered(policy, leis, settings)
        coordinator = SourceIngestionCoordinator(
            registration, policy, adapter, ingestion, operations
        )
        try:
            return await coordinator.run(
                requested_by="canonical-source-worker",
                run_id=uuid5(run_uuid, "gleif:target-entity-universe"),
            )
        finally:
            await adapter.close()
            await ingestion.close()


class CanonicalProjectionWorker:
    """Turn durable document/outbox events into indexed and graph projections."""

    def __init__(self, resources: RuntimeResources, *, worker_id: str) -> None:
        if not worker_id:
            raise ValueError("projection worker ID cannot be empty")
        self._resources = resources
        self._settings = resources.settings
        self._worker_id = worker_id

    async def run_once(  # noqa: C901 - coordinates independent durable projections
        self,
    ) -> ProjectionWorkerReport:
        settings = self._settings
        pool = self._resources.postgres_pool
        run_id = f"projection:{self._worker_id}:{uuid4()}"
        log = get_logger(component="canonical-projection-worker")
        log.info("projection.batch.started", run_id=run_id, worker_id=self._worker_id)
        ledger = PostgresIntelligenceLedger(settings.postgres_dsn, pool=pool)
        documents = PostgresDocumentStore(settings.postgres_dsn, pool=pool)
        resolution = PostgresResolutionStore(settings.postgres_dsn, pool=pool)
        proposed = PostgresProposedTypeSink(settings.postgres_dsn, pool=pool)
        usage = PostgresModelUsageLog(settings.postgres_dsn, pool=pool)
        corpus = PostgresCorpusStore(settings.postgres_dsn, pool=pool)
        bundle = await ModelBundle.build(
            settings=settings,
            usage_log=usage,
            run_id=run_id,
            subject_id="canonical-projection-worker",
        )
        assertion_writer = AssertionWriter(self._resources.graph)
        admission = LedgerAssertionAdmissionSink(
            ledger,
            documents,
            policy=public_source_policy(),
            correlation_id=uuid5(POLICY_NAMESPACE, run_id),
        )
        extraction = ExtractionPipeline(
            bundle.extractor,
            admission,
            proposed,
            EntityResolver(resolution),
            min_confidence=settings.min_extraction_confidence,
        )
        entitlement = PostgresEntitlementResolver(settings.postgres_dsn, pool=pool)
        access = await entitlement.resolve(
            Principal(
                principal_id="canonical-projection-worker",
                entitlement_group=settings.access_entitlement_group,
                side=Side(settings.access_side),
            ),
            run_id,
        )
        patterns = PatternRegistry(self._resources.graph, access=access)

        async def process_document(event: OutboxEvent) -> None:
            log.info(
                "projection.document.started",
                run_id=run_id,
                event_id=str(event.event_id),
                document_version_id=str(event.aggregate_id),
            )
            try:
                await self._set_document_job(event, "running")
                await self._project_document_event(
                    event,
                    ledger=ledger,
                    document_store=documents,
                    extraction=extraction,
                    resolution=resolution,
                )
                await self._set_document_job(event, "complete")
            except Exception as exc:
                try:
                    await self._set_document_job(
                        event, "retryable_failed", safe_error=safe_error_summary(exc)
                    )
                except Exception as state_error:
                    log.error(
                        "projection.document.state_update_failed",
                        run_id=run_id,
                        event_id=str(event.event_id),
                        document_version_id=str(event.aggregate_id),
                        target_state="retryable_failed",
                        error_type=type(state_error).__name__,
                        error_message=safe_console_error_message(state_error),
                        safe_error_summary=safe_error_summary(state_error),
                    )
                log.error(
                    "projection.document.failed",
                    run_id=run_id,
                    event_id=str(event.event_id),
                    document_version_id=str(event.aggregate_id),
                    error_type=type(exc).__name__,
                    status_code=getattr(exc, "status_code", None),
                    error_message=safe_console_error_message(exc),
                    safe_error_summary=safe_error_summary(exc),
                )
                raise
            log.info(
                "projection.document.completed",
                run_id=run_id,
                event_id=str(event.event_id),
                document_version_id=str(event.aggregate_id),
            )

        async def project_assertion(event: OutboxEvent) -> None:
            projection = event.payload.get("projection")
            if not isinstance(projection, dict):
                raise ValueError("assertion projection event has no typed payload")
            from fi_intel.ontology.schema import Assertion

            await assertion_writer.write(Assertion.model_validate(projection))

        async def project_signal(event: OutboxEvent) -> None:
            if event.payload.get("ledger_status") == "candidate":
                return
            payload = event.payload.get("signal")
            anchor = event.payload.get("score_anchor")
            if not isinstance(payload, dict) or not isinstance(anchor, int | float):
                raise ValueError("signal projection event has no typed payload")
            await patterns.project_signal(Signal.model_validate(payload), float(anchor))

        async def acknowledged(_event: OutboxEvent) -> None:
            return None

        dispatcher = OutboxDispatcher(
            ledger,
            {
                "raw-asset.archived.v1": acknowledged,
                "document.versioned.v1": process_document,
                "entity.registered.v1": acknowledged,
                "extraction.completed.v1": acknowledged,
                "entity.links-decided.v1": acknowledged,
                "assertion.accepted.v1": project_assertion,
                "signal.transitioned.v1": project_signal,
            },
            PostgresDeadLetterSink(settings.postgres_dsn, pool=pool),
            checkpoints=PostgresHandlerCheckpointStore(settings.postgres_dsn, pool=pool),
            max_attempts=settings.outbox_handler_max_attempts,
            handler_timeout_seconds=settings.outbox_handler_timeout_seconds,
            leases=PostgresOutboxLeaseStore(
                settings.postgres_dsn,
                pool=pool,
                worker_id=self._worker_id,
                lease_seconds=settings.outbox_lease_seconds,
            ),
        )
        attempted = published = quarantined = deferred = 0
        for _ in range(settings.outbox_dispatch_max_batches):
            report = await dispatcher.dispatch_pending(limit=settings.outbox_dispatch_batch_size)
            attempted += report.attempted
            published += report.published
            quarantined += report.quarantined
            deferred += report.deferred_by_circuit
            if report.published + report.quarantined + report.deferred_by_circuit == 0:
                break
        entity_projection = EntityReferenceProjection(settings.postgres_dsn, pool=pool)
        covered = frozenset(
            item.strip().upper() for item in settings.covered_entity_leis.split(",") if item.strip()
        )
        if covered:
            await entity_projection.synchronize(reference_source_policy(), covered)
        indexed = await corpus.index_chunks(bundle.embedder)
        projection_report = ProjectionWorkerReport(
            attempted=attempted,
            published=published,
            quarantined=quarantined,
            deferred=deferred,
            indexed_chunks=indexed,
        )
        self._resources.telemetry.record_queue_transition(
            "projection", "published", projection_report.published
        )
        self._resources.telemetry.record_queue_transition(
            "projection", "quarantined", projection_report.quarantined
        )
        self._resources.telemetry.record_retrieval(
            "index", "chunks", projection_report.indexed_chunks
        )
        log.info(
            "projection.batch.completed",
            run_id=run_id,
            worker_id=self._worker_id,
            **projection_report.model_dump(),
        )
        return projection_report

    async def _set_document_job(
        self, event: OutboxEvent, state: str, *, safe_error: str | None = None
    ) -> None:
        if state not in {"running", "complete", "retryable_failed"}:
            raise ValueError("unsupported document processing state")
        now = datetime.now(UTC)
        lease_owner = self._worker_id if state == "running" else None
        lease_expires_at = (
            now + timedelta(seconds=self._settings.worker_lease_seconds)
            if state == "running"
            else None
        )
        processed_at = now if state == "complete" else None
        pool = self._resources.postgres_pool
        await pool.execute(
            """
            INSERT INTO document_processing_job_v4 (
                document_version_id, event_id, state, attempt_count,
                next_attempt_at, lease_owner, lease_expires_at,
                safe_error_summary, processed_at, updated_at
            ) VALUES (
                $1::uuid,$2::uuid,$3::text,1,$4::timestamptz,
                $5::text,$6::timestamptz,$7::text,$8::timestamptz,$4::timestamptz
            )
            ON CONFLICT (document_version_id) DO UPDATE SET
                event_id=EXCLUDED.event_id,
                state=EXCLUDED.state,
                attempt_count=document_processing_job_v4.attempt_count +
                    CASE WHEN EXCLUDED.state='running' THEN 1 ELSE 0 END,
                next_attempt_at=EXCLUDED.next_attempt_at,
                lease_owner=EXCLUDED.lease_owner,
                lease_expires_at=EXCLUDED.lease_expires_at,
                safe_error_summary=EXCLUDED.safe_error_summary,
                processed_at=COALESCE(EXCLUDED.processed_at,
                                      document_processing_job_v4.processed_at),
                updated_at=EXCLUDED.updated_at
            """,
            event.aggregate_id,
            event.event_id,
            state,
            now,
            lease_owner,
            lease_expires_at,
            safe_error,
            processed_at,
        )

    async def _project_document_event(
        self,
        event: OutboxEvent,
        *,
        ledger: PostgresIntelligenceLedger,
        document_store: PostgresDocumentStore,
        extraction: ExtractionPipeline,
        resolution: PostgresResolutionStore,
    ) -> None:
        version = await ledger.document_version(event.aggregate_id)
        if version is None:
            raise RuntimeError("document event references an unknown version")
        identity = await ledger.document_identity(version.document_id)
        raw_asset = await ledger.raw_asset(version.raw_asset_id)
        if identity is None or raw_asset is None:
            raise RuntimeError("document version lacks immutable identity or raw asset")
        source = next(
            (item for item in GCC_OFFICIAL_SOURCES if item.source_id == identity.source_id),
            None,
        )
        archive = self._resources.raw_archive
        raw_bytes = await archive.get(raw_asset.object_uri)
        headers_value = raw_asset.metadata.get("headers")
        headers = tuple(
            RawHeader.model_validate(item)
            for item in (headers_value if isinstance(headers_value, list) else [])
        )
        published_value = raw_asset.metadata.get("source_published_at")
        source_policy = (
            reference_source_policy() if identity.source_id == "gleif" else public_source_policy()
        )
        envelope = RawSourceEnvelope(
            source_id=identity.source_id,
            external_id=identity.external_id,
            source_revision=raw_asset.source_revision,
            payload=raw_bytes,
            media_type=raw_asset.media_type,
            headers=headers,
            fetched_at=raw_asset.fetched_at,
            source_published_at=(
                datetime.fromisoformat(published_value)
                if isinstance(published_value, str) and published_value
                else None
            ),
            access_policy=source_policy.model_copy(update={"policy_id": version.policy_id}),
        )
        if source is not None:
            canonical = await GccOfficialCanonicalizer(source, self._settings).canonicalize(
                envelope
            )
        elif identity.source_id == "gleif":
            canonical = await GleifDetailCanonicalizer().canonicalize(envelope)
        elif identity.source_id in {"sec_edgar_8k", "fed_press_releases"}:
            canonical = await GovernmentDetailCanonicalizer().canonicalize(envelope)
        else:
            raise ValueError(f"no governed projector for source {identity.source_id!r}")
        normalized = (await archive.get(version.normalized_object_uri)).decode("utf-8")
        title, separator, body = normalized.partition("\n")
        if not separator or not body:
            raise ValueError("archived canonical source has no title/body boundary")
        projection = canonical.model_copy(
            update={
                "doc_id": str(version.document_version_id),
                "published_at": version.published_at,
                "recorded_at": version.recorded_at,
                "title": title,
                "body": body,
                "language": version.language,
                "document_class": version.document_class,
                "metadata": {
                    **canonical.metadata,
                    **(
                        {"country": source.country, "source_type": source.source_type}
                        if source is not None
                        else {"source_type": identity.source_id}
                    ),
                    "ledger_document_id": str(version.document_id),
                    "ledger_document_version_id": str(version.document_version_id),
                },
            }
        )
        await document_store.commit_batch(
            [projection],
            [],
            FetchCursor(
                source_id=identity.source_id,
                position=str(version.document_version_id),
                updated_at=version.recorded_at,
            ),
        )
        if projection.document_class is DocumentClass.REFERENCE:
            await resolution.load_reference([projection])
        else:
            result = await extraction.extract_document(projection, datetime.now(UTC))
            get_logger(component="canonical-projection-worker").info(
                "projection.document.extracted",
                source_id=projection.source_id,
                document_version_id=projection.doc_id,
                assertions_written=result.assertions_written,
                proposed_types=result.proposed_types,
                offset_rejections=result.offset_rejections,
                semantic_rejections=result.semantic_rejections,
                claims_held_for_resolution=result.claims_held_for_resolution,
            )


async def run_continuously(
    operation: Callable[[], Awaitable[object]],
    *,
    interval_seconds: float,
    stop: asyncio.Event | None = None,
    operation_name: str | None = None,
    monitor: RuntimeMonitor | None = None,
) -> None:
    """Run a bounded operation repeatedly, logging failures without killing the worker."""

    if interval_seconds <= 0:
        raise ValueError("worker poll interval must be positive")
    stop_event = stop or asyncio.Event()
    name = operation_name or getattr(operation, "__qualname__", type(operation).__name__)
    log = get_logger(component="worker-loop", operation=name)
    loop_run_id = f"worker-loop:{uuid4()}"
    log.info(
        "worker.loop.started",
        run_id=loop_run_id,
        interval_seconds=interval_seconds,
    )
    if monitor is not None:
        await monitor.loop_started(loop_run_id)
    try:
        while not stop_event.is_set():
            started = time.monotonic()
            if monitor is not None:
                await monitor.iteration_started()
            operation_task: asyncio.Future[object] | None = None
            try:
                if monitor is None:
                    await operation()
                else:
                    operation_task = asyncio.ensure_future(operation())
                    heartbeat_seconds = max(1.0, min(10.0, interval_seconds))
                    while not operation_task.done():
                        await asyncio.wait(
                            {operation_task},
                            timeout=heartbeat_seconds,
                        )
                        if not operation_task.done():
                            await monitor.heartbeat()
                    await operation_task
            except asyncio.CancelledError:
                if operation_task is not None and not operation_task.done():
                    operation_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await operation_task
                raise
            except Exception as exc:
                duration_ms = (time.monotonic() - started) * 1_000.0
                if monitor is not None:
                    await monitor.iteration_failed(exc, duration_ms)
                log.exception(
                    "worker.iteration.failed",
                    run_id=loop_run_id,
                    error_type=type(exc).__name__,
                    safe_error_summary=safe_error_summary(exc),
                    duration_ms=round(duration_ms),
                    retry_in_seconds=interval_seconds,
                )
            else:
                duration_ms = (time.monotonic() - started) * 1_000.0
                if monitor is not None:
                    await monitor.iteration_completed(duration_ms)
                log.info(
                    "worker.iteration.completed",
                    run_id=loop_run_id,
                    duration_ms=round(duration_ms),
                )
            if monitor is not None:
                await monitor.heartbeat()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                pass
    finally:
        if monitor is not None:
            await monitor.loop_stopped()


__all__ = [
    "CanonicalProjectionWorker",
    "CanonicalSourceWorker",
    "ProjectionWorkerReport",
    "SourceWorkerReport",
    "run_continuously",
]
