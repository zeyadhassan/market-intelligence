"""Service-free interpreter for the governed detector contracts.

Production executes each :class:`~fi_intel.graph.queries.Pattern` as Cypher.
The POC cannot require Neo4j, so this module evaluates the same typed fields,
threshold metadata, temporal pins, and provenance over admitted assertions in
memory. It is intentionally small and parity-tested on the synthetic corpus.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from fi_intel.governance.policy import GraphAccessContext
from fi_intel.graph.properties import project_typed_properties
from fi_intel.graph.queries import ALL_PATTERNS, Pattern
from fi_intel.graph.signals import (
    Signal,
    SignalLifecycleState,
    score_signal,
    signal_authorization_scope,
    stable_signal_id,
)
from fi_intel.ontology.schema import Assertion
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.sources.canonical import BarrierSide


@dataclass(frozen=True)
class _Candidate:
    entity_key: str
    entity_name: str
    assertions: tuple[Assertion, ...]
    evidence: dict[str, str]
    material_arguments: dict[str, str]
    materiality_score: float

    @property
    def latest_recorded_at(self) -> datetime:
        return max(assertion.recorded_at for assertion in self.assertions)

    @property
    def confidence(self) -> float:
        return sum(assertion.confidence for assertion in self.assertions) / len(self.assertions)


def _typed(assertion: Assertion) -> dict[str, str | float | bool]:
    return project_typed_properties(assertion.properties)


def _text(properties: dict[str, str | float | bool], name: str) -> str:
    value = properties.get(f"fact_{name}")
    return str(value) if value is not None else ""


def _number(properties: dict[str, str | float | bool], name: str) -> float:
    value = properties.get(f"fact_{name}")
    return float(value) if isinstance(value, int | float) else 0.0


def _flag(properties: dict[str, str | float | bool], name: str) -> bool:
    return properties.get(f"fact_{name}") is True


def _fresh(assertion: Assertion, pattern: Pattern, as_of: datetime) -> bool:
    return assertion.recorded_at >= as_of - timedelta(days=pattern.freshness_days)


def _threshold(pattern: Pattern, attribute: str) -> float:
    match = next(
        (
            threshold
            for threshold in pattern.materiality_thresholds
            if threshold.attribute == attribute
        ),
        None,
    )
    if match is None:
        raise RuntimeError(f"pattern {pattern.name!r} omits threshold {attribute!r}")
    return match.value


def _currency_allowed(pattern: Pattern, currency: str) -> bool:
    return currency.casefold() in {allowed.casefold() for allowed in pattern.allowed_currencies}


class POCAssertionDetector:
    """Evaluate admitted assertions without a graph service.

    It never reads fixture labels or document bodies. The only inputs are
    governed pattern metadata, admitted typed assertions, an as-of instant,
    and the caller's explicit source grants.
    """

    def __init__(self, patterns: tuple[Pattern, ...] = ALL_PATTERNS) -> None:
        self._patterns = {pattern.name: pattern for pattern in patterns}

    async def detect(
        self,
        assertions: tuple[Assertion, ...],
        *,
        as_of: datetime,
        access: GraphAccessContext,
        enabled: set[str] | None = None,
    ) -> list[Signal]:
        if enabled is not None:
            unknown = enabled - self._patterns.keys()
            if unknown:
                raise ValueError(f"unknown patterns: {sorted(unknown)}")
        visible = tuple(
            assertion
            for assertion in assertions
            if assertion.recorded_at <= as_of
            and assertion.source_id in access.allowed_source_ids
            and (
                assertion.barrier_side is BarrierSide.PUBLIC
                or access.principal.side.value == BarrierSide.PRIVATE.value
            )
        )
        names = sorted(enabled if enabled is not None else self._patterns)
        scope = signal_authorization_scope(
            access.principal.entitlement_group,
            access.principal.side.value,
            access.allowed_source_ids,
        )
        signals: list[Signal] = []
        for name in names:
            pattern = self._patterns[name]
            if not pattern.deployable:
                continue
            for candidate in self._candidates(pattern, visible, as_of):
                signals.append(self._signal(pattern, candidate, as_of, access, scope))
        unique = {signal.signal_id: signal for signal in signals}
        return sorted(
            unique.values(),
            key=lambda signal: (-signal.opportunity_score, signal.signal_id),
        )

    def _candidates(
        self,
        pattern: Pattern,
        assertions: tuple[Assertion, ...],
        as_of: datetime,
    ) -> list[_Candidate]:
        evaluators = {
            "negative_rating_action_with_capital_decline": self._rating_capital,
            "leadership_change_treasury": self._leadership,
            "board_approved_issuance_programme": self._programme,
            "maturity_wall_no_refi": self._maturity,
            "at1_call_approaching_no_refi": self._at1_call,
        }
        evaluator = evaluators.get(pattern.name)
        return [] if evaluator is None else evaluator(pattern, assertions, as_of)

    @staticmethod
    def _rating_capital(
        pattern: Pattern,
        assertions: tuple[Assertion, ...],
        as_of: datetime,
    ) -> list[_Candidate]:
        ratings = [
            assertion
            for assertion in assertions
            if assertion.predicate is EdgeType.RATING_ACTION_ON
            and _fresh(assertion, pattern, as_of)
            and _text(_typed(assertion), "direction") == "negative"
            and _text(_typed(assertion), "rating_type") == "outlook"
        ]
        metrics = [
            assertion
            for assertion in assertions
            if assertion.predicate is EdgeType.REPORTS_METRIC
            and _fresh(assertion, pattern, as_of)
            and _text(_typed(assertion), "direction") == "down"
            and _text(_typed(assertion), "metric") == "cet1"
        ]
        candidates: list[_Candidate] = []
        threshold = _threshold(pattern, "cet1_decline")
        for rating in ratings:
            rating_org = (
                rating.subject
                if rating.subject.node_type is NodeType.ORGANIZATION
                else rating.object
            )
            for metric in metrics:
                if metric.subject.key != rating_org.key:
                    continue
                decline = _number(_typed(metric), "prior") - _number(_typed(metric), "value")
                if decline < threshold:
                    continue
                if abs((metric.valid_from.date() - rating.valid_from.date()).days) > 120:
                    continue
                candidates.append(
                    _Candidate(
                        entity_key=rating_org.key,
                        entity_name=rating_org.display_name,
                        assertions=(rating, metric),
                        evidence={
                            "rating_type": "outlook",
                            "metric": "cet1",
                            "rating_doc": rating.source_doc_id,
                            "metric_doc": metric.source_doc_id,
                        },
                        material_arguments={"rating_type": "outlook", "metric": "cet1"},
                        materiality_score=min(1.0, decline / (threshold * 4.0)),
                    )
                )
        return candidates

    @staticmethod
    def _leadership(
        pattern: Pattern,
        assertions: tuple[Assertion, ...],
        as_of: datetime,
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        for assertion in assertions:
            properties = _typed(assertion)
            if (
                assertion.predicate is not EdgeType.LEADERSHIP_CHANGE_AT
                or not _fresh(assertion, pattern, as_of)
                or assertion.object.node_type is not NodeType.ORGANIZATION
                or _text(properties, "role") not in {"treasurer", "cfo"}
            ):
                continue
            role = _text(properties, "role")
            candidates.append(
                _Candidate(
                    entity_key=assertion.object.key,
                    entity_name=assertion.object.display_name,
                    assertions=(assertion,),
                    evidence={"role": role, "doc": assertion.source_doc_id},
                    material_arguments={"role": role},
                    materiality_score=pattern.default_materiality_score,
                )
            )
        return candidates

    @staticmethod
    def _programme(
        pattern: Pattern,
        assertions: tuple[Assertion, ...],
        as_of: datetime,
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        threshold = _threshold(pattern, "limit_usd_bn")
        for assertion in assertions:
            properties = _typed(assertion)
            limit_usd_bn = _number(properties, "limit_usd_bn")
            currency = _text(properties, "currency")
            if (
                assertion.predicate is not EdgeType.PROGRAMME_APPROVED_BY
                or not _fresh(assertion, pattern, as_of)
                or assertion.object.node_type is not NodeType.ORGANIZATION
                or limit_usd_bn < threshold
                or not _currency_allowed(pattern, currency)
                or _text(properties, "status") != "approved"
                or _flag(properties, "marketed")
            ):
                continue
            candidates.append(
                _Candidate(
                    entity_key=assertion.object.key,
                    entity_name=assertion.object.display_name,
                    assertions=(assertion,),
                    evidence={
                        "programme_key": assertion.subject.key,
                        "currency": currency,
                        "doc": assertion.source_doc_id,
                    },
                    material_arguments={
                        "programme_key": assertion.subject.key,
                        "currency": currency,
                    },
                    materiality_score=min(1.0, limit_usd_bn / (threshold * 3.0)),
                )
            )
        return candidates

    @staticmethod
    def _maturity(
        pattern: Pattern,
        assertions: tuple[Assertion, ...],
        as_of: datetime,
    ) -> list[_Candidate]:
        return POCAssertionDetector._instrument_event_candidates(
            pattern,
            assertions,
            as_of,
            predicate=EdgeType.MATURES_ON,
            required_class=None,
        )

    @staticmethod
    def _at1_call(
        pattern: Pattern,
        assertions: tuple[Assertion, ...],
        as_of: datetime,
    ) -> list[_Candidate]:
        return POCAssertionDetector._instrument_event_candidates(
            pattern,
            assertions,
            as_of,
            predicate=EdgeType.CALLABLE_ON,
            required_class="at1",
        )

    @staticmethod
    def _instrument_event_candidates(
        pattern: Pattern,
        assertions: tuple[Assertion, ...],
        as_of: datetime,
        *,
        predicate: EdgeType,
        required_class: str | None,
    ) -> list[_Candidate]:
        threshold = _threshold(pattern, "amount_usd_mn")
        refinanced = {
            assertion.object.key
            for assertion in assertions
            if assertion.predicate is EdgeType.REFINANCES
        }
        candidates: list[_Candidate] = []
        for event in assertions:
            properties = _typed(event)
            currency = _text(properties, "currency")
            if (
                event.predicate is not predicate
                or not _fresh(event, pattern, as_of)
                or event.subject.key in refinanced
                or _number(properties, "amount_usd_mn") < threshold
                or not _currency_allowed(pattern, currency)
                or (required_class is not None and _text(properties, "class") != required_class)
                or event.valid_from < as_of
                or event.valid_from > as_of + timedelta(days=pattern.prediction_horizon_days)
            ):
                continue
            for issue in assertions:
                if (
                    issue.predicate is not EdgeType.ISSUES
                    or issue.object.key != event.subject.key
                    or issue.subject.node_type is not NodeType.ORGANIZATION
                    or not _fresh(issue, pattern, as_of)
                ):
                    continue
                amount = _number(properties, "amount_usd_mn")
                material_arguments = {
                    "instrument": event.subject.key,
                    "currency": currency,
                }
                if required_class is not None:
                    material_arguments["instrument_class"] = required_class
                candidates.append(
                    _Candidate(
                        entity_key=issue.subject.key,
                        entity_name=issue.subject.display_name,
                        assertions=(event, issue),
                        evidence={
                            **material_arguments,
                            "amount_usd_mn": format(amount, ".0f"),
                            "doc": event.source_doc_id,
                        },
                        material_arguments=material_arguments,
                        materiality_score=min(1.0, amount / (threshold * 2.0)),
                    )
                )
        return candidates

    @staticmethod
    def _signal(
        pattern: Pattern,
        candidate: _Candidate,
        as_of: datetime,
        access: GraphAccessContext,
        authorization_scope: str,
    ) -> Signal:
        assertion_ids = tuple(
            sorted(assertion.assertion_id() for assertion in candidate.assertions)
        )
        refs = sorted(
            {(assertion.source_id, assertion.source_doc_id) for assertion in candidate.assertions}
        )
        source_ids = tuple(source_id for source_id, _ in refs)
        source_doc_ids = tuple(doc_id for _, doc_id in refs)
        predicates = {assertion.predicate for assertion in candidate.assertions}
        if not pattern.required_claim_types <= predicates:
            raise RuntimeError(f"candidate for {pattern.name!r} lacks required claim types")
        if set(candidate.material_arguments) != set(pattern.material_arguments):
            raise RuntimeError(f"candidate for {pattern.name!r} lacks material arguments")
        base_score, opportunity_score, contributions = score_signal(
            pattern,
            as_of=as_of,
            latest_recorded_at=candidate.latest_recorded_at,
            materiality_score=candidate.materiality_score,
            evidence_confidence=candidate.confidence,
            assertion_ids=assertion_ids,
            source_ids=source_ids,
            lifecycle_state=SignalLifecycleState.NEW,
        )
        signal_id = stable_signal_id(
            pattern,
            candidate.entity_key,
            candidate.material_arguments,
            authorization_scope,
        )
        barrier = (
            BarrierSide.PRIVATE
            if any(
                assertion.barrier_side is BarrierSide.PRIVATE for assertion in candidate.assertions
            )
            else BarrierSide.PUBLIC
        )
        return Signal(
            signal_id=signal_id,
            pattern=pattern.name,
            pattern_version=pattern.version,
            hypothesis=pattern.hypothesis,
            eligible_outcome_kinds=tuple(sorted(pattern.eligible_outcome_kinds)),
            entity_key=candidate.entity_key,
            entity_name=candidate.entity_name,
            priority=round(opportunity_score * 100),
            opportunity_score=opportunity_score,
            ranking_base_score=base_score,
            score_contributions=contributions,
            lifecycle_state=SignalLifecycleState.NEW,
            opened_at=as_of,
            updated_at=as_of,
            last_confirmed_at=as_of,
            fired_at=as_of,
            as_of=as_of,
            evidence=candidate.evidence,
            material_arguments=candidate.material_arguments,
            matched_assertion_ids=assertion_ids,
            source_ids=source_ids,
            source_doc_ids=source_doc_ids,
            barrier_side=barrier,
            policy_version=access.policy_version,
            authorization_scope=authorization_scope,
        )
