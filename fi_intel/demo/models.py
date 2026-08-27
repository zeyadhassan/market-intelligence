"""Structured result contracts for the service-free POC demo."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from fi_intel.agents.brief import Brief
from fi_intel.graph.signals import Signal


class POCStageSummary(BaseModel):
    """One measurable stage in the local vertical slice."""

    model_config = ConfigDict(frozen=True)

    stage: str = Field(min_length=1)
    processed: int = Field(ge=0)
    emitted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    measurements: dict[str, JsonValue] = Field(default_factory=dict)


class POCEvaluation(BaseModel):
    """Fixture-labelled quality checks, not a production-quality estimate."""

    model_config = ConfigDict(frozen=True)

    expected_positive_patterns: tuple[str, ...]
    observed_positive_patterns: tuple[str, ...]
    missing_positive_patterns: tuple[str, ...]
    unexpected_positive_patterns: tuple[str, ...]
    decoy_signal_count: int = Field(ge=0)
    lookahead_violation_count: int = Field(ge=0)
    citation_failure_count: int = Field(ge=0)
    pattern_precision: float = Field(ge=0.0, le=1.0)
    pattern_recall: float = Field(ge=0.0, le=1.0)
    passed: bool


class POCDemoReport(BaseModel):
    """Serializable report returned by :func:`run_poc_demo`."""

    model_config = ConfigDict(frozen=True)

    label: str = "POC deterministic fixture evaluation"
    as_of: datetime
    fixture_source: str
    heuristic_components: tuple[str, ...]
    stages: tuple[POCStageSummary, ...]
    signals: tuple[Signal, ...]
    brief: Brief
    evaluation: POCEvaluation
