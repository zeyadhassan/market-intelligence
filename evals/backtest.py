"""Leakage-safe temporal backtesting for deterministic opportunity detectors.

The backtester executes pattern Cypher without persisting ``Signal`` nodes.
Predictions are evaluated as entity/opportunity episodes, while per-pattern
attribution remains available for detector diagnostics.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry, Signal


class LeakageError(RuntimeError):
    """Evidence unavailable at the prediction cutoff influenced a signal."""


@dataclass(frozen=True)
class Outcome:
    """An independently labelled business outcome."""

    outcome_id: str
    entity_key: str
    outcome_date: date
    kind: str
    opportunity_type: str = "dcm_mandate"


class OpportunityRule(BaseModel):
    """The predeclared outcome contract for one detector."""

    model_config = ConfigDict(frozen=True)

    opportunity_type: str
    outcome_kinds: frozenset[str]
    horizon_days: int = Field(gt=0)


DEFAULT_OPPORTUNITY_RULES: dict[str, OpportunityRule] = {
    pattern: OpportunityRule(
        opportunity_type="dcm_mandate",
        outcome_kinds=frozenset({"mandate_announced"}),
        horizon_days=365,
    )
    for pattern in (
        "maturity_wall_no_refi",
        "negative_rating_action_with_capital_decline",
        "leadership_change_treasury",
        "board_approved_issuance_programme",
        "at1_call_approaching_no_refi",
    )
}


class FiredSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern: str
    opportunity_type: str
    entity_key: str
    fired_at: date
    priority: int


class OpportunityPrediction(BaseModel):
    """One surfaced entity/opportunity episode, ranked at its first cutoff."""

    model_config = ConfigDict(frozen=True)

    opportunity_type: str
    entity_key: str
    fired_at: date
    priority: int
    patterns: tuple[str, ...]
    rank_at_cutoff: int
    matched_outcome_id: str | None = None
    lead_days: int | None = None


class PatternAttribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern: str
    fired: int
    preceded_outcome: int
    lead_days: list[int]


class BacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_date: date
    to_date: date
    step_days: int
    precision_at_10: float
    recall: float
    attribution: list[PatternAttribution]
    predictions: list[OpportunityPrediction]
    total_signals: int
    total_opportunities: int
    eligible_outcomes: int
    matched_outcomes: int


class SignalRunner(Protocol):
    async def run(
        self,
        as_of: datetime,
        enabled: set[str] | None,
        window_days: int,
    ) -> list[Signal]: ...


class ReadOnlyPatternRunner:
    """Evaluate registered Cypher without contaminating the graph with signals."""

    def __init__(self, client: GraphClient, registry: PatternRegistry) -> None:
        del client  # retained in the constructor for Backtester compatibility
        self._registry = registry

    async def run(
        self,
        as_of: datetime,
        enabled: set[str] | None,
        window_days: int,
    ) -> list[Signal]:
        return await self._registry.evaluate(
            as_of,
            enabled=enabled,
            window_days=window_days,
        )


class Backtester:
    def __init__(
        self,
        client: GraphClient,
        registry: PatternRegistry,
        *,
        rules: Mapping[str, OpportunityRule] | None = None,
        signal_runner: SignalRunner | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._rules = dict(DEFAULT_OPPORTUNITY_RULES if rules is None else rules)
        self._signal_runner = signal_runner or ReadOnlyPatternRunner(client, registry)

    async def run(
        self,
        from_date: date,
        to_date: date,
        step_days: int,
        outcomes: list[Outcome],
        enabled: set[str] | None = None,
        window_days: int = 395,
    ) -> BacktestResult:
        if step_days < 1:
            msg = "step_days must be >= 1"
            raise ValueError(msg)
        if from_date >= to_date:
            msg = "from_date must be before to_date"
            raise ValueError(msg)
        if window_days < 1:
            msg = "window_days must be >= 1"
            raise ValueError(msg)

        registered = set(self._registry.pattern_names())
        active_patterns = registered if enabled is None else registered & enabled
        missing_rules = sorted(active_patterns - self._rules.keys())
        if missing_rules:
            msg = f"no opportunity/outcome rule configured for patterns: {missing_rules}"
            raise ValueError(msg)
        evaluation_rules = {
            pattern: rule for pattern, rule in self._rules.items() if pattern in active_patterns
        }

        fired: list[FiredSignal] = []
        current = from_date
        while current <= to_date:
            as_of = datetime(current.year, current.month, current.day, tzinfo=UTC)
            signals = await self._signal_runner.run(as_of, enabled, window_days)
            await self._assert_pin(as_of, signals)
            for signal in signals:
                rule = evaluation_rules.get(signal.pattern)
                if rule is None:
                    msg = f"no opportunity/outcome rule configured for pattern {signal.pattern!r}"
                    raise ValueError(msg)
                fired.append(
                    FiredSignal(
                        pattern=signal.pattern,
                        opportunity_type=rule.opportunity_type,
                        entity_key=signal.entity_key,
                        fired_at=current,
                        priority=signal.priority,
                    )
                )
            current += timedelta(days=step_days)

        return compute_backtest_metrics(
            from_date,
            to_date,
            step_days,
            fired,
            outcomes,
            evaluation_rules,
        )

    async def _assert_pin(
        self,
        as_of: datetime,
        signals: list[Signal] | None = None,
    ) -> None:
        """Verify detector evidence exists in the graph snapshot at the cutoff."""
        rows = await self._client.read_all_assertions_including_superseded(
            as_of=as_of,
            access=self._registry.access,
        )
        visible_refs: set[tuple[str, str]] = set()
        for row in rows:
            assertion = row["a"]
            recorded = assertion.get("recorded_at")
            if recorded is not None:
                recorded_dt = recorded.to_native() if hasattr(recorded, "to_native") else recorded
                if recorded_dt > as_of:
                    msg = f"leakage: assertion recorded at {recorded_dt} visible at {as_of.date()}"
                    raise LeakageError(msg)
            source_doc_id = assertion.get("source_doc_id")
            source_id = assertion.get("source_id")
            if source_id and source_doc_id:
                visible_refs.add((str(source_id), str(source_doc_id)))

        for signal in signals or []:
            try:
                cited_refs = set(zip(signal.source_ids, signal.source_doc_ids, strict=True))
            except ValueError as exc:
                msg = f"leakage: signal {signal.signal_id!r} has malformed provenance"
                raise LeakageError(msg) from exc
            unavailable_refs = cited_refs - visible_refs
            if unavailable_refs:
                msg = (
                    f"leakage: signal {signal.signal_id!r} cites evidence unavailable "
                    f"at {as_of.date()}: {sorted(unavailable_refs)}"
                )
                raise LeakageError(msg)
            # Legacy/fault-injection signals may lack structured provenance.
            if not cited_refs:
                visible_doc_ids = {doc_id for _, doc_id in visible_refs}
                cited_docs = {
                    value
                    for key, value in signal.evidence.items()
                    if "doc" in key.lower() and value not in {"", "None"}
                }
                unavailable_docs = cited_docs - visible_doc_ids
                if unavailable_docs:
                    msg = (
                        f"leakage: signal {signal.signal_id!r} cites evidence unavailable "
                        f"at {as_of.date()}: {sorted(unavailable_docs)}"
                    )
                    raise LeakageError(msg)


def _compatible_lead(
    fired_at: date,
    pattern: str,
    opportunity_type: str,
    outcome: Outcome,
    rules: Mapping[str, OpportunityRule],
) -> int | None:
    rule = rules[pattern]
    if rule.opportunity_type != opportunity_type:
        return None
    if outcome.opportunity_type != opportunity_type or outcome.kind not in rule.outcome_kinds:
        return None
    lead = (outcome.outcome_date - fired_at).days
    return lead if 0 < lead <= rule.horizon_days else None


def _eligible_outcomes(
    outcomes: list[Outcome],
    rules: Mapping[str, OpportunityRule],
    from_date: date,
    to_date: date,
) -> list[Outcome]:
    eligible: list[Outcome] = []
    for outcome in outcomes:
        horizons = [
            rule.horizon_days
            for rule in rules.values()
            if rule.opportunity_type == outcome.opportunity_type
            and outcome.kind in rule.outcome_kinds
        ]
        if not horizons:
            continue
        if from_date < outcome.outcome_date <= to_date + timedelta(days=max(horizons)):
            eligible.append(outcome)
    return sorted(eligible, key=lambda item: (item.outcome_date, item.outcome_id))


def _build_predictions(fired: list[FiredSignal]) -> list[OpportunityPrediction]:
    first_date: dict[tuple[str, str], date] = {}
    at_first_cutoff: dict[tuple[str, str], list[FiredSignal]] = defaultdict(list)
    for signal in sorted(
        fired,
        key=lambda item: (item.fired_at, -item.priority, item.pattern, item.entity_key),
    ):
        key = (signal.opportunity_type, signal.entity_key)
        if key not in first_date:
            first_date[key] = signal.fired_at
        if signal.fired_at == first_date[key]:
            at_first_cutoff[key].append(signal)

    unranked: list[OpportunityPrediction] = []
    for (opportunity_type, entity_key), signals in at_first_cutoff.items():
        unranked.append(
            OpportunityPrediction(
                opportunity_type=opportunity_type,
                entity_key=entity_key,
                fired_at=first_date[(opportunity_type, entity_key)],
                priority=max(signal.priority for signal in signals),
                patterns=tuple(sorted({signal.pattern for signal in signals})),
                rank_at_cutoff=0,
            )
        )

    ranked: list[OpportunityPrediction] = []
    by_cutoff: dict[date, list[OpportunityPrediction]] = defaultdict(list)
    for prediction in unranked:
        by_cutoff[prediction.fired_at].append(prediction)
    for cutoff in sorted(by_cutoff):
        ordered = sorted(
            by_cutoff[cutoff],
            key=lambda item: (-item.priority, item.opportunity_type, item.entity_key),
        )
        ranked.extend(
            prediction.model_copy(update={"rank_at_cutoff": rank})
            for rank, prediction in enumerate(ordered, start=1)
        )
    return ranked


def _match_predictions(
    predictions: list[OpportunityPrediction],
    outcomes: list[Outcome],
    rules: Mapping[str, OpportunityRule],
) -> tuple[list[OpportunityPrediction], set[str]]:
    matched_predictions: set[int] = set()
    matches: dict[int, tuple[str, int]] = {}
    matched_outcome_ids: set[str] = set()
    for outcome in outcomes:
        candidates: list[tuple[int, int]] = []
        for index, prediction in enumerate(predictions):
            if index in matched_predictions or prediction.entity_key != outcome.entity_key:
                continue
            leads = [
                lead
                for pattern in prediction.patterns
                if (
                    lead := _compatible_lead(
                        prediction.fired_at,
                        pattern,
                        prediction.opportunity_type,
                        outcome,
                        rules,
                    )
                )
                is not None
            ]
            if leads:
                candidates.append((index, min(leads)))
        if not candidates:
            continue
        index, lead = min(
            candidates,
            key=lambda item: (
                item[1],
                -predictions[item[0]].priority,
                predictions[item[0]].entity_key,
            ),
        )
        matched_predictions.add(index)
        matched_outcome_ids.add(outcome.outcome_id)
        matches[index] = (outcome.outcome_id, lead)

    return (
        [
            prediction.model_copy(
                update={
                    "matched_outcome_id": matches[index][0],
                    "lead_days": matches[index][1],
                }
            )
            if index in matches
            else prediction
            for index, prediction in enumerate(predictions)
        ],
        matched_outcome_ids,
    )


def compute_backtest_metrics(
    from_date: date,
    to_date: date,
    step_days: int,
    fired: list[FiredSignal],
    outcomes: list[Outcome],
    rules: Mapping[str, OpportunityRule] | None = None,
) -> BacktestResult:
    """Pure metric calculation, separated from databases for focused tests."""
    active_rules = dict(DEFAULT_OPPORTUNITY_RULES if rules is None else rules)
    missing_rules = sorted({signal.pattern for signal in fired} - active_rules.keys())
    if missing_rules:
        msg = f"no opportunity/outcome rule configured for patterns: {missing_rules}"
        raise ValueError(msg)

    eligible_outcomes = _eligible_outcomes(outcomes, active_rules, from_date, to_date)
    predictions = _build_predictions(fired)
    predictions, matched_outcome_ids = _match_predictions(
        predictions,
        eligible_outcomes,
        active_rules,
    )

    selected = [prediction for prediction in predictions if prediction.rank_at_cutoff <= 10]
    precision_at_10 = (
        sum(prediction.matched_outcome_id is not None for prediction in selected) / len(selected)
        if selected
        else 0.0
    )
    recall = len(matched_outcome_ids) / len(eligible_outcomes) if eligible_outcomes else 0.0

    first_pattern_fired: dict[tuple[str, str], FiredSignal] = {}
    for signal in fired:
        key = (signal.pattern, signal.entity_key)
        previous = first_pattern_fired.get(key)
        if previous is None or signal.fired_at < previous.fired_at:
            first_pattern_fired[key] = signal

    attribution: list[PatternAttribution] = []
    for pattern in sorted({key[0] for key in first_pattern_fired}):
        pattern_signals = [
            signal for key, signal in first_pattern_fired.items() if key[0] == pattern
        ]
        leads: list[int] = []
        for signal in pattern_signals:
            compatible = [
                lead
                for outcome in eligible_outcomes
                if outcome.entity_key == signal.entity_key
                and (
                    lead := _compatible_lead(
                        signal.fired_at,
                        signal.pattern,
                        signal.opportunity_type,
                        outcome,
                        active_rules,
                    )
                )
                is not None
            ]
            if compatible:
                leads.append(min(compatible))
        attribution.append(
            PatternAttribution(
                pattern=pattern,
                fired=len(pattern_signals),
                preceded_outcome=len(leads),
                lead_days=sorted(leads),
            )
        )

    return BacktestResult(
        from_date=from_date,
        to_date=to_date,
        step_days=step_days,
        precision_at_10=round(precision_at_10, 4),
        recall=round(recall, 4),
        attribution=attribution,
        predictions=predictions,
        total_signals=len(first_pattern_fired),
        total_opportunities=len(predictions),
        eligible_outcomes=len(eligible_outcomes),
        matched_outcomes=len(matched_outcome_ids),
    )
