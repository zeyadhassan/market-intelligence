"""Contract tests for the bounded, evidence-first production foundations."""

from datetime import UTC, datetime, timedelta

import pytest

from fi_intel.agents.grounding import ground_claim
from fi_intel.agents.investigation import (
    InMemoryInvestigationStore,
    InvestigationConflictError,
    InvestigationPolicy,
    InvestigationSession,
    InvestigationState,
    RepeatedInvestigationStepError,
    StepStatus,
    StopReason,
)
from fi_intel.governance.model_registry import ModelComponent
from fi_intel.governance.routing import ModelCallLineage
from fi_intel.graph.coverage import (
    CoverageRequest,
    FactualCoverageContract,
    FactualCoverageState,
    SourceOperationsCoverageProvider,
)
from fi_intel.graph.queries import CoverageScope
from fi_intel.results.manifest import (
    CoverageManifest,
    ImmutableResultManifest,
    PublicationDecision,
    SourceVersionManifest,
    admit_result,
)
from fi_intel.runtime import (
    AnalysisMode,
    ExecutionPath,
    RuntimeCapabilities,
    RuntimePolicyError,
    validate_runtime_mode,
)
from fi_intel.sources.operations import InMemorySourceOperationsStore
from fi_intel.temporal import parse_aware_datetime
from fi_intel.tools.evidence import (
    EntailmentStatus,
    EvidenceItem,
    FalsifierTest,
    FieldEvidenceMapping,
    Opportunity,
    OpportunityClaim,
)

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


def test_every_nonfixture_mode_rejects_ungoverned_capabilities() -> None:
    capabilities = RuntimeCapabilities(
        execution_path=ExecutionPath.FIXTURE_REGRESSION,
        uses_hashing_embeddings=True,
        all_models_configured=False,
        coverage_computed_server_side=False,
        durable_step_store=False,
    )

    with pytest.raises(RuntimePolicyError, match="unified pipeline"):
        validate_runtime_mode(AnalysisMode.PRODUCTION, capabilities)

    with pytest.raises(RuntimePolicyError, match="unified pipeline"):
        validate_runtime_mode(AnalysisMode.SHADOW, capabilities)


def test_offset_timestamp_is_converted_to_utc_instead_of_relabelled() -> None:
    parsed = parse_aware_datetime("2026-08-26T12:00:00+02:00")

    assert parsed == datetime(2026, 8, 26, 10, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_aware_datetime("2026-08-26T12:00:00")


def test_grounding_rejects_decorative_citation_and_fabricated_amount() -> None:
    evidence = EvidenceItem(
        evidence_id="wire/doc:0-30",
        source_id="wire",
        doc_id="doc",
        char_start=0,
        char_end=30,
        excerpt="Example Bank released annual results.",
    )

    decision = ground_claim("Example Bank refinanced USD 500 million.", [evidence])

    assert decision.supported is False
    assert any("USD" in reason or "500" in reason for reason in decision.reasons)
    assert any("predicate" in reason for reason in decision.reasons)


async def test_investigation_trace_is_bounded_append_only_and_stops() -> None:
    store = InMemoryInvestigationStore()
    policy = InvestigationPolicy(max_steps=2, max_tool_calls=2)
    session = await InvestigationSession.start(
        run_id="run-1",
        signal_id="signal-1",
        store=store,
        policy=policy,
    )

    result = await session.run_step(
        "entity_profile",
        {"entity_key": "LEI-1"},
        lambda: _return({"assertions": 1}),
    )
    trajectory = await session.finish(InvestigationState.SUPPORTED, StopReason.SUPPORTED)

    assert result == {"assertions": 1}
    assert trajectory.state is InvestigationState.SUPPORTED
    assert trajectory.steps[0].input_digest
    assert await store.load(trajectory.investigation_id) == trajectory

    replay = await InvestigationSession.start(
        run_id="run-1",
        signal_id="signal-1",
        store=store,
        policy=policy,
    )
    with pytest.raises(RepeatedInvestigationStepError):
        await replay.run_step(
            "entity_profile",
            {"entity_key": "LEI-1"},
            lambda: _return({"assertions": 1}),
        )
    with pytest.raises(InvestigationConflictError, match="terminal investigation"):
        await replay.finish(InvestigationState.HELD, StopReason.POLICY_REJECTED)


async def _return(value: object) -> object:
    return value


async def test_investigation_retries_retryable_steps_and_records_each_attempt() -> None:
    store = InMemoryInvestigationStore()
    policy = InvestigationPolicy(
        max_steps=3,
        max_tool_calls=3,
        max_attempts_per_step=2,
        retry_base_delay_ms=0,
    )
    session = await InvestigationSession.start(
        run_id="run-retry",
        signal_id="signal-retry",
        store=store,
        policy=policy,
    )
    calls = 0

    async def flaky() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary dependency failure")
        return {"recovered": True}

    result = await session.run_step("entity_profile", {"entity_key": "LEI-1"}, flaky)

    assert result == {"recovered": True}
    assert calls == 2
    assert [step.status for step in session.trajectory.steps] == [
        StepStatus.FAILED_RETRYABLE,
        StepStatus.SUCCEEDED,
    ]
    summary = session.trajectory.steps[0].safe_error_summary or ""
    assert summary.startswith("RuntimeError (message_sha256=")
    assert "temporary dependency failure" not in summary


async def test_negative_inference_requires_entity_specific_factual_contract() -> None:
    provider = SourceOperationsCoverageProvider(
        InMemorySourceOperationsStore(),
        required_source_ids={},
        covered_entity_keys=frozenset(),
        factual_contracts=(
            FactualCoverageContract(
                pattern_name="maturity_wall_no_refi",
                entity_key="BANK-LEI",
                subject_key="ISIN-1",
                required_source_ids=frozenset({"issuer-ir"}),
                source_classes=frozenset({"issuer_ir"}),
                window_start=NOW - timedelta(days=30),
                window_end=NOW + timedelta(days=1),
                state=FactualCoverageState.COMPLETE,
                reconciled_at=NOW - timedelta(hours=1),
                policy_version="coverage-v1",
            ),
        ),
    )
    request = CoverageRequest(
        pattern_name="maturity_wall_no_refi",
        entity_key="BANK-LEI",
        factual_subject_key="ISIN-1",
        as_of=NOW,
        freshness_days=180,
        allowed_source_ids=frozenset({"issuer-ir"}),
        scopes=frozenset({CoverageScope.FACTUAL_ENTITY}),
    )

    assert (await provider.assess(request)).complete is True
    missing = request.model_copy(update={"factual_subject_key": "ISIN-2"})
    decision = await provider.assess(missing)
    assert decision.complete is False
    assert "no as-of factual completeness contract" in decision.reasons[0]


async def test_result_admission_uses_manifest_coverage_and_is_deterministic() -> None:
    store = InMemoryInvestigationStore()
    session = await InvestigationSession.start(
        run_id="run-2",
        signal_id="signal-2",
        store=store,
        policy=InvestigationPolicy(),
    )
    trajectory = await session.finish(InvestigationState.SUPPORTED, StopReason.SUPPORTED)
    evidence_id = "wire/doc-1:0-20"
    opportunity = Opportunity(
        title="Bank approved programme",
        signal_id="signal-2",
        entity_key="LEI-2",
        summary="Bank approved a programme.",
        falsifier="The programme is withdrawn.",
        falsifier_test=FalsifierTest(condition="The programme is withdrawn."),
        evidence_ids=[evidence_id],
        claims=[
            OpportunityClaim(
                text="Bank approved a programme.",
                evidence_ids=[evidence_id],
                confidence=0.9,
                field_evidence=(
                    FieldEvidenceMapping(
                        field_name="predicate",
                        value="approved",
                        evidence_ids=(evidence_id,),
                    ),
                    FieldEvidenceMapping(
                        field_name="entity",
                        value="Bank",
                        evidence_ids=(evidence_id,),
                    ),
                ),
                entailment_status=EntailmentStatus.SUPPORTED,
            )
        ],
    )
    base = {
        "run_id": "run-2",
        "topic_id": "issuance-programmes",
        "authorization_scope": "scope-1",
        "temporal_policy_version": "temporal-v1",
        "as_of": NOW,
        "source_versions": (
            SourceVersionManifest(
                source_id="wire",
                document_version_id="doc-version-1",
                content_hash="a" * 64,
                parser_version="parser-v1",
            ),
        ),
        "entity_resolution_id": "resolution-1",
        "entity_id": "LEI-2",
        "resolver_version": "entity-v2",
        "assertion_ids": ("assertion-1",),
        "signal_id": "signal-2",
        "investigation": trajectory,
        "model_lineages": (
            ModelCallLineage(
                release_id="release-1",
                component=ModelComponent.REASONING,
                model_id="reasoner-1",
                artifact_digest="b" * 64,
                prompt_version="research-v2",
                schema_version="opportunity-v2",
                contract_digest="c" * 64,
            ),
        ),
        "prompt_digest": "d" * 64,
        "policy_digest": "e" * 64,
        "opportunity": opportunity,
        "evidence": (
            EvidenceItem(
                evidence_id=evidence_id,
                source_id="wire",
                doc_id="doc-1",
                char_start=0,
                char_end=20,
                excerpt="Bank approved programme",
            ),
        ),
        "validation_results": ("grounding:passed", "entailment:passed"),
        "decision": PublicationDecision.PUBLISH,
    }
    with pytest.raises(ValueError, match="complete operational and factual coverage"):
        ImmutableResultManifest.model_validate(
            {
                **base,
                "coverage": CoverageManifest(
                    operational_complete=True,
                    factual_complete=False,
                    required_source_ids=("wire",),
                    completed_source_ids=("wire",),
                ),
            }
        )

    manifest = ImmutableResultManifest.model_validate(
        {
            **base,
            "coverage": CoverageManifest(
                operational_complete=True,
                factual_complete=True,
                required_source_ids=("wire",),
                completed_source_ids=("wire",),
            ),
        }
    )
    first = admit_result(manifest)
    second = admit_result(manifest)
    assert first == second
    assert first.result_version_id == manifest.result_version_id()
