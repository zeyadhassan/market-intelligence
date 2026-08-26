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
    coverage_complete: bool


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
