"""Focused tests for temporal matching, ranking, recall, and leakage."""

import asyncio
from datetime import UTC, date, datetime

import pytest

from evals.backtest import (
    Backtester,
    FiredSignal,
    LeakageError,
    OpportunityRule,
    Outcome,
    compute_backtest_metrics,
)
from fi_intel.governance.policy import trusted_test_access
from fi_intel.graph.registry import PatternRegistry, Signal

RULES = {
    "dcm_high": OpportunityRule(
        opportunity_type="dcm_mandate",
        outcome_kinds=frozenset({"mandate_announced"}),
        horizon_days=90,
    ),
    "dcm_low": OpportunityRule(
        opportunity_type="dcm_mandate",
        outcome_kinds=frozenset({"mandate_announced"}),
        horizon_days=90,
    ),
    "equity": OpportunityRule(
        opportunity_type="equity_raise",
        outcome_kinds=frozenset({"equity_announced"}),
        horizon_days=30,
    ),
}
START = date(2024, 1, 1)
END = date(2024, 6, 30)


def fired(
    entity_key: str,
    fired_at: date,
    *,
    pattern: str = "dcm_high",
    opportunity_type: str = "dcm_mandate",
    priority: int = 80,
) -> FiredSignal:
    return FiredSignal(
        pattern=pattern,
        opportunity_type=opportunity_type,
        entity_key=entity_key,
        fired_at=fired_at,
        priority=priority,
    )


def outcome(
    entity_key: str,
    outcome_date: date,
    *,
    outcome_id: str = "outcome-1",
    kind: str = "mandate_announced",
    opportunity_type: str = "dcm_mandate",
) -> Outcome:
    return Outcome(
        outcome_id=outcome_id,
        entity_key=entity_key,
        outcome_date=outcome_date,
        kind=kind,
        opportunity_type=opportunity_type,
    )


def metrics(signals: list[FiredSignal], outcomes: list[Outcome]):
    return compute_backtest_metrics(START, END, 7, signals, outcomes, RULES)


def test_wrong_opportunity_type_cannot_match_same_entity() -> None:
    result = metrics(
        [fired("bank-1", date(2024, 3, 1))],
        [
            outcome(
                "bank-1",
                date(2024, 4, 1),
                kind="equity_announced",
                opportunity_type="equity_raise",
            )
        ],
    )

    assert result.precision_at_10 == 0.0
    assert result.recall == 0.0
    assert result.eligible_outcomes == 1
    assert result.predictions[0].matched_outcome_id is None


@pytest.mark.parametrize(
    ("fired_at", "outcome_date"),
    [
        (date(2024, 4, 2), date(2024, 4, 1)),
        (date(2024, 1, 1), date(2024, 4, 2)),
    ],
)
def test_post_outcome_and_outside_horizon_predictions_receive_no_credit(
    fired_at: date,
    outcome_date: date,
) -> None:
    result = metrics([fired("bank-1", fired_at)], [outcome("bank-1", outcome_date)])

    assert result.matched_outcomes == 0
    assert result.precision_at_10 == 0.0
    assert result.recall == 0.0


def test_correlated_patterns_collapse_to_one_opportunity_prediction() -> None:
    fired_at = date(2024, 3, 1)
    result = metrics(
        [
            fired("bank-1", fired_at, pattern="dcm_high", priority=80),
            fired("bank-1", fired_at, pattern="dcm_low", priority=60),
            fired("bank-1", date(2024, 3, 8), pattern="dcm_high", priority=80),
        ],
        [outcome("bank-1", date(2024, 4, 1))],
    )

    assert result.total_signals == 2
    assert result.total_opportunities == 1
    assert result.matched_outcomes == 1
    assert result.precision_at_10 == 1.0
    assert result.predictions[0].patterns == ("dcm_high", "dcm_low")


def test_precision_at_10_uses_priority_rank_at_each_cutoff() -> None:
    cutoff = date(2024, 3, 1)
    signals = [fired(f"bank-{priority}", cutoff, priority=priority) for priority in range(1, 12)]
    outcomes = [
        outcome("bank-11", date(2024, 4, 1), outcome_id="high-priority-win"),
        outcome("bank-1", date(2024, 4, 1), outcome_id="rank-eleven-win"),
    ]

    result = metrics(signals, outcomes)

    ranks = {prediction.entity_key: prediction.rank_at_cutoff for prediction in result.predictions}
    assert ranks["bank-11"] == 1
    assert ranks["bank-1"] == 11
    assert result.precision_at_10 == 0.1
    assert result.recall == 1.0


def test_decoy_is_a_visible_false_positive_not_a_vacuous_attribution_check() -> None:
    fired_at = date(2024, 3, 1)
    result = metrics(
        [fired("positive", fired_at), fired("decoy", fired_at)],
        [outcome("positive", date(2024, 4, 1))],
    )

    predictions = {prediction.entity_key: prediction for prediction in result.predictions}
    assert predictions["positive"].matched_outcome_id == "outcome-1"
    assert predictions["decoy"].matched_outcome_id is None
    assert result.precision_at_10 == 0.5


def test_one_prediction_cannot_claim_multiple_outcomes() -> None:
    result = metrics(
        [fired("bank-1", date(2024, 3, 1))],
        [
            outcome("bank-1", date(2024, 4, 1), outcome_id="first"),
            outcome("bank-1", date(2024, 5, 1), outcome_id="second"),
        ],
    )

    assert result.matched_outcomes == 1
    assert result.recall == 0.5


def test_cited_document_must_exist_in_as_of_snapshot() -> None:
    class FakeClient:
        async def read_all_assertions_including_superseded(
            self,
            as_of: datetime,
            access: object,
        ) -> list[dict[str, dict[str, object]]]:
            return []

    fake = FakeClient()
    registry = PatternRegistry(
        fake,  # type: ignore[arg-type]
        access=trusted_test_access("wire"),
    )
    backtester = Backtester(fake, registry)  # type: ignore[arg-type]
    cutoff = datetime(2024, 6, 1, tzinfo=UTC)
    leaked = Signal(
        signal_id="future-signal",
        pattern="dcm_high",
        entity_key="bank-1",
        entity_name="Bank 1",
        priority=80,
        fired_at=cutoff,
        as_of=cutoff,
        evidence={"doc": "future-document"},
    )

    with pytest.raises(LeakageError, match="unavailable"):
        asyncio.run(backtester._assert_pin(cutoff, [leaked]))  # noqa: SLF001


def test_evidence_provenance_is_source_and_document_scoped() -> None:
    class FakeClient:
        async def read_all_assertions_including_superseded(
            self,
            as_of: datetime,
            access: object,
        ) -> list[dict[str, dict[str, object]]]:
            return [
                {
                    "a": {
                        "recorded_at": datetime(2024, 5, 1, tzinfo=UTC),
                        "source_id": "allowed-source",
                        "source_doc_id": "shared-doc-id",
                    }
                }
            ]

    fake = FakeClient()
    registry = PatternRegistry(
        fake,  # type: ignore[arg-type]
        access=trusted_test_access("allowed-source"),
    )
    backtester = Backtester(fake, registry)  # type: ignore[arg-type]
    cutoff = datetime(2024, 6, 1, tzinfo=UTC)
    wrong_source = Signal(
        signal_id="wrong-source",
        pattern="dcm_high",
        entity_key="bank-1",
        entity_name="Bank 1",
        priority=80,
        fired_at=cutoff,
        as_of=cutoff,
        evidence={"doc": "shared-doc-id"},
        source_ids=("blocked-source",),
        source_doc_ids=("shared-doc-id",),
    )

    with pytest.raises(LeakageError, match="blocked-source"):
        asyncio.run(backtester._assert_pin(cutoff, [wrong_source]))  # noqa: SLF001


def test_disabled_detector_outcomes_are_not_counted_as_false_negatives() -> None:
    class FakeClient:
        async def read_all_assertions_including_superseded(
            self,
            as_of: datetime,
            access: object,
        ) -> list[dict[str, dict[str, object]]]:
            return []

    class FakeRegistry:
        access = trusted_test_access("wire")

        def pattern_names(self) -> list[str]:
            return ["dcm_high", "equity"]

    class EmptyRunner:
        async def run(
            self,
            as_of: datetime,
            enabled: set[str] | None,
            window_days: int,
        ) -> list[Signal]:
            return []

    result = asyncio.run(
        Backtester(
            FakeClient(),  # type: ignore[arg-type]
            FakeRegistry(),  # type: ignore[arg-type]
            rules=RULES,
            signal_runner=EmptyRunner(),
        ).run(
            START,
            END,
            30,
            outcomes=[
                outcome(
                    "bank-1",
                    date(2024, 4, 1),
                    kind="equity_announced",
                    opportunity_type="equity_raise",
                )
            ],
            enabled={"dcm_high"},
        )
    )

    assert result.eligible_outcomes == 0
    assert result.recall == 0.0
