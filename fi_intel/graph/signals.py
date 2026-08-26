"""Stable signal identity, transparent ranking, and lifecycle primitives."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from fi_intel.graph.queries import Pattern
from fi_intel.sources.canonical import BarrierSide

MATERIAL_CHANGE_THRESHOLD = 0.05


class SignalLifecycleState(StrEnum):
    NEW = "new"
    UNCHANGED = "unchanged"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class ScoreContribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    component: str
    raw_value: float
    weight: float
    weighted_value: float
    explanation: str


class Signal(BaseModel):
    """Current authorized view of a stable signal episode."""

    model_config = ConfigDict(frozen=True)

    # Legacy required surface retained for callers and evaluation harnesses.
    signal_id: str
    pattern: str
    entity_key: str
    entity_name: str
    priority: int
    fired_at: datetime
    as_of: datetime
    evidence: dict[str, str]

    pattern_version: str = "unversioned"
    hypothesis: str = ""
    eligible_outcome_kinds: tuple[str, ...] = ()
    opportunity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    ranking_base_score: float = Field(default=0.0, ge=0.0, le=1.0)
    score_contributions: tuple[ScoreContribution, ...] = ()
    lifecycle_state: SignalLifecycleState = SignalLifecycleState.NEW
    opened_at: datetime | None = None
    updated_at: datetime | None = None
    last_confirmed_at: datetime | None = None
    resolved_at: datetime | None = None
    material_arguments: dict[str, str] = Field(default_factory=dict)
    matched_assertion_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    source_doc_ids: tuple[str, ...] = ()
    barrier_side: BarrierSide = BarrierSide.PUBLIC
    policy_version: str = "unpersisted"
    authorization_scope: str = "unscoped"
    analyst_disposition: str | None = None
    analyst_reason: str | None = None
    downstream_opportunity_ids: tuple[str, ...] = ()
    outcome_ids: tuple[str, ...] = ()

    @property
    def actionable(self) -> bool:
        return self.lifecycle_state in {
            SignalLifecycleState.NEW,
            SignalLifecycleState.STRENGTHENED,
            SignalLifecycleState.WEAKENED,
        }


class SignalLifecycleSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: SignalLifecycleState
    opened_at: datetime
    updated_at: datetime
    last_confirmed_at: datetime
    resolved_at: datetime | None = None
    score_anchor: float = Field(ge=0.0, le=1.0)
    policy_version: str
    analyst_disposition: str | None = None
    analyst_reason: str | None = None


class LifecycleDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: SignalLifecycleState
    opened_at: datetime
    updated_at: datetime
    last_confirmed_at: datetime
    resolved_at: datetime | None
    score_anchor: float


def signal_authorization_scope(
    entitlement_group: str,
    side: str,
    allowed_source_ids: Iterable[str] = (),
) -> str:
    """Lifecycle isolation shared by callers with the same effective grants."""
    payload = json.dumps(
        {
            "entitlement_group": entitlement_group,
            "side": side,
            "allowed_source_ids": sorted(set(allowed_source_ids)),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"policy:{digest}"


def stable_signal_id(
    pattern: Pattern,
    entity_key: str,
    material_arguments: Mapping[str, str],
    authorization_scope: str,
) -> str:
    """Identify a signal episode independently from its changing evidence set."""
    payload = json.dumps(
        {
            "pattern": pattern.name,
            "version": pattern.version,
            "entity_key": entity_key,
            "material_arguments": dict(sorted(material_arguments.items())),
            "authorization_scope": authorization_scope,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{pattern.name}:{digest}"


def rescore_for_lifecycle(
    base_score: float,
    contributions: Iterable[ScoreContribution],
    state: SignalLifecycleState,
) -> tuple[float, tuple[ScoreContribution, ...]]:
    """Replace the lifecycle contribution while preserving evidence inputs."""
    novelty = _novelty_value(state)
    replacement = _contribution(
        "novelty",
        novelty,
        0.05,
        "New or materially changed since the prior confirmation.",
    )
    updated = tuple(item for item in contributions if item.component != "novelty") + (replacement,)
    return round(base_score + replacement.weighted_value, 4), updated


def classify_lifecycle(
    previous: SignalLifecycleSnapshot | None,
    base_score: float,
    as_of: datetime,
    *,
    material_change_threshold: float = MATERIAL_CHANGE_THRESHOLD,
) -> LifecycleDecision:
    """Classify one monotonic signal confirmation against its score anchor."""
    if previous is None or previous.state is SignalLifecycleState.RESOLVED:
        return LifecycleDecision(
            state=SignalLifecycleState.NEW,
            opened_at=as_of,
            updated_at=as_of,
            last_confirmed_at=as_of,
            resolved_at=None,
            score_anchor=base_score,
        )
    if as_of < previous.last_confirmed_at:
        raise ValueError("persisted signal lifecycle cannot be evaluated out of order")
    if previous.state is SignalLifecycleState.SUPPRESSED:
        return LifecycleDecision(
            state=SignalLifecycleState.SUPPRESSED,
            opened_at=previous.opened_at,
            updated_at=previous.updated_at,
            last_confirmed_at=as_of,
            resolved_at=previous.resolved_at,
            score_anchor=previous.score_anchor,
        )

    delta = base_score - previous.score_anchor
    if delta >= material_change_threshold:
        state = SignalLifecycleState.STRENGTHENED
    elif delta <= -material_change_threshold:
        state = SignalLifecycleState.WEAKENED
    else:
        state = SignalLifecycleState.UNCHANGED
    changed = state is not SignalLifecycleState.UNCHANGED
    return LifecycleDecision(
        state=state,
        opened_at=previous.opened_at,
        updated_at=as_of if changed else previous.updated_at,
        last_confirmed_at=as_of,
        resolved_at=None,
        score_anchor=base_score if changed else previous.score_anchor,
    )


def score_signal(
    pattern: Pattern,
    *,
    as_of: datetime,
    latest_recorded_at: datetime,
    materiality_score: float,
    evidence_confidence: float,
    assertion_ids: tuple[str, ...],
    source_ids: tuple[str, ...],
    lifecycle_state: SignalLifecycleState,
    historical_precision: float | None = None,
    precision_samples: int = 0,
    precision_weight_scale: float | None = None,
) -> tuple[float, float, tuple[ScoreContribution, ...]]:
    """Return base score, surfaced score, and fully transparent contributions."""
    age_days = max(0.0, (as_of - latest_recorded_at).total_seconds() / 86_400)
    freshness = max(0.0, 1.0 - age_days / pattern.freshness_days)
    coverage = min(1.0, len(set(assertion_ids)) / len(pattern.required_claim_types))
    source_agreement = min(1.0, len(set(source_ids)) / len(pattern.required_claim_types))
    novelty = _novelty_value(lifecycle_state)

    measured_precision = (
        historical_precision if historical_precision is not None else pattern.historical_precision
    )
    # Early feedback is useful but uncertain.  Beta shrinkage happens in the
    # provider; the contribution then ramps smoothly to full weight at 30
    # authorized outcomes instead of appearing at a 30-sample cliff.
    sample_weight = (
        _unit_interval(precision_weight_scale)
        if precision_weight_scale is not None
        else min(1.0, precision_samples / 30)
    )
    precision_weight = 0.15 * sample_weight if measured_precision is not None else 0.0
    precision_explanation = (
        f"Beta-shrunk estimate from {precision_samples} authorized analyst outcomes."
        if measured_precision is not None
        else "No authorized analyst-feedback sample is available; contribution disabled."
    )
    values = (
        ("pattern_prior", pattern.priority / 100.0, 0.30, "Governed detector prior."),
        (
            "historical_precision",
            measured_precision or 0.0,
            precision_weight,
            precision_explanation,
        ),
        (
            "materiality",
            _unit_interval(materiality_score),
            0.20,
            "Magnitude relative to the governed materiality threshold.",
        ),
        ("freshness", freshness, 0.10, "Recency within the pattern freshness window."),
        (
            "claim_coverage",
            coverage,
            0.05,
            "Matched required claim types with exact assertions.",
        ),
        (
            "evidence_confidence",
            _unit_interval(evidence_confidence),
            0.10,
            "Mean confidence of the matched assertions.",
        ),
        (
            "source_agreement",
            source_agreement,
            0.05,
            "Independent authorized sources represented in the evidence set.",
        ),
    )
    base_contributions = tuple(_contribution(*value) for value in values)
    novelty_contribution = _contribution(
        "novelty",
        novelty,
        0.05,
        "New or materially changed since the prior confirmation.",
    )
    base_score = round(sum(item.weighted_value for item in base_contributions), 4)
    opportunity_score = round(base_score + novelty_contribution.weighted_value, 4)
    return base_score, opportunity_score, (*base_contributions, novelty_contribution)


def _contribution(
    component: str,
    raw_value: float,
    weight: float,
    explanation: str,
) -> ScoreContribution:
    return ScoreContribution(
        component=component,
        raw_value=round(raw_value, 4),
        weight=weight,
        weighted_value=round(raw_value * weight, 4),
        explanation=explanation,
    )


def _unit_interval(value: float) -> float:
    return max(0.0, min(1.0, value))


def _novelty_value(state: SignalLifecycleState) -> float:
    return {
        SignalLifecycleState.NEW: 1.0,
        SignalLifecycleState.STRENGTHENED: 1.0,
        SignalLifecycleState.WEAKENED: 0.75,
        SignalLifecycleState.RESOLVED: 0.5,
        SignalLifecycleState.UNCHANGED: 0.0,
        SignalLifecycleState.SUPPRESSED: 0.0,
    }[state]
