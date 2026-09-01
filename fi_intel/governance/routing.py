"""Serving-time model release routing and immutable lineage checks."""

from __future__ import annotations

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict

from fi_intel.governance.model_registry import (
    ModelArtifact,
    ModelComponent,
    ModelRegistry,
    RegistryInvariantError,
)


class ModelCallLineage(BaseModel):
    model_config = ConfigDict(frozen=True)

    release_id: str
    component: ModelComponent
    model_id: str
    artifact_digest: str
    prompt_version: str
    schema_version: str
    contract_digest: str


def contract_digest(
    *,
    prompt_version: str,
    schema_version: str,
    preprocessing_version: str,
    tool_contract_version: str,
    settings: dict[str, object],
) -> str:
    payload = json.dumps(
        {
            "preprocessing_version": preprocessing_version,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "settings": settings,
            "tool_contract_version": tool_contract_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def route_model_release(
    registry: ModelRegistry,
    component: ModelComponent,
    subject_id: str,
    *,
    prompt_version: str,
    schema_version: str,
) -> ModelArtifact:
    artifact = await registry.route(component, subject_id)
    if artifact.component is not component:
        raise RegistryInvariantError("routed release belongs to another component")
    if artifact.prompt_version != prompt_version:
        raise RegistryInvariantError(
            f"routed {component.value} prompt version does not match runtime contract"
        )
    if artifact.schema_version != schema_version:
        raise RegistryInvariantError(
            f"routed {component.value} schema version does not match runtime contract"
        )
    if not artifact.quality_gate_passed:
        raise RegistryInvariantError("routed release has not passed its quality gate")
    return artifact


def model_call_lineage(
    artifact: ModelArtifact,
    *,
    preprocessing_version: str,
    tool_contract_version: str,
    settings: dict[str, object],
) -> ModelCallLineage:
    return ModelCallLineage(
        release_id=str(artifact.release_id),
        component=artifact.component,
        model_id=artifact.model_id,
        artifact_digest=artifact.artifact_digest,
        prompt_version=artifact.prompt_version,
        schema_version=artifact.schema_version,
        contract_digest=contract_digest(
            prompt_version=artifact.prompt_version,
            schema_version=artifact.schema_version,
            preprocessing_version=preprocessing_version,
            tool_contract_version=tool_contract_version,
            settings=settings,
        ),
    )


def configured_model_lineage(
    *,
    component: ModelComponent,
    model_id: str,
    prompt_version: str,
    schema_version: str,
    preprocessing_version: str,
    tool_contract_version: str,
    settings: dict[str, object],
) -> ModelCallLineage:
    """Create stable lineage directly from the effective runtime configuration."""

    identity = json.dumps(
        {
            "component": component.value,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return ModelCallLineage(
        release_id=str(uuid5(NAMESPACE_URL, f"fi-intel:runtime-model:{identity}")),
        component=component,
        model_id=model_id,
        artifact_digest=hashlib.sha256(identity.encode()).hexdigest(),
        prompt_version=prompt_version,
        schema_version=schema_version,
        contract_digest=contract_digest(
            prompt_version=prompt_version,
            schema_version=schema_version,
            preprocessing_version=preprocessing_version,
            tool_contract_version=tool_contract_version,
            settings=settings,
        ),
    )


__all__ = [
    "ModelCallLineage",
    "configured_model_lineage",
    "contract_digest",
    "model_call_lineage",
    "route_model_release",
]
