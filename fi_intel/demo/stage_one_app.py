"""Runnable, service-free Stage 1 subscription product demonstration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI

from fi_intel.api.app import create_app
from fi_intel.api.auth import (
    AuthenticationError,
    Authenticator,
    AuthorizationError,
    RequestPrincipal,
    VerifiedToken,
)
from fi_intel.api.models import (
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
from fi_intel.api.service import InMemoryAnalystService, ResourceNotFoundError
from fi_intel.api.stage_one_page import STAGE_ONE_FIXTURE_HTML, STAGE_ONE_FIXTURE_JS
from fi_intel.application.operations import (
    PipelineStageRuntimeView,
    RuntimeDashboardView,
    RuntimeEventView,
    RuntimeQueueStatus,
)
from fi_intel.application.topics import (
    TOPICS as _TOPICS,
    TOPICS_BY_ID as _TOPICS_BY_ID,
    TOPIC_BY_PATTERN as _TOPIC_BY_PATTERN,
    TopicDefinition as _TopicDefinition,
)
from fi_intel.demo.runner import POCDemoArtifacts, run_poc_demo
from fi_intel.retrieval.entitlement import Principal, Side

DEMO_TOKEN = "stage-one-demo"  # noqa: S105 - fixed credential for localhost fixture app only
DEMO_PRINCIPAL_ID = "stage-one-demo-analyst"
DEMO_DESK = "fi_gcc"
FIXTURE_NOTICE = "Synthetic deterministic fixture - not a production quality or coverage estimate."


_FRESHNESS_REASONS = {
    "maturity_wall_no_refi": (
        "New in the selected analysis window: the maturity is inside the governed horizon and no "
        "refinancing announcement was found in the fixture evidence."
    ),
    "board_approved_issuance_programme": (
        "New in the selected analysis window: an approved programme has documented capacity and "
        "is not yet marked as marketed."
    ),
    "negative_rating_action_with_capital_decline": (
        "New in the selected analysis window: the rating and capital facts jointly crossed the "
        "topic's materiality threshold."
    ),
    "leadership_change_treasury": (
        "Detected in the selected analysis window, but only results above the visible triage "
        "threshold are surfaced."
    ),
}


class _DemoTokenVerifier:
    async def verify(self, credential: str) -> VerifiedToken:
        if credential != DEMO_TOKEN:
            raise AuthenticationError("invalid Stage 1 demo token")
        return VerifiedToken(subject=DEMO_PRINCIPAL_ID, issuer="fi-intel-stage-one-local")


class _DemoIdentityDirectory:
    def __init__(
        self,
        *,
        entitlement_group: str = "poc-fixture-public",
        side: Side | str = Side.PUBLIC,
    ) -> None:
        self._entitlement_group = entitlement_group
        self._side = Side(side)

    async def resolve(self, subject: str) -> RequestPrincipal | None:
        if subject != DEMO_PRINCIPAL_ID:
            return None
        return RequestPrincipal(
            subject=subject,
            principal=Principal(
                principal_id=DEMO_PRINCIPAL_ID,
                entitlement_group=self._entitlement_group,
                side=self._side,
            ),
            desks=frozenset({DEMO_DESK}),
            roles=frozenset({"analyst"}),
            purposes=frozenset({"market_intelligence"}),
        )

    async def close(self) -> None:
        return None


class StageOneDemoService:
    """In-memory subscriptions and results backed by the packaged POC analysis."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, dict[str, datetime]] = {}
        self._analysis_lock = asyncio.Lock()
        self._artifacts: POCDemoArtifacts | None = None
        self._results_by_topic: dict[str, tuple[OpportunityResultView, ...]] = {}
        self._results_by_id: dict[str, OpportunityResultView] = {}
        self._latest_evaluations: dict[tuple[str, str], str] = {}
        self._searches: dict[str, SearchView] = {}
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

    async def list_subscriptions(self, principal: RequestPrincipal) -> list[TopicSubscriptionView]:
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
        self, principal: RequestPrincipal, topic_id: str, *, refresh: bool = False
    ) -> TopicResultsView:
        del refresh
        principal_id = self._authorize(principal)
        topic = self._topic(topic_id)
        if topic_id not in self._subscriptions.get(principal_id, {}):
            raise AuthorizationError("subscribe to the topic before requesting its daily results")
        await self._ensure_analysis()
        artifacts = self._artifacts
        if artifacts is None:  # pragma: no cover - guarded by _ensure_analysis
            raise RuntimeError("Stage 1 fixture analysis did not initialize")
        results = tuple(
            result.model_copy(
                update={
                    "latest_evaluation": self._latest_evaluations.get(
                        (principal_id, result.result_id)
                    )
                }
            )
            for result in self._results_by_topic.get(topic_id, ())
        )
        coverage_state = "complete" if artifacts.report.brief.coverage_complete else "incomplete"
        opportunity_label = "opportunity" if len(results) == 1 else "opportunities"
        message = (
            f"{len(results)} fresh {opportunity_label} found"
            if results
            else "Analysis complete - nothing new"
        )
        return TopicResultsView(
            topic_id=topic_id,
            label=topic.label,
            analysis_status="complete",
            coverage_state=coverage_state,
            as_of=artifacts.report.as_of,
            message=message,
            mode="fixture",
            scope_notice=FIXTURE_NOTICE,
            results=results,
        )

    async def evaluate_result(
        self,
        principal: RequestPrincipal,
        result_id: str,
        request: ResultEvaluationRequest,
    ) -> ResultEvaluationReceipt:
        principal_id = self._authorize(principal)
        await self._ensure_analysis()
        result = self._results_by_id.get(result_id)
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

    async def create_search(
        self, principal: RequestPrincipal, request: SearchCreateRequest
    ) -> SearchView:
        self._authorize(principal)
        now = datetime.now(UTC)
        search_id = str(uuid4())
        search = SearchView(
            search_id=search_id,
            state="complete",
            route="thematic",
            query=request.query,
            temporal_pin=now,
            answer={
                "summary": "Synthetic fixture search only; no current-market claim is made.",
                "claims": [],
                "citations": [],
                "unknowns": ["The fixture search is not connected to live sources."],
            },
        )
        self._searches[search_id] = search
        return search

    async def get_search(self, principal: RequestPrincipal, search_id: str) -> SearchView:
        self._authorize(principal)
        search = self._searches.get(search_id)
        if search is None:
            raise ResourceNotFoundError(f"unknown search {search_id!r}")
        return search

    async def operations_dashboard(
        self,
        principal: RequestPrincipal,
        *,
        event_limit: int = 200,
    ) -> RuntimeDashboardView:
        self._authorize(principal)
        del event_limit
        now = datetime.now(UTC)
        return RuntimeDashboardView(
            generated_at=now,
            overall_status="fixture",
            queue=RuntimeQueueStatus(
                analysis_jobs={},
                search_jobs={},
                document_jobs={},
                outbox_pending=0,
                dead_letters=0,
                deliveries={},
                retrieval_index_status="fixture",
                indexed_document_versions=0,
                unindexed_document_versions=0,
                embedding_calls_last_hour={},
            ),
            workers=(),
            stages=(
                PipelineStageRuntimeView(
                    stage="fixture",
                    label="Synthetic fixture pipeline",
                    status="complete",
                    detail="No live workers, sources, or model gateways are used in fixture mode.",
                    completed=1,
                    last_activity_at=now,
                ),
            ),
            sources=(),
            models=(),
            events=(
                RuntimeEventView(
                    event_id="fixture-runtime",
                    occurred_at=now,
                    stage="fixture",
                    operation="fixture analysis",
                    status="succeeded",
                    message="Synthetic fixture dashboard initialized.",
                ),
            ),
        )

    async def _ensure_analysis(self) -> None:
        if self._artifacts is not None:
            return
        async with self._analysis_lock:
            if self._artifacts is not None:
                return
            artifacts = await run_poc_demo()
            documents = {document.doc_id: document for document in artifacts.documents}
            results_by_topic: dict[str, list[OpportunityResultView]] = {
                topic.topic_id: [] for topic in _TOPICS
            }
            results_by_id: dict[str, OpportunityResultView] = {}
            coverage_state = (
                "complete" if artifacts.report.brief.coverage_complete else "incomplete"
            )
            for item in artifacts.report.brief.items:
                topic_id = _TOPIC_BY_PATTERN.get(item.signal.pattern)
                if topic_id is None:
                    continue
                evidence = tuple(
                    OpportunityEvidenceView(
                        evidence_id=evidence_item.evidence_id,
                        title=(
                            documents[evidence_item.doc_id].title
                            if evidence_item.doc_id in documents
                            else evidence_item.doc_id
                        ),
                        quote=evidence_item.excerpt,
                        source_id=evidence_item.source_id,
                        source_url=evidence_item.source_url,
                        published_at=(
                            documents[evidence_item.doc_id].published_at
                            if evidence_item.doc_id in documents
                            else None
                        ),
                    )
                    for evidence_item in item.evidence
                )
                result = OpportunityResultView(
                    result_id=item.signal.signal_id,
                    topic_id=topic_id,
                    title=item.opportunity.title,
                    entity_name=item.signal.entity_name,
                    summary=item.opportunity.summary,
                    freshness_reason=_FRESHNESS_REASONS[item.signal.pattern],
                    lifecycle_state="new",
                    score=item.signal.opportunity_score,
                    as_of=item.signal.as_of,
                    changed_at=item.signal.updated_at or item.signal.as_of,
                    coverage_state=coverage_state,
                    falsifier=item.opportunity.falsifier,
                    why_now=next(
                        (
                            claim.text
                            for claim in item.opportunity.claims
                            if claim.claim_type.value == "timing"
                        ),
                        _FRESHNESS_REASONS[item.signal.pattern],
                    ),
                    commercial_angle=next(
                        (
                            claim.text
                            for claim in item.opportunity.claims
                            if claim.claim_type.value == "commercial_angle"
                        ),
                        "",
                    ),
                    materiality=next(
                        (
                            claim.text
                            for claim in item.opportunity.claims
                            if claim.claim_type.value == "materiality"
                        ),
                        "",
                    ),
                    contradictions=tuple(
                        claim.text
                        for claim in item.opportunity.claims
                        if claim.claim_type.value == "contradiction"
                    ),
                    uncertainty=item.opportunity.uncertainty_category,
                    coverage_details=(
                        "Complete within the declared fixture scope"
                        if artifacts.report.brief.coverage_complete
                        else "Incomplete; no absence conclusion is permitted"
                    ),
                    change_summary=item.signal.lifecycle_state.value,
                    investigation_trace=(
                        tuple(
                            {
                                "operation": step.operation,
                                "status": step.status.value,
                                "reason": step.safe_error_summary or "completed within policy",
                            }
                            for step in item.investigation.steps
                        )
                        if item.investigation is not None
                        else ()
                    ),
                    evidence=evidence,
                )
                results_by_topic[topic_id].append(result)
                results_by_id[result.result_id] = result
            self._artifacts = artifacts
            self._results_by_topic = {
                topic_id: tuple(sorted(results, key=lambda item: item.score, reverse=True))
                for topic_id, results in results_by_topic.items()
            }
            self._results_by_id = results_by_id


def create_stage_one_demo_app() -> FastAPI:
    """Uvicorn factory for the localhost-only synthetic Stage 1 demo."""

    directory = _DemoIdentityDirectory()
    return create_app(
        Authenticator(_DemoTokenVerifier(), directory),
        InMemoryAnalystService(),
        stage_one_service=StageOneDemoService(),
        stage_one_html=STAGE_ONE_FIXTURE_HTML,
        stage_one_javascript=STAGE_ONE_FIXTURE_JS,
        canonical_stage_one_only=True,
        owned_resources=(directory,),
    )
