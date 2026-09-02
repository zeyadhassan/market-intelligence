"""Stage One product service over canonical immutable analysis results."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import asyncpg

from fi_intel.api.auth import AuthorizationError, RequestPrincipal
from fi_intel.api.models import (
    LiveSourceStatusView,
    OpportunityEvidenceView,
    OpportunityResultView,
    ResultEvaluationReceipt,
    ResultEvaluationRequest,
    SearchCreateRequest,
    SearchView,
    TopicResultsView,
    TopicSubscriptionUpdate,
    TopicSubscriptionView,
    TopicTagView,
)
from fi_intel.api.service import ResourceNotFoundError
from fi_intel.application.analysis_state import EvaluationVerdict, PostgresAnalysisStateStore
from fi_intel.application.jobs import (
    AnalysisJob,
    AnalysisJobState,
    AnalysisJobStore,
    PostgresAnalysisJobStore,
    PrincipalSnapshot,
)
from fi_intel.application.runtime_resources import PostgresPoolProvider
from fi_intel.application.search import PostgresSearchStore
from fi_intel.application.topics import PostgresTopicCatalog
from fi_intel.config import Settings
from fi_intel.graph.signals import signal_authorization_scope
from fi_intel.results.manifest import ImmutableResultManifest
from fi_intel.sources.adapters.gcc_official import GCC_OFFICIAL_SOURCES
from fi_intel.telemetry import PipelineStage, Telemetry

STAGE_ONE_DESK = "fi_gcc"


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = value
    if not isinstance(parsed, dict):
        raise TypeError("stored manifest must be a JSON object")
    return {str(key): item for key, item in parsed.items()}


def _json_items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TypeError("stored JSON collection must be an array")
    return tuple(value)


def _json_integer(value: object, *, key: str) -> int:
    if not isinstance(value, int):
        raise TypeError(f"stored JSON counter {key!r} must be an integer")
    return value


class PostgresStageOneService:
    """Enqueue durable work and render the PostgreSQL daily read model."""

    def __init__(
        self,
        dsn: str,
        *,
        settings: Settings | None = None,
        jobs: AnalysisJobStore | None = None,
        telemetry: Telemetry | None = None,
        mode: str = "shadow",
        pool: asyncpg.Pool | None = None,
        pool_provider: PostgresPoolProvider | None = None,
    ) -> None:
        self._dsn = dsn
        self._settings = settings or Settings(postgres_dsn=dsn)
        self._pool = pool
        self._pool_provider = pool_provider
        self._owns_pool = pool is None and pool_provider is None
        self._analysis_state = PostgresAnalysisStateStore(
            dsn, pool=pool, pool_provider=pool_provider
        )
        self._jobs = jobs or PostgresAnalysisJobStore(dsn, pool=pool, pool_provider=pool_provider)
        self._topics = PostgresTopicCatalog(dsn, pool=pool, pool_provider=pool_provider)
        self._telemetry = telemetry
        self._mode = mode

    async def _enqueue_analysis(
        self,
        principal: RequestPrincipal,
        topic_ids: frozenset[str],
        scope: str,
        source_ids: tuple[str, ...],
        *,
        refresh: bool = False,
    ) -> AnalysisJob:
        topics = await self._topics.require_many(tuple(sorted(topic_ids)))
        topic_sources = {
            source_id for topic in topics.values() for source_id in topic.required_source_ids
        }
        configured_sources = self._settings.configured_coverage_source_ids
        if configured_sources:
            topic_sources &= configured_sources
        required_source_ids = tuple(sorted(set(source_ids) & topic_sources))
        input_revision: tuple[str, ...] = ()
        if refresh and required_source_ids:
            pool = await self._get_pool()
            rows = await pool.fetch(
                """
                SELECT DISTINCT ON (source_id)
                       source_id, observation_id, run_id
                FROM source_observation_v2
                WHERE source_id = ANY($1::text[])
                ORDER BY source_id, finished_at DESC, observation_id DESC
                """,
                list(required_source_ids),
            )
            revisions = [f"{row['source_id']}:{row['observation_id']}" for row in rows]
            run_ids = [row["run_id"] for row in rows]
            if run_ids:
                processing = await pool.fetch(
                    """
                    SELECT DISTINCT ingest.result_document_version_id,
                           COALESCE(job.state, 'queued') AS processing_state,
                           job.updated_at
                    FROM ingest_job_v2 ingest
                    LEFT JOIN document_processing_job_v4 job
                      ON job.document_version_id=ingest.result_document_version_id
                    WHERE ingest.run_id = ANY($1::uuid[])
                      AND ingest.status IN ('committed','not_novel')
                      AND ingest.result_document_version_id IS NOT NULL
                    ORDER BY ingest.result_document_version_id
                    """,
                    run_ids,
                )
                revisions.extend(
                    "document:"
                    f"{row['result_document_version_id']}:"
                    f"{row['processing_state']}:"
                    f"{row['updated_at'].isoformat() if row['updated_at'] else 'not-started'}"
                    for row in processing
                )
            input_revision = tuple(revisions)
        job = AnalysisJob.request(
            self._settings,
            principal,
            topic_ids,
            scope,
            required_source_ids,
            input_revision=input_revision,
        )
        if self._telemetry is None:
            return await self._jobs.enqueue(job)
        with self._telemetry.stage(PipelineStage.ANALYZE):
            return await self._jobs.enqueue(job)

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = (
                await self._pool_provider.get_pool()
                if self._pool_provider is not None
                else await asyncpg.create_pool(self._dsn, min_size=1, max_size=6)
            )
        return self._pool

    @staticmethod
    def _authorize(principal: RequestPrincipal) -> str:
        principal.require_role("analyst", "reviewer", "admin")
        principal.require_desk(STAGE_ONE_DESK)
        return principal.principal.principal_id

    async def _scope(self, principal: RequestPrincipal) -> tuple[str, tuple[str, ...]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT source_id FROM entitlement_grant
            WHERE entitlement_group = $1 ORDER BY source_id
            """,
            principal.principal.entitlement_group,
        )
        source_ids = tuple(str(row["source_id"]) for row in rows)
        return (
            signal_authorization_scope(
                principal.principal.entitlement_group,
                principal.principal.side.value,
                source_ids,
            ),
            source_ids,
        )

    async def _active_topics(self, principal_id: str) -> frozenset[str]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (subscription.topic_id)
                   subscription.topic_id, subscription.active
            FROM topic_subscription_transition_v3 subscription
            JOIN analysis_topic_v4 topic ON topic.topic_id = subscription.topic_id
            WHERE subscription.principal_id = $1 AND topic.active
            ORDER BY subscription.topic_id, subscription.occurred_at DESC,
                     subscription.transition_id DESC
            """,
            principal_id,
        )
        return frozenset(str(row["topic_id"]) for row in rows if row["active"])

    async def list_topics(self, principal: RequestPrincipal) -> list[TopicTagView]:
        principal_id = self._authorize(principal)
        active = await self._active_topics(principal_id)
        topics = await self._topics.active()
        return [
            TopicTagView(
                topic_id=topic.topic_id,
                label=topic.label,
                description=topic.description,
                subscribed=topic.topic_id in active,
            )
            for topic in topics
        ]

    async def list_subscriptions(self, principal: RequestPrincipal) -> list[TopicSubscriptionView]:
        principal_id = self._authorize(principal)
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (topic_id) topic_id, active, occurred_at
            FROM topic_subscription_transition_v3
            WHERE principal_id = $1
            ORDER BY topic_id, occurred_at DESC, transition_id DESC
            """,
            principal_id,
        )
        return [
            TopicSubscriptionView(
                topic_id=row["topic_id"],
                active=row["active"],
                updated_at=row["occurred_at"],
            )
            for row in rows
            if row["active"]
        ]

    async def update_subscription(
        self,
        principal: RequestPrincipal,
        topic_id: str,
        request: TopicSubscriptionUpdate,
    ) -> TopicSubscriptionView:
        principal_id = self._authorize(principal)
        try:
            await self._topics.require(topic_id)
        except KeyError as exc:
            raise ResourceNotFoundError(f"unknown topic {topic_id!r}") from exc
        now = datetime.now(UTC)
        transition_id = _stable_id(principal_id, topic_id, str(request.active), now.isoformat())
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO topic_subscription_transition_v3 (
                transition_id, principal_id, topic_id, active, occurred_at
            ) VALUES ($1,$2,$3,$4,$5)
            """,
            transition_id,
            principal_id,
            topic_id,
            request.active,
            now,
        )
        return TopicSubscriptionView(topic_id=topic_id, active=request.active, updated_at=now)

    async def get_topic_results(
        self,
        principal: RequestPrincipal,
        topic_id: str,
        *,
        refresh: bool = False,
    ) -> TopicResultsView:
        principal_id = self._authorize(principal)
        try:
            topic = await self._topics.require(topic_id)
        except KeyError as exc:
            raise ResourceNotFoundError(f"unknown topic {topic_id!r}") from exc
        active = await self._active_topics(principal_id)
        if topic_id not in active:
            raise AuthorizationError("subscribe to the topic before requesting its daily results")
        scope, source_ids = await self._scope(principal)
        pool = await self._get_pool()
        read_model = await pool.fetchrow(
            """
            SELECT read.*, job.temporal_pin, job.state AS job_state,
                   job.safe_error_summary, job.input_manifest
            FROM daily_topic_read_model_v4 read
            JOIN analysis_job_v4 job ON job.job_id = read.analysis_job_id
            WHERE read.topic_id = $1 AND read.authorization_scope = $2
            ORDER BY read.business_date DESC, read.materialized_at DESC LIMIT 1
            """,
            topic_id,
            scope,
        )
        # Every selection joins today's deterministic job. This remains a
        # bounded INSERT/SELECT and never starts work in the API process.
        queued_job = await self._enqueue_analysis(
            principal,
            frozenset({topic_id}),
            scope,
            source_ids,
            refresh=refresh,
        )
        if read_model is None:
            failed = queued_job.state in {
                AnalysisJobState.RETRYABLE_FAILED,
                AnalysisJobState.TERMINAL_FAILED,
            }
            return TopicResultsView(
                topic_id=topic_id,
                label=topic.label,
                analysis_status=queued_job.state.value,
                coverage_state="failed" if failed else "pending",
                as_of=queued_job.temporal_pin,
                message=(
                    "Canonical analysis failed; its durable job remains inspectable."
                    if failed
                    else "Source data is ready; document extraction and indexing are finishing."
                    if queued_job.state is AnalysisJobState.DEFERRED
                    else "Canonical analysis is queued for an independent worker."
                ),
                mode=self._mode,
                scope_notice=(
                    queued_job.safe_error_summary
                    or "The page joined the shared durable daily analysis job."
                ),
                analysis_job_id=queued_job.job_id,
                business_date=queued_job.business_date.isoformat(),
            )
        result_ids = tuple(str(item) for item in read_model["ordered_result_version_ids"])
        lifecycle_raw = _json_object(read_model["result_lifecycle"])
        rows = await pool.fetch(
            """
            SELECT result_version_id, manifest, created_at
            FROM result_version_v3
            WHERE result_version_id = ANY($1::text[])
              AND manifest->>'topic_id' = $2
              AND manifest->>'authorization_scope' = $3
              AND publication_state = 'publish'
            ORDER BY array_position($1::text[], result_version_id)
            """,
            list(result_ids),
            topic_id,
            scope,
        )
        results: list[OpportunityResultView] = []
        for row in rows:
            manifest = ImmutableResultManifest.model_validate(_json_object(row["manifest"]))
            exposure_id = await self._analysis_state.record_exposure(
                row["result_version_id"], principal_id, "stage_one_web", datetime.now(UTC)
            )
            latest = await pool.fetchval(
                """
                SELECT verdict FROM result_evaluation_v3
                WHERE exposure_id = $1
                ORDER BY recorded_at DESC, evaluation_id DESC LIMIT 1
                """,
                exposure_id,
            )
            result_id = str(row["result_version_id"])
            results.append(
                self._view(
                    manifest,
                    result_id,
                    latest,
                    lifecycle_state=str(
                        lifecycle_raw.get(result_id, manifest.change_classification.value)
                    ),
                )
            )
        coverage = _json_object(read_model["coverage_summary"])
        required = tuple(str(item) for item in _json_items(coverage.get("required_source_ids")))
        required_documents = tuple(
            str(item)
            for item in _json_items(coverage.get("required_document_version_ids"))
        )
        completed = frozenset(
            str(item) for item in _json_items(coverage.get("completed_source_ids"))
        )
        complete = bool(coverage.get("complete", False))
        source_rows = await pool.fetch(
            """
            SELECT registry.source_id, registry.display_name,
                   observation.health, observation.complete, observation.fresh,
                   observation.silent, observation.within_expected_volume,
                   observation.finished_at, observation.committed_count,
                   observation.quarantine_count, observation.error_type,
                   observation.error_message
            FROM source_registry registry
            LEFT JOIN LATERAL (
                SELECT health, complete, fresh, silent, within_expected_volume,
                       finished_at, committed_count, quarantine_count,
                       error_type, error_message
                FROM source_observation_v2
                WHERE source_id = registry.source_id AND finished_at <= $2
                ORDER BY finished_at DESC, observation_id DESC LIMIT 1
            ) observation ON TRUE
            WHERE registry.source_id = ANY($1::text[])
            ORDER BY registry.source_id
            """,
            list(required),
            read_model["temporal_pin"],
        )
        document_count_rows = await pool.fetch(
            """
            SELECT identity.source_id, count(*) AS document_count
            FROM document_identity identity
            WHERE identity.source_id = ANY($1::text[])
              AND EXISTS (
                SELECT 1 FROM document_version version
                WHERE version.document_id=identity.document_id
                  AND version.recorded_at <= $2
              )
            GROUP BY identity.source_id
            """,
            list(required),
            read_model["temporal_pin"],
        )
        document_counts = {
            str(row["source_id"]): int(row["document_count"])
            for row in document_count_rows
        }
        source_observations = {str(row["source_id"]): row for row in source_rows}
        source_matrix = {source.source_id: source for source in GCC_OFFICIAL_SOURCES}
        coverage_reasons = tuple(str(item) for item in _json_items(coverage.get("reasons")))
        latest_source_time = read_model["latest_source_time"]

        def source_status(source_id: str) -> LiveSourceStatusView:
            observation = source_observations.get(source_id)
            source_complete = source_id in completed
            if source_complete:
                status = "complete"
                detail = "Completed in the canonical analysis run."
            elif observation is not None and observation["health"] == "failed":
                status = "fetch_failed"
                detail = (
                    f"Fetch failed: {observation['error_type'] or 'source error'}; "
                    f"{observation['error_message'] or 'no safe error summary'}"
                )
            elif observation is None or observation["health"] is None:
                status = "incomplete"
                detail = "No source observation completed before the analysis pin."
            else:
                status = "incomplete"
                detail = next(
                    (reason for reason in coverage_reasons if source_id in reason),
                    "Required source work did not complete freshness and volume checks.",
                )
            return LiveSourceStatusView(
                source_id=source_id,
                display_name=(
                    str(observation["display_name"]) if observation is not None else source_id
                ),
                country=(
                    source_matrix[source_id].country if source_id in source_matrix else "Unknown"
                ),
                source_type=(
                    source_matrix[source_id].source_type
                    if source_id in source_matrix
                    else "registered_authorized_source"
                ),
                source_url=(source_matrix[source_id].url if source_id in source_matrix else ""),
                status=status,
                fetched_at=(
                    observation["finished_at"] if observation is not None else latest_source_time
                ),
                candidate_count=document_counts.get(source_id, 0),
                rejected_candidate_count=(
                    int(observation["quarantine_count"] or 0) if observation is not None else 0
                ),
                detail=detail,
            )

        statuses = tuple(source_status(source_id) for source_id in required)
        message = str(read_model["safe_message"])
        result_model_names = {
            lineage.model_id
            for row in rows
            for lineage in ImmutableResultManifest.model_validate(
                _json_object(row["manifest"])
            ).model_lineages
        }
        model_usage_rows = await pool.fetch(
            """
            SELECT model, status, count(*) AS call_count
            FROM model_call_log
            WHERE ($1::text IS NOT NULL AND run_id=$1)
               OR subject_id = ANY($2::text[])
            GROUP BY model, status
            ORDER BY model, status
            """,
            str(read_model["run_id"]) if read_model["run_id"] else None,
            list(required_documents),
        )
        model_names = sorted(
            result_model_names | {str(row["model"]) for row in model_usage_rows}
        )
        model_call_count = sum(int(row["call_count"]) for row in model_usage_rows)
        model_failure_count = sum(
            int(row["call_count"])
            for row in model_usage_rows
            if str(row["status"]) != "succeeded"
        )
        job_state = str(read_model["job_state"])
        analysis_status = job_state
        scope_notice = (
            "Canonical authorized source-to-result analysis pipeline."
            if complete
            else "Incomplete canonical coverage: " + "; ".join(coverage_reasons)
        )
        if queued_job.state not in {
            AnalysisJobState.COMPLETE,
            AnalysisJobState.PARTIAL,
            AnalysisJobState.HELD,
        }:
            analysis_status = queued_job.state.value
            message = (
                "Source data is ready; document extraction and indexing are finishing."
                if queued_job.state is AnalysisJobState.DEFERRED
                else "A durable refresh is queued; showing the last immutable result set."
            )
            scope_notice = (
                queued_job.safe_error_summary
                or "The page joined the shared durable daily analysis job."
            )
        counts_raw = _json_object(read_model["lifecycle_counts"])
        lifecycle_counts = {
            str(key): _json_integer(value, key=str(key)) for key, value in counts_raw.items()
        }
        return TopicResultsView(
            topic_id=topic_id,
            label=topic.label,
            analysis_status=analysis_status,
            coverage_state="complete" if complete else "incomplete",
            as_of=read_model["temporal_pin"],
            message=message,
            mode=self._mode,
            scope_notice=scope_notice,
            model_name=", ".join(model_names) or None,
            model_call_count=model_call_count,
            model_failure_count=model_failure_count,
            run_id=(str(read_model["run_id"]) if read_model["run_id"] else None),
            analysis_job_id=str(read_model["analysis_job_id"]),
            business_date=read_model["business_date"].isoformat(),
            lifecycle_counts=lifecycle_counts,
            required_source_count=len(required),
            successful_source_count=len(completed & set(required)),
            rejected_candidate_count=sum(item.rejected_candidate_count for item in statuses),
            source_statuses=statuses,
            results=tuple(sorted(results, key=lambda item: item.score, reverse=True)),
        )

    @staticmethod
    def _view(
        manifest: ImmutableResultManifest,
        result_version_id: str,
        latest_evaluation: str | None,
        *,
        lifecycle_state: str,
    ) -> OpportunityResultView:
        opportunity = manifest.opportunity
        claims_by_kind: dict[str, list[str]] = {}
        for claim in opportunity.claims:
            claims_by_kind.setdefault(claim.claim_type.value, []).append(claim.text)
        versions = {item.source_id: item for item in manifest.source_versions}

        def source_url(source_id: str, explicit: str | None) -> str | None:
            version = versions.get(source_id)
            return explicit or (version.url if version is not None else None)

        evidence = tuple(
            OpportunityEvidenceView(
                evidence_id=item.evidence_id,
                title=item.doc_id,
                quote=item.excerpt,
                source_id=item.source_id,
                source_url=source_url(item.source_id, item.source_url),
                content_hash=item.content_hash
                or (versions[item.source_id].content_hash if item.source_id in versions else None),
            )
            for item in manifest.evidence
        )
        return OpportunityResultView(
            result_id=result_version_id,
            topic_id=manifest.topic_id,
            title=opportunity.title,
            entity_name=next(
                (
                    mapping.value
                    for claim in opportunity.claims
                    for mapping in claim.field_evidence
                    if mapping.field_name == "entity"
                ),
                manifest.entity_id,
            ),
            summary=opportunity.summary,
            freshness_reason="; ".join(claims_by_kind.get("timing", ()))
            or "New or changed in the canonical analysis run.",
            lifecycle_state=lifecycle_state,
            score=manifest.triage_score or 0.0,
            as_of=manifest.as_of,
            changed_at=manifest.investigation.updated_at,
            coverage_state="complete",
            falsifier=opportunity.falsifier,
            why_now=" ".join(claims_by_kind.get("timing", ())),
            commercial_angle=" ".join(claims_by_kind.get("commercial_angle", ())),
            materiality=" ".join(claims_by_kind.get("materiality", ())),
            contradictions=tuple(claims_by_kind.get("contradiction", ())),
            uncertainty=opportunity.uncertainty_category,
            coverage_details="; ".join(manifest.coverage.reasons)
            or "Required operational and factual coverage passed.",
            change_summary=manifest.change_classification.value,
            investigation_trace=tuple(
                {
                    "operation": step.operation,
                    "status": step.status.value,
                    "reason": step.safe_error_summary or "completed within policy",
                }
                for step in manifest.investigation.steps
            ),
            evidence=evidence,
            latest_evaluation=latest_evaluation,
        )

    async def evaluate_result(
        self,
        principal: RequestPrincipal,
        result_id: str,
        request: ResultEvaluationRequest,
    ) -> ResultEvaluationReceipt:
        principal_id = self._authorize(principal)
        active = await self._active_topics(principal_id)
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT exposure.exposure_id, result.manifest->>'topic_id' AS topic_id
            FROM result_exposure_v3 exposure
            JOIN result_version_v3 result USING (result_version_id)
            WHERE exposure.result_version_id = $1 AND exposure.principal_id = $2
              AND exposure.channel = 'stage_one_web'
            ORDER BY exposure.exposed_at DESC LIMIT 1
            """,
            result_id,
            principal_id,
        )
        if row is None or row["topic_id"] not in active:
            raise ResourceNotFoundError(f"unknown or unexposed result {result_id!r}")
        recorded_at = datetime.now(UTC)
        evaluation_id = await self._analysis_state.record_evaluation(
            row["exposure_id"],
            EvaluationVerdict(request.verdict.value),
            request.note,
            principal_id,
            recorded_at,
        )
        return ResultEvaluationReceipt(
            evaluation_id=evaluation_id,
            result_id=result_id,
            verdict=request.verdict,
            recorded_at=recorded_at,
        )

    async def create_search(
        self, principal: RequestPrincipal, request: SearchCreateRequest
    ) -> SearchView:
        self._authorize(principal)
        scope, _ = await self._scope(principal)
        store = PostgresSearchStore(self._settings, pool=await self._get_pool())
        job = await store.enqueue(
            PrincipalSnapshot.from_principal(principal),
            scope,
            request.query,
            request.seed_entity_ids,
        )
        return SearchView(
            search_id=job.search_id,
            state=job.state.value,
            route=job.plan.route.value,
            query=job.query_text,
            temporal_pin=job.temporal_pin,
            answer=job.answer,
            safe_error_summary=job.safe_error_summary,
        )

    async def get_search(self, principal: RequestPrincipal, search_id: str) -> SearchView:
        principal_id = self._authorize(principal)
        scope, _ = await self._scope(principal)
        store = PostgresSearchStore(self._settings, pool=await self._get_pool())
        job = await store.get(search_id)
        if (
            job is None
            or job.principal.principal_id != principal_id
            or job.authorization_scope != scope
        ):
            raise ResourceNotFoundError(f"unknown search {search_id!r}")
        return SearchView(
            search_id=job.search_id,
            state=job.state.value,
            route=job.plan.route.value,
            query=job.query_text,
            temporal_pin=job.temporal_pin,
            answer=job.answer,
            safe_error_summary=job.safe_error_summary,
        )

    async def close(self) -> None:
        errors: list[Exception] = []
        for resource in (self._analysis_state, self._jobs, self._topics):
            try:
                await resource.close()
            except Exception as exc:  # pragma: no cover - shutdown best effort
                errors.append(exc)
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None
        if errors:
            raise ExceptionGroup("Stage One service shutdown failed", errors)


__all__ = ["PostgresStageOneService", "STAGE_ONE_DESK"]
