"""Local Stage 1 app backed by live GCC sources and a real configured LLM."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI

from fi_intel.api.app import create_app
from fi_intel.api.auth import Authenticator, AuthorizationError, RequestPrincipal
from fi_intel.api.models import (
    LiveSourceStatusView,
    ResultEvaluationReceipt,
    ResultEvaluationRequest,
    TopicResultsView,
    TopicSubscriptionUpdate,
    TopicSubscriptionView,
    TopicTagView,
)
from fi_intel.api.service import InMemoryAnalystService, ResourceNotFoundError
from fi_intel.config import Settings
from fi_intel.demo.gcc_live import (
    GCC_LIVE_SOURCES,
    LiveGccAnalysisRunner,
    LiveGccRun,
    OfficialGccSourceReader,
    OpenAICompatibleLiveOpportunityModel,
    live_demo_configuration_errors,
)
from fi_intel.demo.stage_one_app import (
    _TOPICS,
    _TOPICS_BY_ID,
    DEMO_DESK,
    _DemoIdentityDirectory,
    _DemoTokenVerifier,
    _TopicDefinition,
)
from fi_intel.governance.model_usage import InMemoryModelUsageLog

LIVE_SCOPE_NOTICE = (
    "Live official public-source POC covering two registered regulator/market pages in each of "
    "the six GCC countries. It does not yet include the production issuer-IR universe, licensed "
    "news, or licensed rating-agency feeds."
)


class StageOneLiveService:
    """In-memory subscriptions over a cached, source-ledgered live analysis run."""

    def __init__(self, settings: Settings, runner: LiveGccAnalysisRunner) -> None:
        self._settings = settings
        self._runner = runner
        self._subscriptions: dict[str, dict[str, datetime]] = {}
        self._analysis_lock = asyncio.Lock()
        self._run: LiveGccRun | None = None
        self._latest_evaluations: dict[tuple[str, str], str] = {}
        self.evaluation_events: list[ResultEvaluationReceipt] = []

    @staticmethod
    def _authorize(principal: RequestPrincipal) -> str:
        principal.require_role("analyst", "reviewer", "admin")
        principal.require_desk(DEMO_DESK)
        return principal.principal.principal_id

    @staticmethod
    def _topic(topic_id: str) -> _TopicDefinition:
        topic = _TOPICS_BY_ID.get(topic_id)
        if topic is None:
            raise ResourceNotFoundError(f"unknown topic {topic_id!r}")
        return topic

    async def list_topics(self, principal: RequestPrincipal) -> list[TopicTagView]:
        principal_id = self._authorize(principal)
        active = self._subscriptions.get(principal_id, {})
        return [
            TopicTagView(
                topic_id=topic.topic_id,
                label=topic.label,
                description=topic.description,
                subscribed=topic.topic_id in active,
            )
            for topic in _TOPICS
        ]

    async def list_subscriptions(
        self, principal: RequestPrincipal
    ) -> list[TopicSubscriptionView]:
        principal_id = self._authorize(principal)
        return [
            TopicSubscriptionView(topic_id=topic_id, active=True, updated_at=updated_at)
            for topic_id, updated_at in sorted(self._subscriptions.get(principal_id, {}).items())
        ]

    async def update_subscription(
        self,
        principal: RequestPrincipal,
        topic_id: str,
        request: TopicSubscriptionUpdate,
    ) -> TopicSubscriptionView:
        principal_id = self._authorize(principal)
        self._topic(topic_id)
        now = datetime.now(UTC)
        subscriptions = self._subscriptions.setdefault(principal_id, {})
        if request.active:
            subscriptions[topic_id] = now
        else:
            subscriptions.pop(topic_id, None)
        return TopicSubscriptionView(topic_id=topic_id, active=request.active, updated_at=now)

    async def get_topic_results(
        self,
        principal: RequestPrincipal,
        topic_id: str,
        *,
        refresh: bool = False,
    ) -> TopicResultsView:
        principal_id = self._authorize(principal)
        topic = self._topic(topic_id)
        if topic_id not in self._subscriptions.get(principal_id, {}):
            raise AuthorizationError("subscribe to the topic before requesting its daily results")
        run = await self._ensure_analysis(refresh=refresh)
        results = tuple(
            result.model_copy(
                update={
                    "latest_evaluation": self._latest_evaluations.get(
                        (principal_id, result.result_id)
                    )
                }
            )
            for result in run.results_by_topic.get(topic_id, ())
        )
        coverage_state = "complete" if run.coverage_complete else "incomplete"
        if results:
            noun = "opportunity" if len(results) == 1 else "opportunities"
            message = f"{len(results)} live {noun} found"
            if not run.coverage_complete:
                message += " - coverage incomplete"
        elif run.coverage_complete:
            message = "Analysis complete - nothing new in the registered POC scope"
        else:
            message = "Analysis incomplete - no absence claim can be made"
        source_statuses = tuple(
            LiveSourceStatusView(
                source_id=status.source.source_id,
                display_name=status.source.display_name,
                country=status.source.country,
                source_type=status.source.source_type,
                source_url=status.source.url,
                status=status.status,
                fetched_at=status.fetched_at,
                content_hash=status.content_hash,
                candidate_count=status.candidate_count,
                rejected_candidate_count=status.rejected_candidate_count,
                detail=status.detail,
            )
            for status in run.source_statuses
        )
        return TopicResultsView(
            topic_id=topic_id,
            label=topic.label,
            analysis_status="complete" if run.coverage_complete else "partial",
            coverage_state=coverage_state,
            as_of=run.as_of,
            message=message,
            mode="live",
            scope_notice=LIVE_SCOPE_NOTICE,
            model_name=run.model_name,
            run_id=run.run_id,
            required_source_count=len(run.source_statuses),
            successful_source_count=sum(
                status.status == "complete" for status in run.source_statuses
            ),
            rejected_candidate_count=run.rejected_candidate_count,
            source_statuses=source_statuses,
            results=results,
        )

    async def evaluate_result(
        self,
        principal: RequestPrincipal,
        result_id: str,
        request: ResultEvaluationRequest,
    ) -> ResultEvaluationReceipt:
        principal_id = self._authorize(principal)
        run = await self._ensure_analysis(refresh=False)
        result = next(
            (
                item
                for results in run.results_by_topic.values()
                for item in results
                if item.result_id == result_id
            ),
            None,
        )
        if result is None:
            raise ResourceNotFoundError(f"unknown result {result_id!r}")
        if result.topic_id not in self._subscriptions.get(principal_id, {}):
            raise AuthorizationError("subscribe to the result topic before evaluating it")
        receipt = ResultEvaluationReceipt(
            evaluation_id=str(uuid4()),
            result_id=result_id,
            verdict=request.verdict,
            recorded_at=datetime.now(UTC),
        )
        self.evaluation_events.append(receipt)
        self._latest_evaluations[(principal_id, result_id)] = request.verdict.value
        return receipt

    async def _ensure_analysis(self, *, refresh: bool) -> LiveGccRun:
        current = self._run
        now = datetime.now(UTC)
        fresh_after = now - timedelta(seconds=self._settings.gcc_live_cache_seconds)
        if current is not None and not refresh and current.as_of >= fresh_after:
            return current
        async with self._analysis_lock:
            current = self._run
            if current is not None and not refresh and current.as_of >= fresh_after:
                return current
            self._run = await self._runner.run()
            return self._run

    async def close(self) -> None:
        await self._runner.close()


def create_stage_one_live_app() -> FastAPI:
    """Uvicorn factory that refuses to impersonate a live demo when unconfigured."""

    settings = Settings()
    errors = live_demo_configuration_errors(settings)
    if errors:
        joined = "; ".join(errors)
        raise RuntimeError(f"Live Stage 1 demo is not configured: {joined}")
    directory = _DemoIdentityDirectory()
    usage_log = InMemoryModelUsageLog()
    model = OpenAICompatibleLiveOpportunityModel(settings, usage_log)
    runner = LiveGccAnalysisRunner(settings, OfficialGccSourceReader(settings), model)
    stage_one_service = StageOneLiveService(settings, runner)
    return create_app(
        Authenticator(_DemoTokenVerifier(), directory),
        InMemoryAnalystService(),
        stage_one_service=stage_one_service,
        owned_resources=(directory, stage_one_service),
    )


def live_source_country_count() -> int:
    """Expose matrix breadth for the CLI banner and tests."""

    return len({source.country for source in GCC_LIVE_SOURCES})
