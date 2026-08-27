"""Database-free tests for governed patterns, ranking, and lifecycle."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from fi_intel.config import Settings
from fi_intel.governance.policy import trusted_test_access
from fi_intel.graph.client import GraphClient
from fi_intel.graph.properties import TypedPropertyError, project_typed_properties
from fi_intel.graph.queries import (
    ALL_PATTERNS,
    LEADERSHIP,
    MATURITY_WALL,
    PROGRAMME,
    Pattern,
    PatternDeploymentState,
)
from fi_intel.graph.registry import PatternRegistry
from fi_intel.graph.signals import (
    SignalLifecycleSnapshot,
    SignalLifecycleState,
    classify_lifecycle,
    rescore_for_lifecycle,
    score_signal,
    signal_authorization_scope,
    stable_signal_id,
)

NOW = datetime(2024, 6, 1, tzinfo=UTC)


def _programme_row(
    *,
    source_ids: list[str] | None = None,
    source_doc_ids: list[str] | None = None,
    barrier_sides: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "entity_key": "bank-1",
        "entity_name": "Bank One",
        "programme_key": "programme-1",
        "currency": "usd",
        "doc": "doc-1",
        "_assertion_ids": ["assertion-1"],
        "_source_ids": source_ids or ["wire-a"],
        "_source_doc_ids": source_doc_ids or ["doc-1"],
        "_barrier_sides": barrier_sides or ["public"],
        "_latest_recorded_at": NOW,
        "_materiality_score": 0.8,
        "_evidence_confidence": 0.9,
    }


def test_pattern_assets_have_complete_governed_metadata() -> None:
    for pattern in ALL_PATTERNS:
        assert pattern.version.count(".") == 2
        assert pattern.precision_lineage == pattern.version.split(".", 1)[0]
        assert pattern.hypothesis
        assert pattern.eligible_outcome_kinds
        assert pattern.required_claim_types
        assert pattern.required_attributes
        assert pattern.materiality_thresholds
        assert pattern.coverage_prerequisites
        assert pattern.owner != pattern.reviewer
        assert pattern.deployment_state in {
            PatternDeploymentState.SHADOW,
            PatternDeploymentState.PILOT,
            PatternDeploymentState.ACTIVE,
        }
        assert pattern.deployable is (
            pattern.deployment_state
            in {PatternDeploymentState.PILOT, PatternDeploymentState.ACTIVE}
        )
        if pattern.deployment_state is PatternDeploymentState.ACTIVE:
            assert pattern.priority >= Settings().triage_priority_threshold
        assert "properties_json CONTAINS" not in pattern.cypher
        assert "$freshness_days" in pattern.cypher


def test_pattern_contract_rejects_json_substring_queries() -> None:
    payload = PROGRAMME.model_dump()
    payload["cypher_template"] = PROGRAMME.cypher_template.replace(
        "a.fact_status = 'approved'",
        "a.properties_json CONTAINS 'approved'",
    )
    with pytest.raises(ValidationError, match="typed assertion properties"):
        Pattern.model_validate(payload)


def test_thresholds_and_currency_scope_are_rendered_from_metadata() -> None:
    threshold = MATURITY_WALL.materiality_thresholds[0]
    revised = MATURITY_WALL.model_copy(
        update={
            "materiality_thresholds": (
                threshold.model_copy(update={"value": threshold.value + 50.0}),
            ),
            "allowed_currencies": ("USD", "SAR"),
        }
    )

    assert "$materiality_threshold_0" in revised.cypher
    assert "fact_amount_usd_mn >= 250.0" not in revised.cypher
    assert revised.query_parameters["materiality_threshold_0"] == 300.0
    assert "fact_currency IN $allowed_currencies" in revised.cypher
    assert revised.query_parameters["allowed_currencies"] == ["usd", "sar"]
    assert revised.cypher == MATURITY_WALL.cypher


def test_typed_property_projection_is_explicit_and_strict() -> None:
    projected = project_typed_properties(
        {
            "direction": " Negative ",
            "value": "12.1",
            "marketed": "false",
            "vendor_free_text": "does not become queryable",
        }
    )
    assert projected == {
        "fact_direction": "negative",
        "fact_value": 12.1,
        "fact_marketed": False,
    }
    with pytest.raises(TypedPropertyError, match="numeric"):
        project_typed_properties({"value": "twelve"})
    with pytest.raises(TypedPropertyError, match="boolean"):
        project_typed_properties({"marketed": "maybe"})


async def test_registry_revalidates_pattern_row_provenance_and_entitlement() -> None:
    client = SimpleNamespace(audit_access=AsyncMock())
    registry = PatternRegistry(
        cast(GraphClient, client),
        patterns=(PROGRAMME,),
        access=trusted_test_access("wire-a"),
    )
    candidate = await registry._candidate(PROGRAMME, _programme_row())  # noqa: SLF001
    assert candidate.assertion_ids == ("assertion-1",)
    client.audit_access.assert_awaited_once()

    with pytest.raises(RuntimeError, match="misaligned provenance"):
        await registry._candidate(  # noqa: SLF001
            PROGRAMME,
            _programme_row(source_doc_ids=["doc-1", "doc-2"]),
        )
    with pytest.raises(PermissionError, match="unauthorized sources"):
        await registry._candidate(  # noqa: SLF001
            PROGRAMME,
            _programme_row(source_ids=["wire-b"]),
        )
    with pytest.raises(PermissionError, match="public barrier"):
        await registry._candidate(  # noqa: SLF001
            PROGRAMME,
            _programme_row(barrier_sides=["private"]),
        )


def test_signal_identity_is_stable_across_evidence_but_episode_and_scope_sensitive() -> None:
    public_scope = signal_authorization_scope("public-desk", "public", ["wire-a"])
    identity = stable_signal_id(
        PROGRAMME,
        "bank-1",
        {"programme_key": "programme-1", "currency": "usd"},
        public_scope,
    )
    reordered = stable_signal_id(
        PROGRAMME,
        "bank-1",
        {"currency": "usd", "programme_key": "programme-1"},
        public_scope,
    )
    changed_episode = stable_signal_id(
        PROGRAMME,
        "bank-1",
        {"programme_key": "programme-2", "currency": "usd"},
        public_scope,
    )
    private_scope = stable_signal_id(
        PROGRAMME,
        "bank-1",
        {"programme_key": "programme-1", "currency": "usd"},
        signal_authorization_scope("private-desk", "private", ["wire-a"]),
    )
    changed_grants_scope = stable_signal_id(
        PROGRAMME,
        "bank-1",
        {"programme_key": "programme-1", "currency": "usd"},
        signal_authorization_scope("public-desk", "public", ["wire-b"]),
    )
    assert identity == reordered
    assert identity != changed_episode
    assert identity != private_scope
    assert identity != changed_grants_scope


def test_lifecycle_state_machine_tracks_material_change_and_suppression() -> None:
    new = classify_lifecycle(None, 0.6, NOW)
    assert new.state is SignalLifecycleState.NEW

    prior = SignalLifecycleSnapshot(
        state=new.state,
        opened_at=new.opened_at,
        updated_at=new.updated_at,
        last_confirmed_at=new.last_confirmed_at,
        score_anchor=new.score_anchor,
        policy_version="policy-v1",
    )
    unchanged = classify_lifecycle(prior, 0.62, NOW + timedelta(days=1))
    assert unchanged.state is SignalLifecycleState.UNCHANGED
    assert unchanged.updated_at == NOW
    strengthened = classify_lifecycle(prior, 0.66, NOW + timedelta(days=1))
    assert strengthened.state is SignalLifecycleState.STRENGTHENED
    weakened = classify_lifecycle(prior, 0.54, NOW + timedelta(days=1))
    assert weakened.state is SignalLifecycleState.WEAKENED

    suppressed_prior = prior.model_copy(update={"state": SignalLifecycleState.SUPPRESSED})
    suppressed = classify_lifecycle(suppressed_prior, 0.9, NOW + timedelta(days=1))
    assert suppressed.state is SignalLifecycleState.SUPPRESSED
    with pytest.raises(ValueError, match="out of order"):
        classify_lifecycle(prior, 0.6, NOW - timedelta(seconds=1))


def test_score_exposes_every_weighted_contribution() -> None:
    base, score, contributions = score_signal(
        PROGRAMME,
        as_of=NOW,
        latest_recorded_at=NOW - timedelta(days=10),
        materiality_score=1.0,
        evidence_confidence=0.9,
        assertion_ids=("a-1",),
        source_ids=("wire",),
        lifecycle_state=SignalLifecycleState.NEW,
        historical_precision=0.8,
        precision_samples=30,
    )
    assert 0.0 < base <= score <= 1.0
    assert {item.component for item in contributions} == {
        "pattern_prior",
        "historical_precision",
        "materiality",
        "freshness",
        "claim_coverage",
        "evidence_confidence",
        "source_agreement",
        "novelty",
    }
    assert score == pytest.approx(sum(item.weighted_value for item in contributions))
    assert all(item.explanation for item in contributions)
    precision = next(item for item in contributions if item.component == "historical_precision")
    assert precision.raw_value == 0.8
    assert "30 authorized analyst outcomes" in precision.explanation

    suppressed_score, suppressed_contributions = rescore_for_lifecycle(
        base,
        contributions,
        SignalLifecycleState.SUPPRESSED,
    )
    assert suppressed_score == base
    assert sum(item.weighted_value for item in suppressed_contributions) == pytest.approx(base)

    resolved_score, resolved_contributions = rescore_for_lifecycle(
        base,
        contributions,
        SignalLifecycleState.RESOLVED,
    )
    assert resolved_score == pytest.approx(base + 0.025)
    assert sum(item.weighted_value for item in resolved_contributions) == pytest.approx(
        resolved_score
    )


def test_material_maturity_outranks_routine_leadership_change() -> None:
    common = {
        "as_of": NOW,
        "latest_recorded_at": NOW,
        "evidence_confidence": 0.9,
        "source_ids": ("wire",),
        "lifecycle_state": SignalLifecycleState.NEW,
    }
    _, maturity_score, _ = score_signal(
        MATURITY_WALL,
        materiality_score=1.0,
        assertion_ids=("maturity", "issue"),
        **common,
    )
    _, leadership_score, _ = score_signal(
        LEADERSHIP,
        materiality_score=LEADERSHIP.default_materiality_score,
        assertion_ids=("leadership",),
        **common,
    )

    assert maturity_score > leadership_score
    assert (maturity_score - leadership_score) * 100 >= (
        MATURITY_WALL.priority - LEADERSHIP.priority
    )


def test_early_precision_feedback_contributes_proportionally() -> None:
    _, _, contributions = score_signal(
        PROGRAMME,
        as_of=NOW,
        latest_recorded_at=NOW,
        materiality_score=1.0,
        evidence_confidence=0.9,
        assertion_ids=("a-1",),
        source_ids=("wire",),
        lifecycle_state=SignalLifecycleState.NEW,
        historical_precision=5 / 7,
        precision_samples=5,
    )
    precision = next(item for item in contributions if item.component == "historical_precision")

    assert precision.weight == pytest.approx(0.15 * 5 / 30)
    assert precision.weighted_value > 0.0
