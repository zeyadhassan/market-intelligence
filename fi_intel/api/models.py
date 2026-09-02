"""Stable request and response contracts for analyst workflows."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ApiModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class SignalView(ApiModel):
    signal_id: str
    pattern_id: str
    pattern_version: str
    entity_id: str
    entity_name: str
    desk: str
    status: str
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    as_of: datetime
    changed_at: datetime
    assertion_ids: tuple[str, ...] = ()
    evidence_span_ids: tuple[str, ...] = ()
    latest_feedback: str | None = None
    closed_at: datetime | None = None


class EntityAssertionView(ApiModel):
    assertion_id: str
    predicate: str
    object_json: dict[str, JsonValue]
    qualifiers: dict[str, JsonValue] = Field(default_factory=dict)
    event_time: datetime | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    recorded_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_span_ids: tuple[str, ...] = ()


class EntityView(ApiModel):
    entity_id: str
    entity_type: str
    canonical_name: str
    identifiers: dict[str, str] = Field(default_factory=dict)
    timeline: tuple[EntityAssertionView, ...] = ()


class EvidenceSpanView(ApiModel):
    evidence_span_id: str
    document_version_id: str
    title: str
    quote: str
    char_start: int
    char_end: int
    source_url: str | None = None
    source_id: str | None = None
    published_at: datetime | None = None


class FeedbackVerdict(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    NEEDS_REVIEW = "needs_review"
    USEFUL = "useful"
    NOT_USEFUL = "not_useful"
    WRONG_ENTITY = "wrong_entity"
    STALE = "stale"
    ALREADY_KNOWN = "already_known"
    WRONG_EVIDENCE = "wrong_evidence"
    WRONG_MATERIALITY = "wrong_materiality"


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verdict: FeedbackVerdict
    reason: str = Field(min_length=1, max_length=2000)


class FeedbackReceipt(ApiModel):
    feedback_id: str
    signal_id: str
    recorded_at: datetime


class SignalCloseReason(StrEnum):
    ACTIONED = "actioned"
    NOT_RELEVANT = "not_relevant"
    DUPLICATE = "duplicate"
    STALE = "stale"
    INVALID = "invalid"


class SignalCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: SignalCloseReason
    note: str = Field(min_length=1, max_length=2000)


class SignalCloseReceipt(ApiModel):
    transition_id: str
    signal_id: str
    status: str
    closed_at: datetime
    already_closed: bool = False


class ReviewDecisionValue(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: ReviewDecisionValue
    reason: str = Field(min_length=1, max_length=2000)


class ReviewReceipt(ApiModel):
    review_id: str
    subject_type: str
    subject_id: str
    decided_at: datetime


class BriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    desk: str = Field(min_length=1)
    as_of: datetime


class BriefPublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    html: str = Field(min_length=1, max_length=2_000_000)


class BriefView(ApiModel):
    brief_id: str
    desk: str
    as_of: datetime
    status: str
    coverage_complete: bool
    html: str | None = None
    run_id: str | None = None
    publication_id: str | None = None
    published_at: datetime | None = None


class RunView(ApiModel):
    run_id: str
    run_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    counters: dict[str, int] = Field(default_factory=dict)
    error_summary: str | None = None


class SessionView(ApiModel):
    principal_id: str
    desks: tuple[str, ...]
    roles: tuple[str, ...]


class TopicTagView(ApiModel):
    topic_id: str
    label: str
    description: str
    subscribed: bool = False


class TopicSubscriptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


class TopicSubscriptionView(ApiModel):
    topic_id: str
    active: bool
    updated_at: datetime


class OpportunityEvidenceView(ApiModel):
    evidence_id: str
    title: str
    quote: str
    source_id: str
    source_url: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    content_hash: str | None = None
    country: str | None = None
    source_type: str | None = None


class OpportunityResultView(ApiModel):
    result_id: str
    topic_id: str
    title: str
    entity_name: str
    summary: str
    freshness_reason: str
    lifecycle_state: str
    score: float = Field(ge=0.0, le=1.0)
    as_of: datetime
    changed_at: datetime
    coverage_state: str
    falsifier: str
    why_now: str = ""
    commercial_angle: str = ""
    materiality: str = ""
    contradictions: tuple[str, ...] = ()
    uncertainty: str = ""
    coverage_details: str = ""
    change_summary: str = ""
    investigation_trace: tuple[dict[str, str], ...] = ()
    evidence: tuple[OpportunityEvidenceView, ...] = ()
    latest_evaluation: str | None = None


class LiveSourceStatusView(ApiModel):
    source_id: str
    display_name: str
    country: str
    source_type: str
    source_url: str
    status: str
    fetched_at: datetime | None = None
    content_hash: str | None = None
    candidate_count: int = 0
    rejected_candidate_count: int = 0
    detail: str


class TopicResultsView(ApiModel):
    topic_id: str
    label: str
    analysis_status: str
    coverage_state: str
    as_of: datetime
    message: str
    mode: str = "fixture"
    scope_notice: str
    model_name: str | None = None
    model_call_count: int = 0
    model_failure_count: int = 0
    run_id: str | None = None
    analysis_job_id: str | None = None
    business_date: str | None = None
    lifecycle_counts: dict[str, int] = Field(default_factory=dict)
    required_source_count: int = 0
    successful_source_count: int = 0
    rejected_candidate_count: int = 0
    source_statuses: tuple[LiveSourceStatusView, ...] = ()
    results: tuple[OpportunityResultView, ...] = ()


class ResultEvaluationVerdict(StrEnum):
    USEFUL = "useful"
    NOT_RELEVANT = "not_relevant"
    INCORRECT = "incorrect"
    DUPLICATE = "duplicate"
    TOO_OLD = "too_old"


class ResultEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verdict: ResultEvaluationVerdict
    note: str = Field(default="", max_length=1000)


class ResultEvaluationReceipt(ApiModel):
    evaluation_id: str
    result_id: str
    verdict: ResultEvaluationVerdict
    recorded_at: datetime


class SearchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    seed_entity_ids: tuple[str, ...] = Field(default=(), max_length=10)


class SearchView(ApiModel):
    search_id: str
    state: str
    route: str
    query: str
    temporal_pin: datetime
    answer: dict[str, object] | None = None
    safe_error_summary: str | None = None
