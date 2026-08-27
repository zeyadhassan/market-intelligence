"""Application port used by the authenticated HTTP boundary."""

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import uuid4

from fi_intel.api.auth import RequestPrincipal
from fi_intel.api.models import (
    BriefPublicationRequest,
    BriefRequest,
    BriefView,
    EntityView,
    EvidenceSpanView,
    FeedbackReceipt,
    FeedbackRequest,
    ResultEvaluationReceipt,
    ResultEvaluationRequest,
    ReviewDecisionRequest,
    ReviewReceipt,
    RunView,
    SearchCreateRequest,
    SearchView,
    SignalCloseReceipt,
    SignalCloseRequest,
    SignalView,
    TopicResultsView,
    TopicSubscriptionUpdate,
    TopicSubscriptionView,
    TopicTagView,
)


class ResourceNotFoundError(LookupError):
    pass


class PublicationNotReadyError(RuntimeError):
    """Server-computed run coverage does not permit publication."""


@runtime_checkable
class AnalystService(Protocol):
    async def list_signals(
        self,
        principal: RequestPrincipal,
        *,
        desk: str,
        status: str | None,
        limit: int,
    ) -> list[SignalView]: ...

    async def get_signal(self, principal: RequestPrincipal, signal_id: str) -> SignalView: ...

    async def submit_feedback(
        self,
        principal: RequestPrincipal,
        signal_id: str,
        request: FeedbackRequest,
    ) -> FeedbackReceipt: ...

    async def close_signal(
        self,
        principal: RequestPrincipal,
        signal_id: str,
        request: SignalCloseRequest,
    ) -> SignalCloseReceipt: ...

    async def get_entity(self, principal: RequestPrincipal, entity_id: str) -> EntityView: ...

    async def get_evidence(
        self, principal: RequestPrincipal, evidence_span_id: str
    ) -> EvidenceSpanView: ...

    async def decide_review(
        self,
        principal: RequestPrincipal,
        subject_type: str,
        subject_id: str,
        request: ReviewDecisionRequest,
    ) -> ReviewReceipt: ...

    async def request_brief(
        self, principal: RequestPrincipal, request: BriefRequest
    ) -> BriefView: ...

    async def get_brief(self, principal: RequestPrincipal, brief_id: str) -> BriefView: ...

    async def publish_brief(
        self,
        principal: RequestPrincipal,
        brief_id: str,
        request: BriefPublicationRequest,
    ) -> BriefView: ...

    async def get_run(self, principal: RequestPrincipal, run_id: str) -> RunView: ...

    async def ready(self) -> bool: ...

    async def close(self) -> None: ...


@runtime_checkable
class StageOneService(Protocol):
    """Subscription-first product port used by the Stage 1 page."""

    async def list_topics(self, principal: RequestPrincipal) -> list[TopicTagView]: ...

    async def list_subscriptions(
        self, principal: RequestPrincipal
    ) -> list[TopicSubscriptionView]: ...

    async def update_subscription(
        self,
        principal: RequestPrincipal,
        topic_id: str,
        request: TopicSubscriptionUpdate,
    ) -> TopicSubscriptionView: ...

    async def get_topic_results(
        self, principal: RequestPrincipal, topic_id: str, *, refresh: bool = False
    ) -> TopicResultsView: ...

    async def evaluate_result(
        self,
        principal: RequestPrincipal,
        result_id: str,
        request: ResultEvaluationRequest,
    ) -> ResultEvaluationReceipt: ...

    async def create_search(
        self, principal: RequestPrincipal, request: SearchCreateRequest
    ) -> SearchView: ...

    async def get_search(self, principal: RequestPrincipal, search_id: str) -> SearchView: ...


class InMemoryAnalystService:
    """Policy-aware reference service for contract tests and local demos."""

    def __init__(
        self,
        *,
        signals: tuple[SignalView, ...] = (),
        entities: tuple[EntityView, ...] = (),
        evidence: tuple[EvidenceSpanView, ...] = (),
        runs: tuple[RunView, ...] = (),
    ) -> None:
        self.signals = {item.signal_id: item for item in signals}
        self.entities = {item.entity_id: item for item in entities}
        self.evidence = {item.evidence_span_id: item for item in evidence}
        self.runs = {item.run_id: item for item in runs}
        self.briefs: dict[str, BriefView] = {}
        self.feedback_principals: list[str] = []
        self.review_principals: list[str] = []

    async def list_signals(
        self,
        principal: RequestPrincipal,
        *,
        desk: str,
        status: str | None,
        limit: int,
    ) -> list[SignalView]:
        principal.require_role("analyst", "reviewer", "admin")
        principal.require_desk(desk)
        matches = [
            signal
            for signal in self.signals.values()
            if signal.desk == desk and (status is None or signal.status == status)
        ]
        return sorted(matches, key=lambda item: item.changed_at, reverse=True)[:limit]

    async def get_signal(self, principal: RequestPrincipal, signal_id: str) -> SignalView:
        principal.require_role("analyst", "reviewer", "admin")
        signal = self.signals.get(signal_id)
        if signal is None:
            raise ResourceNotFoundError(signal_id)
        principal.require_desk(signal.desk)
        return signal

    async def submit_feedback(
        self,
        principal: RequestPrincipal,
        signal_id: str,
        request: FeedbackRequest,
    ) -> FeedbackReceipt:
        principal.require_role("analyst", "reviewer", "admin")
        signal = await self.get_signal(principal, signal_id)
        self.feedback_principals.append(principal.principal.principal_id)
        self.signals[signal_id] = signal.model_copy(
            update={"latest_feedback": request.verdict.value}
        )
        return FeedbackReceipt(
            feedback_id=str(uuid4()), signal_id=signal_id, recorded_at=datetime.now(UTC)
        )

    async def close_signal(
        self,
        principal: RequestPrincipal,
        signal_id: str,
        request: SignalCloseRequest,
    ) -> SignalCloseReceipt:
        principal.require_role("analyst", "reviewer", "admin")
        del request
        signal = await self.get_signal(principal, signal_id)
        now = datetime.now(UTC)
        terminal = {"suppressed", "expired", "withdrawn"}
        already_closed = signal.status in terminal
        status = (
            signal.status
            if already_closed
            else ("withdrawn" if signal.status == "published" else "suppressed")
        )
        self.signals[signal_id] = signal.model_copy(
            update={"status": status, "changed_at": now, "closed_at": now}
        )
        return SignalCloseReceipt(
            transition_id=str(uuid4()),
            signal_id=signal_id,
            status=status,
            closed_at=now,
            already_closed=already_closed,
        )

    async def get_entity(self, principal: RequestPrincipal, entity_id: str) -> EntityView:
        principal.require_role("analyst", "reviewer", "admin")
        entity = self.entities.get(entity_id)
        if entity is None:
            raise ResourceNotFoundError(entity_id)
        return entity

    async def get_evidence(
        self, principal: RequestPrincipal, evidence_span_id: str
    ) -> EvidenceSpanView:
        principal.require_role("analyst", "reviewer", "admin")
        evidence = self.evidence.get(evidence_span_id)
        if evidence is None:
            raise ResourceNotFoundError(evidence_span_id)
        return evidence

    async def decide_review(
        self,
        principal: RequestPrincipal,
        subject_type: str,
        subject_id: str,
        request: ReviewDecisionRequest,
    ) -> ReviewReceipt:
        del request
        principal.require_role("reviewer", "admin")
        self.review_principals.append(principal.principal.principal_id)
        return ReviewReceipt(
            review_id=str(uuid4()),
            subject_type=subject_type,
            subject_id=subject_id,
            decided_at=datetime.now(UTC),
        )

    async def request_brief(self, principal: RequestPrincipal, request: BriefRequest) -> BriefView:
        principal.require_role("analyst", "admin")
        principal.require_desk(request.desk)
        brief = BriefView(
            brief_id=str(uuid4()),
            desk=request.desk,
            as_of=request.as_of,
            status="queued",
            coverage_complete=False,
        )
        self.briefs[brief.brief_id] = brief
        return brief

    async def get_brief(self, principal: RequestPrincipal, brief_id: str) -> BriefView:
        principal.require_role("analyst", "publisher", "admin")
        brief = self.briefs.get(brief_id)
        if brief is None:
            raise ResourceNotFoundError(brief_id)
        principal.require_desk(brief.desk)
        return brief

    async def publish_brief(
        self,
        principal: RequestPrincipal,
        brief_id: str,
        request: BriefPublicationRequest,
    ) -> BriefView:
        principal.require_role("publisher", "admin")
        brief = await self.get_brief(principal, brief_id)
        if not brief.coverage_complete:
            raise PublicationNotReadyError(
                "brief publication requires server-computed complete coverage"
            )
        now = datetime.now(UTC)
        published = brief.model_copy(
            update={
                "status": "published",
                "coverage_complete": brief.coverage_complete,
                "html": request.html,
                "publication_id": str(uuid4()),
                "published_at": now,
            }
        )
        self.briefs[brief_id] = published
        return published

    async def get_run(self, principal: RequestPrincipal, run_id: str) -> RunView:
        principal.require_role("operator", "admin")
        run = self.runs.get(run_id)
        if run is None:
            raise ResourceNotFoundError(run_id)
        return run

    async def ready(self) -> bool:
        return True

    async def close(self) -> None:
        return None
