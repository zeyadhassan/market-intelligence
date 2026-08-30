"""Executable evidence for the bounded developer-MVP case inventory."""

from pathlib import Path

from evals.datasets import load_and_verify_manifest
from evals.developer_mvp_readiness import REQUIRED_LABELS, evaluate_readiness_cases

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "evals" / "datasets" / "developer_mvp_readiness_v1.json"
CASES = ROOT / "evals" / "data" / "developer_mvp_cases_v1.json"


def test_bounded_readiness_evaluation_covers_every_required_case_and_records_limits() -> None:
    manifest = load_and_verify_manifest(MANIFEST, ROOT)
    evaluation = evaluate_readiness_cases(CASES)

    assert not manifest.production_eligible
    assert evaluation.passed
    assert evaluation.missing_required_labels == frozenset()
    assert evaluation.label_count == len(REQUIRED_LABELS)
    assert evaluation.case_count == 15
    assert len(evaluation.counts_by_control) >= 8
    assert any("not live-source recall" in limitation for limitation in evaluation.limitations)
    assert any("statistical" in limitation for limitation in evaluation.limitations)
