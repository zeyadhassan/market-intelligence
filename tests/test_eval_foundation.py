from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.datasets import (
    DatasetManifest,
    load_and_verify_manifest,
    manifest_digest,
)
from evals.statistics import BinaryCounts, RateGate, evaluate_rate_gate, wilson_interval

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "evals" / "datasets" / "synthetic_foundation_v1.json"


def test_synthetic_dataset_is_verified_but_explicitly_not_production_eligible() -> None:
    manifest = load_and_verify_manifest(MANIFEST, ROOT)
    assert not manifest.production_eligible
    assert len(manifest_digest(MANIFEST)) == 64


def test_manifest_rejects_checksum_tampering(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text("{}", encoding="utf-8")
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["splits"][0]["files"] = [
        {"path": "fixture.json", "sha256": "0" * 64, "records": 1}
    ]
    candidate = tmp_path / "manifest.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum mismatch"):
        load_and_verify_manifest(candidate, tmp_path)


def test_governed_manifest_rejects_entity_leakage_and_shared_owner() -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw.update(
        {
            "tier": "governed_release",
            "separation_axes": ["entity"],
            "splits": [
                {
                    **raw["splits"][0],
                    "split_id": "development",
                    "role": "development",
                    "entity_partition": ["bank-1"],
                },
                {
                    **raw["splits"][0],
                    "split_id": "holdout",
                    "role": "locked_holdout",
                    "entity_partition": ["bank-1"],
                    "files": [
                        {
                            "path": "different.json",
                            "sha256": "d" * 64,
                            "records": 1,
                        }
                    ],
                },
            ],
        }
    )
    with pytest.raises(ValidationError, match="separate ownership"):
        DatasetManifest.model_validate(raw)

    raw["evaluation_owner"] = "independent-model-risk"
    with pytest.raises(ValidationError, match="entity split leakage"):
        DatasetManifest.model_validate(raw)


def test_regression_fixture_cannot_be_relabelled_as_a_governed_quality_set() -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw.update(
        {
            "tier": "governed_release",
            "evaluation_owner": "independent-model-risk",
            "separation_axes": ["time", "entity", "source"],
            "splits": [
                raw["splits"][0],
                {
                    **raw["splits"][0],
                    "split_id": "holdout",
                    "role": "locked_holdout",
                    "entity_partition": ["different-bank"],
                    "source_partition": ["different-source"],
                    "time_start": "2021-01-01",
                    "time_end": "2021-12-31",
                    "files": [
                        {
                            "path": "different.json",
                            "sha256": "d" * 64,
                            "records": 1,
                        }
                    ],
                },
            ],
        }
    )

    with pytest.raises(ValidationError, match="200 labelled documents"):
        DatasetManifest.model_validate(raw)


def test_perfect_tiny_sample_fails_a_confidence_bound_gate() -> None:
    result = evaluate_rate_gate(
        BinaryCounts(successes=2, total=2),
        {},
        RateGate(name="precision", minimum_lower_bound=0.95, minimum_samples=100),
    )
    assert result.overall.rate == 1.0
    assert result.overall.lower < 0.95
    assert not result.passed
    assert any("sample count" in failure for failure in result.failures)


def test_critical_slice_cannot_be_hidden_by_a_strong_aggregate() -> None:
    result = evaluate_rate_gate(
        BinaryCounts(successes=995, total=1_000),
        {
            "source:filings": BinaryCounts(successes=498, total=500),
            "source:ratings": BinaryCounts(successes=40, total=50),
        },
        RateGate(
            name="entity-link-precision",
            minimum_lower_bound=0.95,
            minimum_samples=50,
            required_slices=frozenset({"source:filings", "source:ratings", "language:ar"}),
        ),
    )
    assert not result.passed
    assert any("source:ratings" in failure for failure in result.failures)
    assert any("language:ar" in failure for failure in result.failures)


def test_wilson_interval_converges_with_sample_size() -> None:
    small = wilson_interval(BinaryCounts(successes=10, total=10))
    large = wilson_interval(BinaryCounts(successes=1_000, total=1_000))
    assert large.lower > small.lower
    assert large.upper == 1.0
