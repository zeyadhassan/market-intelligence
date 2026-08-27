"""Unit tests proving live quality harnesses are real pass/fail gates."""

from evals.extraction_quality import (
    ExtractionQualityThresholds,
    assess_extraction_quality,
)
from evals.opportunity_quality import (
    OpportunityQualityThresholds,
    assess_opportunity_quality,
)
from fi_intel.ingest.extract_pipeline import ExtractionResult
from fi_intel.tools.evidence import (
    EvidenceItem,
    Opportunity,
    OpportunityClaim,
    OpportunityStatus,
)


def extraction_result(
    doc_id: str,
    assertions: int,
    *,
    rejected: int = 0,
) -> ExtractionResult:
    return ExtractionResult(
        doc_id=doc_id,
        assertions_written=assertions,
        offset_rejections=rejected,
        proposed_types=0,
        low_confidence_dropped=0,
    )


def assertion_key(doc_id: str, predicate: str) -> tuple[str, ...]:
    return (
        doc_id,
        predicate,
        "Organization",
        "bank-1",
        "Instrument",
        f"instrument:{doc_id}",
    )


def test_extraction_gate_passes_only_when_accuracy_and_activity_are_nonzero() -> None:
    expected = [assertion_key("doc-1", "ISSUES"), assertion_key("doc-2", "MATURES_ON")]
    report = assess_extraction_quality(
        [extraction_result("doc-1", 1), extraction_result("doc-2", 1)],
        actual=expected,
        expected=expected,
        model_calls=2,
        thresholds=ExtractionQualityThresholds(
            min_precision=0.20,
            min_recall=0.20,
            max_rejection_rate=0.80,
            min_rate_samples=2,
        ),
    )

    assert report.passed
    assert report.precision == 1.0
    assert report.recall == 1.0


def test_extraction_gate_rejects_zero_yield_and_duplicate_hallucinations() -> None:
    zero = assess_extraction_quality(
        [extraction_result("doc-1", 0)],
        actual=[],
        expected=[assertion_key("doc-1", "ISSUES")],
        model_calls=0,
        thresholds=ExtractionQualityThresholds(),
    )
    duplicate = assess_extraction_quality(
        [extraction_result("doc-1", 2)],
        actual=[assertion_key("doc-1", "ISSUES"), assertion_key("doc-1", "ISSUES")],
        expected=[assertion_key("doc-1", "ISSUES")],
        model_calls=1,
        thresholds=ExtractionQualityThresholds(min_precision=0.75, min_recall=1.0),
    )

    assert not zero.passed
    assert any("assertions" in failure for failure in zero.failures)
    assert any("model calls" in failure for failure in zero.failures)
    assert duplicate.precision == 0.5
    assert not duplicate.passed


def evidence() -> EvidenceItem:
    return EvidenceItem(
        evidence_id="wire/doc-1:0-20",
        source_id="wire",
        doc_id="doc-1",
        char_start=0,
        char_end=20,
        excerpt="Bank approved a new programme.",
    )


def opportunity(
    *,
    insufficient: bool = False,
    evidence_ids: list[str] | None = None,
) -> Opportunity:
    citations = evidence_ids or []
    return Opportunity(
        title="Potential debt capital markets mandate",
        entity_key="bank-1",
        status=(
            OpportunityStatus.INSUFFICIENT_EVIDENCE if insufficient else OpportunityStatus.SUPPORTED
        ),
        summary="The bank has a concrete and documented financing need." if citations else "",
        falsifier="The bank cancels the programme or confirms it will not issue debt.",
        evidence_ids=citations,
        claims=(
            [
                OpportunityClaim(
                    text="The bank has a concrete and documented financing need.",
                    evidence_ids=citations,
                    confidence=0.9,
                )
            ]
            if citations
            else []
        ),
        insufficient_evidence=insufficient,
    )


def test_opportunity_gate_accepts_grounded_positive_and_cost_free_abstention() -> None:
    cited = evidence()
    report = assess_opportunity_quality(
        opportunity(evidence_ids=[cited.evidence_id]),
        [cited],
        positive_model_calls=1,
        abstention=opportunity(insufficient=True),
        abstention_evidence=[],
        abstention_model_calls=0,
        thresholds=OpportunityQualityThresholds(
            min_case_lower_bound=0.20,
            min_rate_samples=2,
        ),
    )

    assert report.passed


def test_opportunity_gate_rejects_ungrounded_output_and_expensive_abstention() -> None:
    ungrounded = opportunity(evidence_ids=["wire/invented:0-20"]).model_copy(update={"claims": []})
    report = assess_opportunity_quality(
        ungrounded,
        [evidence()],
        positive_model_calls=0,
        abstention=opportunity(insufficient=False),
        abstention_evidence=[evidence()],
        abstention_model_calls=1,
        thresholds=OpportunityQualityThresholds(),
    )

    assert not report.passed
    assert any("not returned" in failure for failure in report.failures)
    assert any("positive claims" in failure for failure in report.failures)
    assert any("model calls" in failure for failure in report.failures)
    assert any("failed to abstain" in failure for failure in report.failures)


def test_perfect_small_samples_fail_both_real_quality_gates() -> None:
    expected = [assertion_key("doc-1", "ISSUES"), assertion_key("doc-2", "MATURES_ON")]
    extraction = assess_extraction_quality(
        [extraction_result("doc-1", 1), extraction_result("doc-2", 1)],
        actual=expected,
        expected=expected,
        model_calls=2,
        thresholds=ExtractionQualityThresholds(),
    )
    cited = evidence()
    opportunities = assess_opportunity_quality(
        opportunity(evidence_ids=[cited.evidence_id]),
        [cited],
        positive_model_calls=1,
        abstention=opportunity(insufficient=True),
        abstention_evidence=[],
        abstention_model_calls=0,
        thresholds=OpportunityQualityThresholds(),
    )

    assert extraction.precision == 1.0 and not extraction.passed
    assert opportunities.compliance_lower < 0.80 and not opportunities.passed
    assert any("sample count" in failure for failure in extraction.failures)
    assert any("sample count" in failure for failure in opportunities.failures)
