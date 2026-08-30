"""Material-change identity contracts across daily analysis windows."""

from datetime import UTC, datetime, timedelta

from fi_intel.application.opportunities import (
    PostgresOpportunityRepository,
    material_result_fingerprint,
)
from fi_intel.results.manifest import ImmutableResultManifest
from fi_intel.tools.evidence import EvidenceItem, Opportunity

NOW = datetime(2026, 8, 27, tzinfo=UTC)


def _manifest(*, excerpt: str = "Bank has a USD 500m maturity.") -> ImmutableResultManifest:
    evidence = EvidenceItem(
        evidence_id="source/doc:0-32",
        source_id="source",
        doc_id="doc",
        char_start=0,
        char_end=32,
        excerpt=excerpt,
        content_hash="a" * 64,
        lexical_score=0.8,
        vector_score=0.7,
        reranker_score=0.9,
    )
    opportunity = Opportunity(
        title="Upcoming maturity",
        signal_id="signal-1",
        entity_key="entity-1",
        summary="The bank has an upcoming maturity.",
        falsifier="A completed refinancing is observed.",
        evidence_ids=[evidence.evidence_id],
    )
    # This test exercises lifecycle fingerprint scope, not publication
    # validation; fields unused by the fingerprint may remain lightweight.
    return ImmutableResultManifest.model_construct(
        run_id="run-1",
        topic_id="upcoming-maturities",
        authorization_scope="scope-1",
        temporal_policy_version="temporal-v1",
        as_of=NOW,
        source_versions=(),
        entity_resolution_id="resolution-1",
        entity_id="entity-1",
        resolver_version="entity-v2",
        assertion_ids=("assertion-1",),
        signal_id="signal-1",
        investigation=None,
        model_lineages=(),
        prompt_digest="b" * 64,
        policy_digest="c" * 64,
        coverage=None,
        evidence=(evidence,),
        opportunity=opportunity,
        triage_score=0.8,
        validation_results=("grounding:passed",),
        decision="publish",
    )


def test_daily_run_volatility_does_not_create_a_material_update() -> None:
    first = _manifest()
    next_window = first.model_copy(update={"run_id": "run-2", "as_of": NOW + timedelta(days=1)})

    assert material_result_fingerprint(first) == material_result_fingerprint(next_window)
    assert PostgresOpportunityRepository.identity(first) == PostgresOpportunityRepository.identity(
        next_window
    )


def test_exact_evidence_change_creates_a_material_update() -> None:
    assert material_result_fingerprint(_manifest()) != material_result_fingerprint(
        _manifest(excerpt="Bank completed the USD 500m refinancing.")
    )
