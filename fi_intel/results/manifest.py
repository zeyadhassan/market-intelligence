"""Complete immutable manifest and deterministic result admission."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from fi_intel.agents.investigation import InvestigationTrajectory
from fi_intel.governance.routing import ModelCallLineage
from fi_intel.tools.evidence import EntailmentStatus, EvidenceItem, Opportunity


class PublicationDecision(StrEnum):
    PUBLISH = "publish"
    HOLD = "hold"
    ABSTAIN = "abstain"


class ChangeClassification(StrEnum):
    NEW = "new"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    WEAKENED = "weakened"
    CONTRADICTED = "contradicted"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"
    HELD = "held"


class SourceVersionManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    document_version_id: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    url: str | None = None
    parser_version: str


class CoverageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    operational_complete: bool
    factual_complete: bool
    required_source_ids: tuple[str, ...]
    completed_source_ids: tuple[str, ...]
    reasons: tuple[str, ...] = ()


class ImmutableResultManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    topic_id: str
    authorization_scope: str
    temporal_policy_version: str
    as_of: AwareDatetime
    source_versions: tuple[SourceVersionManifest, ...]
    entity_resolution_id: str
    entity_id: str
    resolver_version: str
    assertion_ids: tuple[str, ...]
    graph_path_ids: tuple[str, ...] = ()
    signal_id: str
    change_classification: ChangeClassification = ChangeClassification.NEW
    triage_score: float | None = Field(default=None, ge=0.0, le=1.0)
    investigation: InvestigationTrajectory
    model_lineages: tuple[ModelCallLineage, ...]
    prompt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage: CoverageManifest
    evidence: tuple[EvidenceItem, ...]
    opportunity: Opportunity
    validation_results: tuple[str, ...]
    decision: PublicationDecision

    @model_validator(mode="after")
    def _publication_is_complete(self) -> ImmutableResultManifest:
        if self.investigation.signal_id != self.signal_id:
            raise ValueError("manifest signal and investigation do not match")
        if self.decision is PublicationDecision.PUBLISH:
            _validate_publishable(self)
        return self

    def canonical_payload(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    def output_hash(self) -> str:
        return hashlib.sha256(self.canonical_payload().encode()).hexdigest()

    def logical_result_id(self) -> str:
        payload = "|".join(
            [
                self.topic_id,
                self.signal_id,
                self.authorization_scope,
                self.temporal_policy_version,
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def result_version_id(self) -> str:
        return hashlib.sha256(
            f"{self.logical_result_id()}|{self.output_hash()}".encode()
        ).hexdigest()


class ResultVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    result_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_result_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest: ImmutableResultManifest


class ResultAdmissionError(RuntimeError):
    pass


def _validate_publishable(manifest: ImmutableResultManifest) -> None:
    coverage = manifest.coverage
    if not coverage.operational_complete or not coverage.factual_complete:
        raise ValueError("publication requires complete operational and factual coverage")
    required = (
        (manifest.source_versions, "publication requires immutable source versions"),
        (manifest.model_lineages, "publication requires governed model lineages"),
        (manifest.validation_results, "publication requires validation results"),
    )
    for value, message in required:
        if not value:
            raise ValueError(message)
    opportunity = manifest.opportunity
    if opportunity.insufficient_evidence:
        raise ValueError("insufficient-evidence output cannot be published")
    if any(not claim.field_evidence for claim in opportunity.claims):
        raise ValueError("every published claim requires field evidence mappings")
    if any(
        claim.entailment_status is not EntailmentStatus.SUPPORTED for claim in opportunity.claims
    ):
        raise ValueError("every published claim requires supported entailment")
    if {item.evidence_id for item in manifest.evidence} != set(opportunity.evidence_ids):
        raise ValueError("manifest evidence must exactly match opportunity citations")
    if opportunity.falsifier_test is None:
        raise ValueError("publication requires a typed falsifier test")
    mapped_fields = {
        mapping.field_name for claim in opportunity.claims for mapping in claim.field_evidence
    }
    if not {"entity", "predicate"} <= mapped_fields:
        raise ValueError("published claims require entity and predicate mappings")


def admit_result(manifest: ImmutableResultManifest) -> ResultVersion:
    if manifest.decision is not PublicationDecision.PUBLISH:
        raise ResultAdmissionError(
            f"manifest decision {manifest.decision.value!r} is not publishable"
        )
    return ResultVersion(
        result_version_id=manifest.result_version_id(),
        logical_result_id=manifest.logical_result_id(),
        output_hash=manifest.output_hash(),
        manifest=manifest,
    )


__all__ = [
    "CoverageManifest",
    "ChangeClassification",
    "ImmutableResultManifest",
    "PublicationDecision",
    "ResultAdmissionError",
    "ResultVersion",
    "SourceVersionManifest",
    "admit_result",
]
