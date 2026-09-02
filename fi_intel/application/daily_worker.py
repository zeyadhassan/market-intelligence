"""Durable daily analysis worker over already processed canonical inputs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid5

from pydantic import BaseModel, ConfigDict

from fi_intel.agents.brief import BriefCompiler
from fi_intel.agents.investigation import (
    InvestigationPolicy,
    InvestigationState,
    PostgresInvestigationStore,
)
from fi_intel.agents.opportunity_research import OpportunityResearcher
from fi_intel.application.analysis_state import (
    AnalysisCompletion,
    AnalysisRunRecord,
    AnalysisScopeJob,
    DetectorExecutionRecord,
    PostgresAnalysisStateStore,
    _digest,
)
from fi_intel.application.jobs import (
    AnalysisJob,
    AnalysisJobState,
    PostgresAnalysisJobStore,
    stable_digest,
)
from fi_intel.application.opportunities import (
    PostgresOpportunityRepository,
)
from fi_intel.application.policies import POLICY_NAMESPACE, public_source_policy
from fi_intel.application.result_admission import DailyResultAdmission
from fi_intel.application.runtime_resources import RuntimeResources
from fi_intel.application.signal_authority import LedgerSignalAuthority
from fi_intel.application.topics import PostgresTopicCatalog
from fi_intel.governance.audit import PostgresAuditLog
from fi_intel.governance.model_usage import ModelCapacityLimits, PostgresModelUsageLog
from fi_intel.governance.policy import PostgresEntitlementResolver
from fi_intel.governance.serving import ModelBundle
from fi_intel.graph.coverage import (
    PostgresFactualCoverageStore,
    SourceOperationsCoverageProvider,
    source_coverage_policy,
)
from fi_intel.graph.entry import PostgresGraphEntryResolver
from fi_intel.graph.precision import PostgresPatternPrecisionProvider
from fi_intel.graph.registry import PatternRegistry
from fi_intel.graph.signals import signal_authorization_scope
from fi_intel.ingest.store import PostgresDocumentStore
from fi_intel.ledger.repository import PostgresIntelligenceLedger
from fi_intel.logging import get_logger, safe_error_summary
from fi_intel.results.manifest import (
    ChangeClassification,
    SourceVersionManifest,
    admit_result,
)
from fi_intel.retrieval.corpus import CorpusSearch
from fi_intel.retrieval.service import RetrievalService
from fi_intel.retrieval.store import PostgresCorpusStore
from fi_intel.sources.operations import PostgresSourceOperationsStore, SourceHealth
from fi_intel.tools.research_tools import ResearchTools, ToolContext


class DailyAnalysisOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    state: AnalysisJobState
    coverage: dict[str, object]
    latest_source_time: datetime | None
    topic_messages: dict[str, str]


def _json_items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TypeError("stored JSON collection must be an array")
    return tuple(value)


class ProcessedDailyAnalysis:
    """Thin graph-first workflow whose inputs were produced by other workers."""

    def __init__(self, resources: RuntimeResources) -> None:
        self._resources = resources
        self._settings = resources.settings

    async def run(self, job: AnalysisJob) -> DailyAnalysisOutcome:  # noqa: C901, PLR0915
        if job.state is not AnalysisJobState.RUNNING:
            raise ValueError("daily use case requires a claimed analysis job")
        settings = self._settings
        pool = self._resources.postgres_pool
        state = PostgresAnalysisStateStore(settings.postgres_dsn, pool=pool)
        topics = await PostgresTopicCatalog(settings.postgres_dsn, pool=pool).require_many(
            job.topic_ids
        )
        entitlement = PostgresEntitlementResolver(settings.postgres_dsn, pool=pool)
        access = await entitlement.resolve(job.principal.restore().principal, job.job_id)
        scope = signal_authorization_scope(
            access.principal.entitlement_group,
            access.principal.side.value,
            access.allowed_source_ids,
        )
        if scope != job.authorization_scope:
            raise RuntimeError("analysis authorization scope changed before execution")
        required_sources = set(
            str(item) for item in job.input_manifest.get("required_source_ids", ())
        )
        required_sources &= set(access.allowed_source_ids)
        if not required_sources:
            raise RuntimeError("analysis job has no authorized required sources")

        frozen, coverage, latest_source_time = await self._freeze_inputs(job, required_sources)
        frozen_digest = stable_digest(frozen)
        run_id = str(uuid5(POLICY_NAMESPACE, f"daily-analysis:{frozen_digest}"))
        run = AnalysisRunRecord(
            run_id=run_id,
            mode=settings.analysis_mode,
            principal_id=job.principal.principal_id,
            authorization_scope=scope,
            policy_version=access.policy_version,
            temporal_pin=job.temporal_pin,
            input_manifest_digest=frozen_digest,
            created_at=job.requested_at,
        )
        run = await state.create_run(run)
        existing_completion = await state.latest_completion(run_id)
        if existing_completion is not None:
            outcome_state = (
                AnalysisJobState.COMPLETE if existing_completion.complete else AnalysisJobState.HELD
            )
            return DailyAnalysisOutcome(
                run_id=run_id,
                state=outcome_state,
                coverage=coverage,
                latest_source_time=latest_source_time,
                topic_messages={
                    topic_id: (
                        "Analysis complete."
                        if existing_completion.complete
                        else "Analysis incomplete - no absence claim can be made."
                    )
                    for topic_id in job.topic_ids
                },
            )
        await state.transition_run(
            run_id, InvestigationState.RUNNING, occurred_at=datetime.now(UTC)
        )
        scope_jobs = {
            topic_id: AnalysisScopeJob.create(
                run_id=run_id,
                topic_id=topic_id,
                scope={
                    "topic_version": topics[topic_id].version,
                    "patterns": sorted(topics[topic_id].patterns),
                },
                created_at=run.temporal_pin,
            )
            for topic_id in job.topic_ids
        }
        for scope_job in scope_jobs.values():
            await state.create_scope_job(scope_job)

        coverage_reasons = [str(item) for item in _json_items(coverage.get("reasons"))]
        completed_sources = set(
            str(item) for item in _json_items(coverage.get("completed_source_ids"))
        )
        required_job_ids = set(
            str(item) for item in _json_items(coverage.get("required_document_version_ids"))
        )
        completed_job_ids = set(
            str(item) for item in _json_items(coverage.get("processed_document_version_ids"))
        )
        if coverage_reasons:
            for topic_id, scope_job in scope_jobs.items():
                for pattern_name in sorted(topics[topic_id].patterns):
                    await state.record_detector(
                        DetectorExecutionRecord(
                            execution_id=_digest(
                                [scope_job.job_id, pattern_name, "held-input-coverage"]
                            ),
                            job_id=scope_job.job_id,
                            pattern_name=pattern_name,
                            pattern_version="registered-graph-template",
                            state="held_coverage",
                            coverage_decision={
                                "complete": False,
                                "reasons": coverage_reasons,
                                "checked_source_ids": sorted(required_sources),
                            },
                            input_digest=frozen_digest,
                            output_digest=_digest([]),
                            started_at=datetime.now(UTC),
                            finished_at=datetime.now(UTC),
                        )
                    )
            completion = AnalysisCompletion.compute(
                run_id=run_id,
                required_source_ids=required_sources,
                completed_source_ids=completed_sources,
                required_job_ids=required_job_ids,
                completed_job_ids=completed_job_ids,
                coverage_reasons=tuple(sorted(set(coverage_reasons))),
            )
            await state.record_completion(completion)
            await state.transition_run(
                run_id,
                InvestigationState.HELD,
                occurred_at=datetime.now(UTC),
                safe_error_summary="; ".join(coverage_reasons)[:500],
            )
            return DailyAnalysisOutcome(
                run_id=run_id,
                state=AnalysisJobState.HELD,
                coverage=coverage,
                latest_source_time=latest_source_time,
                topic_messages={
                    topic_id: "Analysis incomplete - no absence claim can be made."
                    for topic_id in job.topic_ids
                },
            )

        documents = PostgresDocumentStore(settings.postgres_dsn, pool=pool)
        operations = PostgresSourceOperationsStore(settings.postgres_dsn, pool=pool)
        audit = PostgresAuditLog(settings.postgres_dsn, pool=pool)
        usage = PostgresModelUsageLog(settings.postgres_dsn, pool=pool)
        resolution_entry = PostgresGraphEntryResolver(settings.postgres_dsn, pool=pool)
        corpus = PostgresCorpusStore(settings.postgres_dsn, pool=pool)
        factual = PostgresFactualCoverageStore(settings.postgres_dsn, pool=pool)
        precision = PostgresPatternPrecisionProvider(
            settings.postgres_dsn,
            pool=pool,
            full_weight_samples=settings.historical_precision_full_weight_samples,
        )
        investigations = PostgresInvestigationStore(settings.postgres_dsn, pool=pool)
        ledger = PostgresIntelligenceLedger(settings.postgres_dsn, pool=pool)
        bundle = await ModelBundle.build(
            settings=settings,
            usage_log=usage,
            run_id=run_id,
            subject_id=scope,
        )
        configured_coverage = {
            item.strip()
            for item in settings.coverage_required_source_ids.split(",")
            if item.strip()
        }
        registry = PatternRegistry(
            self._resources.graph,
            access=access.model_copy(update={"run_id": run_id}),
            coverage=SourceOperationsCoverageProvider(
                operations,
                required_source_ids=source_coverage_policy(
                    frozenset(configured_coverage or required_sources)
                ),
                covered_entity_keys=frozenset(
                    str(item) for item in _json_items(frozen.get("covered_entity_leis"))
                ),
                factual_store=factual,
            ),
            precision=precision,
            signal_authority=LedgerSignalAuthority(
                ledger,
                policy=public_source_policy(),
                correlation_id=uuid5(POLICY_NAMESPACE, run_id),
            ),
            defer_signal_projection=True,
        )
        retrieval = RetrievalService(CorpusSearch(corpus, bundle.embedder), audit, run_id)
        tools = ResearchTools(
            retrieval,
            self._resources.graph,
            registry,
            ToolContext(principal=access.principal, as_of=run.temporal_pin),
            entry_resolver=resolution_entry,
            reranker=bundle.reranker,
        )
        researcher = OpportunityResearcher(
            tools,
            bundle.reasoner,
            documents,
            investigation_store=investigations,
            investigation_policy=InvestigationPolicy(
                max_attempts_per_step=settings.daily_signal_max_attempts
            ),
            entailment_verifier=bundle.entailment,
            require_semantic_entailment=True,
            run_id=run_id,
        )
        compiler = BriefCompiler(
            registry,
            researcher,
            capacity_limits=ModelCapacityLimits(
                max_calls=settings.daily_max_model_calls,
                max_total_tokens=settings.daily_max_model_tokens,
                max_latency_ms=settings.daily_max_model_latency_seconds * 1_000.0,
            ),
            usage_log=usage,
            run_id=run_id,
            settings=settings,
        )
        enabled = {pattern for topic_id in job.topic_ids for pattern in topics[topic_id].patterns}
        detector_started = datetime.now(UTC)
        brief = await compiler.compile(run.temporal_pin, desk="fi_gcc", enabled=enabled)
        self._resources.telemetry.record_model_outcome("daily-analysis", "completed")
        pending_signal_projection = await pool.fetchval(
            """
            SELECT count(*) FROM transactional_outbox
            WHERE event_type='signal.transitioned.v1'
              AND correlation_id=$1 AND published_at IS NULL
            """,
            uuid5(POLICY_NAMESPACE, run_id),
        )
        if int(pending_signal_projection or 0):
            raise RuntimeError("authoritative signal transitions are awaiting graph projection")
        detector_finished = datetime.now(UTC)
        gap_by_pattern = {gap.pattern_name: gap for gap in brief.dark_detectors}
        failed_patterns = {item.signal.pattern for item in brief.failed_signals}
        deferred_patterns = {item.pattern for item in brief.deferred_signals}
        observed_signals = (
            [item.signal for item in brief.items]
            + brief.unresearched_signals
            + brief.deferred_signals
            + brief.abstained_signals
            + [item.signal for item in brief.failed_signals]
        )
        versions = {pattern.name: pattern.version for pattern in registry.registered_patterns()}
        completed_scope_jobs: set[str] = set()
        for topic_id, scope_job in scope_jobs.items():
            topic_patterns = topics[topic_id].patterns
            incomplete = bool(
                topic_patterns & (gap_by_pattern.keys() | failed_patterns | deferred_patterns)
            )
            for pattern_name in sorted(topic_patterns):
                gap = gap_by_pattern.get(pattern_name)
                signal_ids = sorted(
                    signal.signal_id
                    for signal in observed_signals
                    if signal.pattern == pattern_name
                )
                input_digest = _digest(
                    [scope_job.job_id, pattern_name, frozen_digest, run.temporal_pin]
                )
                await state.record_detector(
                    DetectorExecutionRecord(
                        execution_id=_digest(
                            [scope_job.job_id, pattern_name, versions[pattern_name], input_digest]
                        ),
                        job_id=scope_job.job_id,
                        pattern_name=pattern_name,
                        pattern_version=versions[pattern_name],
                        state=(
                            "held_coverage"
                            if gap is not None
                            else "failed"
                            if pattern_name in failed_patterns
                            else "deferred"
                            if pattern_name in deferred_patterns
                            else "completed"
                        ),
                        coverage_decision={
                            "complete": gap is None,
                            "reasons": list(gap.reasons) if gap is not None else [],
                            "checked_source_ids": (
                                list(gap.checked_source_ids) if gap is not None else []
                            ),
                        },
                        input_digest=input_digest,
                        output_digest=_digest(signal_ids),
                        started_at=detector_started,
                        finished_at=detector_finished,
                    )
                )
            if not incomplete:
                completed_scope_jobs.add(scope_job.job_id)
        coverage_reasons.extend(
            f"detector {gap.pattern_name}: {'; '.join(gap.reasons)}" for gap in brief.dark_detectors
        )
        coverage_reasons.extend(
            f"signal {item.signal.signal_id} failed: {item.error_type}"
            for item in brief.failed_signals
        )
        if brief.deferred_signals:
            coverage_reasons.append(
                f"{len(brief.deferred_signals)} signals deferred by model capacity"
            )
        completion = AnalysisCompletion.compute(
            run_id=run_id,
            required_source_ids=required_sources,
            completed_source_ids=completed_sources,
            required_job_ids=set(scope_job.job_id for scope_job in scope_jobs.values()),
            completed_job_ids=completed_scope_jobs,
            coverage_reasons=tuple(sorted(set(coverage_reasons))),
        )
        if not completion.complete:
            await state.record_completion(completion)
            coverage["complete"] = False
            coverage["reasons"] = list(completion.coverage_reasons)
            await state.transition_run(
                run_id,
                InvestigationState.HELD,
                occurred_at=datetime.now(UTC),
                safe_error_summary="; ".join(completion.coverage_reasons)[:500],
            )
            return DailyAnalysisOutcome(
                run_id=run_id,
                state=AnalysisJobState.HELD,
                coverage=coverage,
                latest_source_time=latest_source_time,
                topic_messages={
                    topic_id: "Analysis incomplete - no absence claim can be made."
                    for topic_id in job.topic_ids
                },
            )

        manifests = {
            (item.source_id, item.document_version_id): item
            for item in self._source_manifests(frozen)
        }
        topic_by_pattern = {
            pattern: topic_id for topic_id, topic in topics.items() for pattern in topic.patterns
        }
        candidates = await DailyResultAdmission(state, documents, topic_by_pattern).build(
            brief,
            bundle,
            authorization_scope=scope,
            run_id=run_id,
            required_sources=required_sources,
            completed_sources=completed_sources,
            source_manifests=manifests,
        )
        opportunities = PostgresOpportunityRepository(settings.postgres_dsn, pool=pool)
        observed_by_topic: dict[str, set[str]] = {topic_id: set() for topic_id in job.topic_ids}
        new_or_changed: dict[str, int] = {topic_id: 0 for topic_id in job.topic_ids}
        for candidate in candidates:
            decision = await opportunities.classify(candidate)
            final_result = candidate
            if decision.write_result_version:
                classification = ChangeClassification(decision.state.value)
                final_result = admit_result(
                    candidate.manifest.model_copy(update={"change_classification": classification})
                )
                decision = decision.model_copy(
                    update={"result_version_id": final_result.result_version_id}
                )
                await state.put_result(final_result)
                new_or_changed[final_result.manifest.topic_id] += 1
            await opportunities.record(final_result, decision, occurred_at=datetime.now(UTC))
            self._resources.telemetry.record_result_transition(
                decision.state.value,
                "new-version" if decision.write_result_version else "retained-version",
            )
            observed_by_topic[final_result.manifest.topic_id].add(decision.logical_opportunity_id)
        for topic_id in job.topic_ids:
            resolved = await opportunities.resolve_missing(
                topic_id,
                scope,
                observed_by_topic[topic_id],
                occurred_at=datetime.now(UTC),
            )
            if resolved:
                self._resources.telemetry.record_result_transition("resolved", "transition")
        await state.transition_run(
            run_id, InvestigationState.PUBLISHED, occurred_at=datetime.now(UTC)
        )
        coverage["complete"] = True
        # Completion is the final durable marker. A process death before this
        # point safely replays deterministic admission; a death after it lets
        # the stale job lease resume directly at job/read-model finalization.
        await state.record_completion(completion)
        messages = {
            topic_id: (
                f"{new_or_changed[topic_id]} new or materially changed opportunities"
                if new_or_changed[topic_id]
                else "Analysis complete - nothing new in the covered scope"
            )
            for topic_id in job.topic_ids
        }
        return DailyAnalysisOutcome(
            run_id=run_id,
            state=AnalysisJobState.COMPLETE,
            coverage=coverage,
            latest_source_time=latest_source_time,
            topic_messages=messages,
        )

    async def _freeze_inputs(
        self, job: AnalysisJob, required_sources: set[str]
    ) -> tuple[dict[str, object], dict[str, object], datetime | None]:
        pool = self._resources.postgres_pool
        observations = await pool.fetch(
            """
            SELECT DISTINCT ON (source_id) * FROM source_observation_v2
            WHERE source_id = ANY($1::text[]) AND finished_at <= $2
            ORDER BY source_id, finished_at DESC, observation_id DESC
            """,
            sorted(required_sources),
            job.temporal_pin,
        )
        by_source = {str(row["source_id"]): row for row in observations}
        completed_sources = {
            source_id
            for source_id, row in by_source.items()
            if row["health"] == SourceHealth.HEALTHY.value
            and row["complete"]
            and row["fresh"]
            and not row["silent"]
            and row["within_expected_volume"]
        }
        reasons = [
            f"required source {source_id} has no complete fresh observation before the pin"
            for source_id in sorted(required_sources - completed_sources)
        ]
        observation_run_ids = [row["run_id"] for row in observations]
        document_rows = (
            await pool.fetch(
                """
            SELECT DISTINCT result_document_version_id
            FROM ingest_job_v2
            WHERE run_id = ANY($1::uuid[])
              AND status IN ('committed','not_novel')
              AND result_document_version_id IS NOT NULL
            ORDER BY result_document_version_id
            """,
                observation_run_ids,
            )
            if observation_run_ids
            else []
        )
        required_documents = {str(row["result_document_version_id"]) for row in document_rows}
        processed_rows = (
            await pool.fetch(
                """
            SELECT document_version_id FROM document_processing_job_v4
            WHERE document_version_id = ANY($1::uuid[]) AND state='complete'
            """,
                sorted(required_documents),
            )
            if required_documents
            else []
        )
        processed_documents = {str(row["document_version_id"]) for row in processed_rows}
        missing_processing = required_documents - processed_documents
        if missing_processing:
            reasons.append(
                f"{len(missing_processing)} current document versions are not fully processed"
            )
        pending_projection = await pool.fetchval(
            """
            SELECT count(*) FROM transactional_outbox
            WHERE published_at IS NULL AND occurred_at <= $1
              AND event_type IN ('document.versioned.v1','assertion.accepted.v1',
                                 'signal.transitioned.v1')
            """,
            job.temporal_pin,
        )
        if int(pending_projection or 0):
            reasons.append(f"{int(pending_projection)} projection events remain pending")
        quarantined_projection = await pool.fetchval(
            """
            SELECT count(*) FROM outbox_dead_letter_v3 dead
            JOIN transactional_outbox event USING (event_id)
            WHERE event.occurred_at <= $1
              AND event.event_type IN ('document.versioned.v1','assertion.accepted.v1',
                                       'signal.transitioned.v1')
              AND NOT EXISTS (
                SELECT 1 FROM transactional_outbox replay
                WHERE replay.causation_id=event.event_id
                  AND replay.published_at IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM outbox_dead_letter_v3 replay_dead
                    WHERE replay_dead.event_id=replay.event_id
                  )
              )
            """,
            job.temporal_pin,
        )
        if int(quarantined_projection or 0):
            reasons.append(
                f"{int(quarantined_projection)} projection events require operator recovery"
            )
        all_versions = await pool.fetch(
            """
            SELECT version.document_version_id, identity.source_id,
                   version.normalized_text_hash, version.parser_version
            FROM document_version version
            JOIN document_identity identity USING (document_id)
            WHERE identity.source_id = ANY($1::text[])
              AND version.recorded_at <= $2
            ORDER BY version.recorded_at DESC, version.document_version_id DESC
            LIMIT 10000
            """,
            sorted(required_sources),
            job.temporal_pin,
        )
        index_state = await pool.fetchrow(
            """
            SELECT embed_model_version, embedding_dim, chunker_version, indexed_at
            FROM retrieval_index_state WHERE index_name='document_chunk' AND status='ready'
            """
        )
        if index_state is None:
            reasons.append("retrieval index is not ready")
        frozen: dict[str, object] = {
            **job.input_manifest,
            "temporal_pin": job.temporal_pin.isoformat(),
            "source_observation_ids": sorted(str(row["observation_id"]) for row in observations),
            "document_versions": [
                {
                    "document_version_id": str(row["document_version_id"]),
                    "source_id": str(row["source_id"]),
                    "content_hash": str(row["normalized_text_hash"]),
                    "parser_version": str(row["parser_version"]),
                }
                for row in all_versions
            ],
            "retrieval_index": dict(index_state) if index_state is not None else None,
        }
        async with pool.acquire() as connection, connection.transaction():
            stored = await connection.fetchval(
                "SELECT frozen_manifest FROM analysis_job_v4 WHERE job_id=$1 FOR UPDATE",
                job.job_id,
            )
            if stored is None:
                await connection.execute(
                    """
                    UPDATE analysis_job_v4 SET frozen_manifest=$2::jsonb, frozen_at=$3
                    WHERE job_id=$1 AND frozen_manifest IS NULL
                    """,
                    job.job_id,
                    json.dumps(frozen, default=str, sort_keys=True),
                    datetime.now(UTC),
                )
            else:
                decoded = json.loads(stored) if isinstance(stored, str) else dict(stored)
                if stable_digest(decoded) != stable_digest(frozen):
                    raise RuntimeError("analysis input manifest changed after it was frozen")
                frozen = decoded
        latest_source_time = max(
            (row["latest_source_published_at"] or row["finished_at"] for row in observations),
            default=None,
        )
        coverage: dict[str, object] = {
            "complete": not reasons,
            "required_source_ids": sorted(required_sources),
            "completed_source_ids": sorted(completed_sources),
            "required_document_version_ids": sorted(required_documents),
            "processed_document_version_ids": sorted(processed_documents),
            "reasons": reasons,
        }
        return frozen, coverage, latest_source_time

    @staticmethod
    def _source_manifests(frozen: dict[str, object]) -> list[SourceVersionManifest]:
        raw = frozen.get("document_versions", ())
        if not isinstance(raw, list):
            return []
        return [
            SourceVersionManifest(
                source_id=str(item["source_id"]),
                document_version_id=str(item["document_version_id"]),
                content_hash=str(item["content_hash"]),
                parser_version=str(item["parser_version"]),
            )
            for item in raw
            if isinstance(item, dict)
        ]


class CanonicalAnalysisJobWorker:
    def __init__(self, resources: RuntimeResources, *, worker_id: str) -> None:
        if not worker_id:
            raise ValueError("analysis worker ID cannot be empty")
        self._resources = resources
        self._worker_id = worker_id
        self._jobs = PostgresAnalysisJobStore(
            resources.settings.postgres_dsn, pool=resources.postgres_pool
        )
        self._analysis = ProcessedDailyAnalysis(resources)
        self._opportunities = PostgresOpportunityRepository(
            resources.settings.postgres_dsn, pool=resources.postgres_pool
        )
        self._log = get_logger(component="canonical-analysis-worker")

    async def run_once(self) -> AnalysisJob | None:
        settings = self._resources.settings
        log = getattr(self, "_log", get_logger(component="canonical-analysis-worker"))
        job = await self._jobs.claim(self._worker_id, settings.worker_lease_seconds)
        if job is None:
            return None
        log.info(
            "analysis.job.started",
            run_id=str(job.run_id or job.job_id),
            job_id=str(job.job_id),
            worker_id=self._worker_id,
            attempt_count=job.attempt_count,
            topic_ids=job.topic_ids,
            required_source_ids=job.input_manifest.get("required_source_ids", []),
        )
        self._resources.telemetry.record_queue_transition("analysis", "running")
        try:
            outcome = await self._analysis.run(job)
            reasons = _json_items(outcome.coverage.get("reasons"))
            self._resources.telemetry.record_coverage(
                complete=bool(outcome.coverage.get("complete", False)),
                reason_class="none" if not reasons else "incomplete-input-or-detector",
            )
            projected_job = job.model_copy(
                update={"state": outcome.state, "run_id": outcome.run_id}
            )
            for topic_id in projected_job.topic_ids:
                await self._opportunities.materialize_topic(
                    projected_job,
                    topic_id,
                    run_id=outcome.run_id,
                    coverage=outcome.coverage,
                    latest_source_time=outcome.latest_source_time,
                    safe_message=outcome.topic_messages[topic_id],
                )
            # Job finalization comes last. A process death before this line
            # leaves a reclaimable lease and idempotent read-model upserts.
            finished = await self._jobs.finish(
                job.job_id,
                self._worker_id,
                outcome.state,
                run_id=outcome.run_id,
                safe_detail="; ".join(str(item) for item in reasons)[:500] or None,
            )
            self._resources.telemetry.record_queue_transition("analysis", finished.state.value)
            log.info(
                "analysis.job.completed",
                run_id=str(outcome.run_id),
                job_id=str(job.job_id),
                worker_id=self._worker_id,
                state=finished.state.value,
                coverage_complete=bool(outcome.coverage.get("complete", False)),
                coverage_reasons=reasons,
            )
            return finished
        except Exception as exc:
            failed = await self._jobs.fail(
                job.job_id,
                self._worker_id,
                exc,
                retryable=not isinstance(exc, (ValueError, TypeError)),
                max_attempts=settings.worker_max_attempts,
            )
            self._resources.telemetry.record_queue_transition("analysis", failed.state.value)
            log.exception(
                "analysis.job.failed",
                run_id=str(job.run_id or job.job_id),
                job_id=str(job.job_id),
                worker_id=self._worker_id,
                state=failed.state.value,
                error_type=type(exc).__name__,
                safe_error_summary=safe_error_summary(exc),
            )
            return failed


__all__ = [
    "CanonicalAnalysisJobWorker",
    "DailyAnalysisOutcome",
    "ProcessedDailyAnalysis",
]
