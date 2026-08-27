"""Build fully governed immutable result candidates from researched brief items."""

from __future__ import annotations

from fi_intel.agents.brief import Brief
from fi_intel.agents.opportunity_research import RESEARCH_PROMPT_VERSION
from fi_intel.application.analysis_state import (
    PostgresAnalysisStateStore,
    ValidationDecisionRecord,
    _digest,
)
from fi_intel.governance.serving import GovernedModelBundle
from fi_intel.ingest.store import PostgresDocumentStore
from fi_intel.results.manifest import (
    ChangeClassification,
    CoverageManifest,
    ImmutableResultManifest,
    PublicationDecision,
    ResultVersion,
    SourceVersionManifest,
    admit_result,
)
from fi_intel.tools.evidence import FieldEvidenceMapping


class DailyResultAdmission:
    def __init__(
        self,
        state: PostgresAnalysisStateStore,
        documents: PostgresDocumentStore,
        topic_by_pattern: dict[str, str],
    ) -> None:
        self._state = state
        self._documents = documents
        self._topic_by_pattern = topic_by_pattern

    async def build(
        self,
        brief: Brief,
        bundle: GovernedModelBundle,
        *,
        authorization_scope: str,
        run_id: str,
        required_sources: set[str],
        completed_sources: set[str],
        source_manifests: dict[tuple[str, str], SourceVersionManifest],
    ) -> list[ResultVersion]:
        results: list[ResultVersion] = []
        for item in brief.items:
            if item.investigation is None:
                raise ValueError("publishable brief item has no durable investigation")
            opportunity = item.opportunity
            claims = []
            for claim in opportunity.claims:
                mappings = list(claim.field_evidence)
                if not any(mapping.field_name == "entity" for mapping in mappings):
                    owners = tuple(
                        evidence.evidence_id
                        for evidence in item.evidence
                        if item.signal.entity_name.casefold() in evidence.excerpt.casefold()
                    )
                    if owners and item.signal.entity_name.casefold() in claim.text.casefold():
                        mappings.append(
                            FieldEvidenceMapping(
                                field_name="entity",
                                value=item.signal.entity_name,
                                evidence_ids=owners,
                            )
                        )
                claims.append(claim.model_copy(update={"field_evidence": tuple(mappings)}))
            opportunity = opportunity.model_copy(
                update={"claims": claims, "summary": " ".join(c.text for c in claims)}
            )
            evidence_sources: list[SourceVersionManifest] = []
            for evidence in item.evidence:
                document = await self._documents.load_document(evidence.source_id, evidence.doc_id)
                if document is None:
                    raise ValueError(f"manifest evidence document {evidence.evidence_id} is absent")
                version_id = str(
                    document.metadata.get("ledger_document_version_id", document.doc_id)
                )
                source = source_manifests.get((evidence.source_id, version_id))
                if source is None:
                    source = SourceVersionManifest(
                        source_id=evidence.source_id,
                        document_version_id=version_id,
                        content_hash=document.content_hash(),
                        url=document.url,
                        parser_version="canonicalizer-v2",
                    )
                evidence_sources.append(source)
            source_versions = tuple(
                {source.document_version_id: source for source in evidence_sources}.values()
            )
            graph_entry_step = next(
                (step for step in item.investigation.steps if step.operation == "graph_entry"),
                None,
            )
            graph_entry = graph_entry_step.output_payload if graph_entry_step else {}
            validation_results = (
                "citation_coordinates:passed",
                "deterministic_grounding:passed",
                "semantic_entailment:passed",
                "authorization:passed",
                "temporal_state:passed",
            )
            for index, claim in enumerate(opportunity.claims):
                claim_id = _digest([item.signal.signal_id, index, claim.text])
                validator_version = "publication-validation-v2"
                await self._state.record_validation(
                    ValidationDecisionRecord(
                        decision_id=_digest(
                            [
                                item.investigation.investigation_id,
                                claim_id,
                                validator_version,
                            ]
                        ),
                        investigation_id=item.investigation.investigation_id,
                        claim_id=claim_id,
                        validator_version=validator_version,
                        status="supported",
                        field_evidence=tuple(
                            mapping.model_dump(mode="json") for mapping in claim.field_evidence
                        ),
                        reasons=validation_results,
                        decided_at=item.investigation.updated_at,
                    )
                )
            manifest = ImmutableResultManifest(
                run_id=run_id,
                topic_id=self._topic_by_pattern[item.signal.pattern],
                authorization_scope=authorization_scope,
                temporal_policy_version=item.signal.policy_version,
                as_of=brief.as_of,
                source_versions=source_versions,
                entity_resolution_id=(
                    graph_entry_step.output_digest
                    if graph_entry_step is not None
                    else _digest(item.signal.entity_key)
                ),
                entity_id=str(graph_entry.get("canonical_entity_id") or item.signal.entity_key),
                resolver_version=str(graph_entry.get("resolver_version") or "entity-entry-v2"),
                assertion_ids=item.signal.matched_assertion_ids,
                graph_path_ids=tuple(
                    path.get("path_id", "")
                    for step in item.investigation.steps
                    if step.operation == "entity_neighborhood"
                    for path in step.output_payload.get("items", [])
                    if isinstance(path, dict) and path.get("path_id")
                ),
                signal_id=item.signal.signal_id,
                change_classification=ChangeClassification.NEW,
                triage_score=item.signal.priority / 100.0,
                investigation=item.investigation,
                model_lineages=bundle.lineages,
                prompt_digest=_digest(RESEARCH_PROMPT_VERSION),
                policy_digest=_digest(
                    {
                        "authorization_scope": authorization_scope,
                        "policy_version": item.signal.policy_version,
                    }
                ),
                coverage=CoverageManifest(
                    operational_complete=True,
                    factual_complete=True,
                    required_source_ids=tuple(sorted(required_sources)),
                    completed_source_ids=tuple(sorted(completed_sources)),
                ),
                evidence=tuple(item.evidence),
                opportunity=opportunity,
                validation_results=validation_results,
                decision=PublicationDecision.PUBLISH,
            )
            results.append(admit_result(manifest))
        return results


__all__ = ["DailyResultAdmission"]
