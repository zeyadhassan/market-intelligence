"""Source-lineage to canonical page and email immutable-version contract."""

from datetime import UTC, date, datetime

from fi_intel.agents.investigation import InvestigationState, InvestigationTrajectory
from fi_intel.api.stage_one_postgres import PostgresStageOneService
from fi_intel.application.delivery import _render_digest
from fi_intel.governance.model_registry import ModelComponent
from fi_intel.governance.routing import ModelCallLineage
from fi_intel.results.manifest import (
    CoverageManifest,
    ImmutableResultManifest,
    PublicationDecision,
    SourceVersionManifest,
    admit_result,
)
from fi_intel.tools.evidence import (
    EntailmentStatus,
    EvidenceItem,
    EvidenceStrength,
    FalsifierTest,
    FieldEvidenceMapping,
    Opportunity,
    OpportunityClaim,
)

NOW = datetime(2026, 8, 27, 6, 0, tzinfo=UTC)


def _admitted_manifest() -> ImmutableResultManifest:
    evidence = EvidenceItem(
        evidence_id="gcc-exchange/notice-42:10-73",
        source_id="gcc-exchange",
        doc_id="notice-42",
        char_start=10,
        char_end=73,
        excerpt="Example Bank has a USD 500 million maturity due in March 2027.",
        source_url="https://example.test/notices/42",
        source_version_id="document-version-42",
        content_hash="a" * 64,
        lexical_score=0.8,
        vector_score=0.7,
        reranker_score=0.9,
    )
    claim = OpportunityClaim(
        text="Example Bank has a USD 500 million maturity due in March 2027.",
        evidence_ids=[evidence.evidence_id],
        confidence=0.9,
        field_evidence=(
            FieldEvidenceMapping(
                field_name="entity",
                value="Example Bank",
                evidence_ids=(evidence.evidence_id,),
            ),
            FieldEvidenceMapping(
                field_name="predicate",
                value="has upcoming maturity",
                evidence_ids=(evidence.evidence_id,),
            ),
        ),
        entailment_status=EntailmentStatus.SUPPORTED,
    )
    opportunity = Opportunity(
        title="Example Bank upcoming maturity",
        signal_id="signal-42",
        entity_key="entity-42",
        summary="A supported refinancing discussion is timely before the March 2027 maturity.",
        falsifier="A completed refinancing is recorded.",
        falsifier_test=FalsifierTest(condition="completed refinancing is recorded"),
        evidence_ids=[evidence.evidence_id],
        claims=[claim],
        evidence_strength=EvidenceStrength.STRONG,
        uncertainty_category="bounded_source_scope",
    )
    return ImmutableResultManifest(
        run_id="run-42",
        topic_id="upcoming-maturities",
        authorization_scope="scope-public",
        temporal_policy_version="bitemporal-v1",
        as_of=NOW,
        source_versions=(
            SourceVersionManifest(
                source_id="gcc-exchange",
                document_version_id="document-version-42",
                content_hash="a" * 64,
                url="https://example.test/notices/42",
                parser_version="html-v1",
            ),
        ),
        entity_resolution_id="resolution-42",
        entity_id="entity-42",
        resolver_version="entity-v2",
        assertion_ids=("assertion-42",),
        graph_path_ids=("path-42",),
        signal_id="signal-42",
        triage_score=0.9,
        investigation=InvestigationTrajectory(
            investigation_id="investigation-42",
            run_id="run-42",
            signal_id="signal-42",
            policy_version="bounded-investigation-v1",
            state=InvestigationState.PUBLISHED,
            started_at=NOW,
            updated_at=NOW,
        ),
        model_lineages=(
            ModelCallLineage(
                release_id="reasoning-release-v1",
                component=ModelComponent.REASONING,
                model_id="governed-reasoner",
                artifact_digest="b" * 64,
                prompt_version="opportunity-v1",
                schema_version="opportunity-v2",
                contract_digest="c" * 64,
            ),
        ),
        prompt_digest="d" * 64,
        policy_digest="e" * 64,
        coverage=CoverageManifest(
            operational_complete=True,
            factual_complete=True,
            required_source_ids=("gcc-exchange",),
            completed_source_ids=("gcc-exchange",),
        ),
        evidence=(evidence,),
        opportunity=opportunity,
        validation_results=("citation:passed", "entailment:passed"),
        decision=PublicationDecision.PUBLISH,
    )


def test_source_coordinate_page_and_email_share_one_admitted_result_version() -> None:
    result = admit_result(_admitted_manifest())
    page = PostgresStageOneService._view(
        result.manifest,
        result.result_version_id,
        None,
        lifecycle_state="new",
    )
    _, text_body, html_body = _render_digest(
        date(2026, 8, 27),
        [(result.manifest.topic_id, result.result_version_id, "new")],
        {result.result_version_id: result.manifest},
        include_nothing_new=False,
        link_only=False,
    )

    source = result.manifest.source_versions[0]
    assert page.result_id == result.result_version_id
    assert page.title == result.manifest.opportunity.title
    assert page.summary == result.manifest.opportunity.summary
    assert page.evidence[0].quote == result.manifest.evidence[0].excerpt
    assert page.evidence[0].content_hash == source.content_hash
    assert result.result_version_id in text_body
    assert result.result_version_id in html_body
    assert "http://localhost:8000/stage-one" in html_body
    assert page.title in text_body and page.title in html_body
    assert page.summary in text_body and page.summary in html_body
