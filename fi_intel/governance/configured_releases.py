"""Idempotently materialize evaluated model releases from operator configuration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from fi_intel.agents.entailment import ENTAILMENT_PROMPT_VERSION, ENTAILMENT_SCHEMA_VERSION
from fi_intel.agents.opportunity_research import RESEARCH_PROMPT_VERSION
from fi_intel.config import Settings
from fi_intel.governance.model_registry import (
    ModelArtifact,
    ModelComponent,
    ModelRegistry,
    ModelReleaseSnapshot,
    ReleaseState,
    ReleaseTransition,
)
from fi_intel.ingest.extract import PROMPT_VERSION as EXTRACTION_PROMPT_VERSION
from fi_intel.retrieval.embedders.openai_compatible_embedder import (
    INDEX_IDENTITY_SCHEMA,
    INPUT_PREPROCESSING_VERSION,
)
from fi_intel.retrieval.reranking import RERANKER_PROMPT_VERSION, RERANKER_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ConfiguredReleasePlan:
    artifact: ModelArtifact
    transitions: tuple[ReleaseTransition, ...]


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("configured model timestamps require a timezone")
    return parsed


def _transition_id(release_id: UUID, state: ReleaseState) -> UUID:
    return uuid5(NAMESPACE_URL, f"fi-intel:model-release:{release_id}:{state.value}")


def configured_release_plans(settings: Settings) -> tuple[ConfiguredReleasePlan, ...]:
    """Build immutable active-release histories from the one deployment config."""

    evaluated_at = _timestamp(settings.model_evaluated_at)
    registered_at = _timestamp(settings.model_registered_at)
    embedding_model = settings.embedding_model
    if embedding_model is None:
        raise ValueError("FI_INTEL_EMBEDDING_MODEL is required")
    specifications = (
        (
            ModelComponent.EXTRACTION,
            settings.extraction_release_id,
            settings.extraction_model,
            settings.extraction_artifact_digest,
            EXTRACTION_PROMPT_VERSION,
            "extraction-response-v1",
        ),
        (
            ModelComponent.REASONING,
            settings.reasoning_release_id,
            settings.research_model,
            settings.reasoning_artifact_digest,
            RESEARCH_PROMPT_VERSION,
            "opportunity-v2",
        ),
        (
            ModelComponent.EMBEDDING,
            settings.embedding_release_id,
            embedding_model,
            settings.embedding_artifact_digest,
            INPUT_PREPROCESSING_VERSION,
            INDEX_IDENTITY_SCHEMA,
        ),
        (
            ModelComponent.RERANKER,
            settings.reranker_release_id,
            settings.reranker_model,
            settings.reranker_artifact_digest,
            RERANKER_PROMPT_VERSION,
            RERANKER_SCHEMA_VERSION,
        ),
        (
            ModelComponent.ENTAILMENT,
            settings.entailment_release_id,
            settings.entailment_model,
            settings.entailment_artifact_digest,
            ENTAILMENT_PROMPT_VERSION,
            ENTAILMENT_SCHEMA_VERSION,
        ),
    )
    plans: list[ConfiguredReleasePlan] = []
    for (
        component,
        release_value,
        model_id,
        digest,
        prompt_version,
        schema_version,
    ) in specifications:
        release_id = UUID(release_value)
        artifact = ModelArtifact(
            release_id=release_id,
            component=component,
            model_id=model_id,
            artifact_digest=digest,
            prompt_version=prompt_version,
            schema_version=schema_version,
            evaluation_dataset_digest=settings.model_evaluation_dataset_digest,
            evaluation_report_digest=settings.model_evaluation_report_digest,
            quality_gate_passed=settings.model_quality_gate_passed,
            evaluated_at=evaluated_at,
            created_at=registered_at,
            created_by=settings.model_release_created_by,
        )
        states = (
            (None, ReleaseState.CANDIDATE, 0),
            (ReleaseState.CANDIDATE, ReleaseState.SHADOW, 0),
            (ReleaseState.SHADOW, ReleaseState.CANARY, 10),
            (ReleaseState.CANARY, ReleaseState.ACTIVE, 100),
        )
        transitions = tuple(
            ReleaseTransition(
                transition_id=_transition_id(release_id, requested),
                release_id=release_id,
                from_state=previous,
                to_state=requested,
                rollout_percent=rollout,
                occurred_at=registered_at + timedelta(seconds=index + 1),
                actor=settings.model_release_created_by,
                reason="operator-configured evaluated release",
            )
            for index, (previous, requested, rollout) in enumerate(states)
        )
        plans.append(ConfiguredReleasePlan(artifact=artifact, transitions=transitions))
    return tuple(plans)


async def synchronize_configured_releases(
    settings: Settings,
    registry: ModelRegistry,
) -> tuple[ModelReleaseSnapshot, ...]:
    """Register and promote each configured release; exact retries are idempotent."""

    snapshots: list[ModelReleaseSnapshot] = []
    for plan in configured_release_plans(settings):
        snapshot = await registry.register(plan.artifact, plan.transitions[0])
        for transition in plan.transitions[1:]:
            snapshot = await registry.transition(transition)
        snapshots.append(snapshot)
    return tuple(snapshots)


__all__ = [
    "ConfiguredReleasePlan",
    "configured_release_plans",
    "synchronize_configured_releases",
]
