"""Fail-closed operating-mode policy for the canonical pipeline."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from fi_intel.config import Settings


class AnalysisMode(StrEnum):
    FIXTURE = "fixture"
    SHADOW = "shadow"
    PILOT = "pilot"
    PRODUCTION = "production"


class ExecutionPath(StrEnum):
    FIXTURE_REGRESSION = "fixture_regression"
    UNIFIED_PIPELINE = "unified_pipeline"


class RuntimeCapabilities(BaseModel):
    """The guarantees actually supplied by an entry point."""

    model_config = ConfigDict(frozen=True)

    execution_path: ExecutionPath
    uses_fixture_data: bool = False
    uses_hashing_embeddings: bool = False
    all_models_configured: bool = False
    coverage_computed_server_side: bool = False
    durable_step_store: bool = False
    authoritative_neo4j_writes: bool = False


class RuntimePolicyError(RuntimeError):
    """Raised when an operating claim exceeds the runtime's guarantees."""


def validate_runtime_mode(  # noqa: C901
    mode: AnalysisMode | str,
    capabilities: RuntimeCapabilities,
) -> None:
    """Reject unsafe mode/capability combinations before startup."""

    selected = AnalysisMode(mode)
    errors: list[str] = []
    if selected is AnalysisMode.FIXTURE:
        if capabilities.execution_path is not ExecutionPath.FIXTURE_REGRESSION:
            errors.append("fixture mode requires the explicit fixture regression path")
    else:
        if capabilities.execution_path is not ExecutionPath.UNIFIED_PIPELINE:
            errors.append(f"{selected.value} mode requires the unified pipeline")
        if capabilities.uses_fixture_data:
            errors.append(f"{selected.value} mode cannot use fixture data")
        if capabilities.uses_hashing_embeddings:
            errors.append(f"{selected.value} mode cannot use hashing embeddings")
        if not capabilities.all_models_configured:
            errors.append(f"{selected.value} mode requires explicitly configured models")
        if not capabilities.coverage_computed_server_side:
            errors.append(f"{selected.value} mode requires server-computed coverage")
        if not capabilities.durable_step_store:
            errors.append(f"{selected.value} mode requires durable step state")
        if capabilities.authoritative_neo4j_writes:
            errors.append(f"{selected.value} mode forbids direct authoritative Neo4j writes")

    if errors:
        raise RuntimePolicyError("; ".join(dict.fromkeys(errors)))


def validate_settings_runtime(
    settings: Settings,
    capabilities: RuntimeCapabilities,
) -> None:
    validate_runtime_mode(settings.analysis_mode, capabilities)


__all__ = [
    "AnalysisMode",
    "ExecutionPath",
    "RuntimeCapabilities",
    "RuntimePolicyError",
    "validate_runtime_mode",
    "validate_settings_runtime",
]
