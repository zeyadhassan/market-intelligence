"""Bounded, non-statistical readiness evaluation for developer-MVP controls."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

REQUIRED_LABELS = frozenset(
    {
        "new",
        "updated",
        "unchanged",
        "resolved",
        "contradicted",
        "ambiguous",
        "superseded",
        "future_effective",
        "late_recorded",
        "incomplete_coverage",
        "wrong_entity",
        "support",
        "contradiction",
        "similar_bank",
        "subsidiary",
        "branch",
        "holdco_opco",
        "issuer_instrument",
        "asset_manager",
        "arabic_legal_name",
        "english_transliteration",
        "revised",
        "withdrawn",
        "cancelled",
        "completed",
        "unavailable_required_source",
        "hidden_refinancing",
        "copied_content",
        "syndicated_content",
        "boilerplate",
        "table",
        "malformed_document",
        "decorative_citation",
        "fabricated_amount",
        "fabricated_currency",
        "fabricated_date",
        "fabricated_status",
        "prompt_injection",
        "model_refusal",
        "malformed_model_output",
        "model_timeout",
        "tool_outage",
        "barrier_crossover",
        "high_degree_entity",
        "signal_disappearance_unknown_outcome",
    }
)


class ReadinessCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    labels: frozenset[str] = Field(min_length=1)
    control: str = Field(min_length=1)
    expected: str = Field(min_length=1)


class ReadinessCaseSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    tier: str = Field(pattern="^regression_fixture$")
    executed_at: AwareDatetime
    limitations: tuple[str, ...] = Field(min_length=1)
    cases: tuple[ReadinessCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_case_ids(self) -> ReadinessCaseSet:
        identities = [case.case_id for case in self.cases]
        if len(identities) != len(set(identities)):
            raise ValueError("readiness case IDs must be unique")
        return self


class ReadinessEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_id: str
    version: str
    case_count: int = Field(gt=0)
    label_count: int = Field(gt=0)
    counts_by_control: dict[str, int]
    missing_required_labels: frozenset[str]
    limitations: tuple[str, ...]
    passed: bool


def evaluate_readiness_cases(path: Path) -> ReadinessEvaluation:
    """Validate case inventory and report only deterministic contract coverage."""

    case_set = ReadinessCaseSet.model_validate_json(path.read_text(encoding="utf-8"))
    observed_labels = frozenset(label for case in case_set.cases for label in case.labels)
    missing = REQUIRED_LABELS - observed_labels
    counts = Counter(case.control for case in case_set.cases)
    return ReadinessEvaluation(
        dataset_id=case_set.dataset_id,
        version=case_set.version,
        case_count=len(case_set.cases),
        label_count=len(observed_labels),
        counts_by_control=dict(sorted(counts.items())),
        missing_required_labels=missing,
        limitations=case_set.limitations,
        passed=not missing,
    )


def canonical_case_bytes(path: Path) -> bytes:
    """Canonical bytes used by the immutable outer dataset manifest."""

    case_set = ReadinessCaseSet.model_validate_json(path.read_text(encoding="utf-8"))
    return json.dumps(
        case_set.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


__all__ = [
    "REQUIRED_LABELS",
    "ReadinessCase",
    "ReadinessCaseSet",
    "ReadinessEvaluation",
    "canonical_case_bytes",
    "evaluate_readiness_cases",
]
