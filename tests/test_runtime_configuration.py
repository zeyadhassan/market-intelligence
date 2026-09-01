"""Contracts for the single operator-owned runtime configuration."""

from pathlib import Path

from fi_intel.application.preflight import canonical_configuration_errors
from fi_intel.config import Settings
from fi_intel.governance.configured_releases import (
    configured_release_plans,
    synchronize_configured_releases,
)
from fi_intel.governance.model_registry import (
    InMemoryModelRegistry,
    ModelComponent,
    ReleaseState,
)


def complete_settings() -> Settings:
    return Settings(
        analysis_mode="shadow",
        llm_base_url="http://host.containers.internal:8001/v1",
        extraction_model="extract-model",
        research_model="reason-model",
        entailment_model="entail-model",
        reranker_model="rerank-model",
        embedding_base_url="https://10.1.94.110:8443/v1",
        embedding_model="nvidia/llama-3.2-nv-embedqa-1b-v2",
        embedding_dim=2048,
        rss_user_agent="Example Bank FI Intelligence fi-intel@example.test",
        coverage_required_source_ids="sa_sama_news",
        covered_entity_leis="506700LOLO7M6V0E4247",
        oidc_issuer="https://identity.example.test/",
        oidc_audience="fi-intel",
        oidc_jwks_url="https://identity.example.test/.well-known/jwks.json",
        access_subject="oidc-user-1",
        access_principal_id="analyst-1",
        model_quality_gate_passed=True,
        model_evaluation_dataset_digest="1" * 64,
        model_evaluation_report_digest="2" * 64,
        model_evaluated_at="2026-08-30T08:00:00+02:00",
        model_registered_at="2026-08-30T08:05:00+02:00",
        model_release_created_by="model-risk-owner",
        extraction_release_id="10000000-0000-0000-0000-000000000001",
        extraction_artifact_digest="3" * 64,
        reasoning_release_id="10000000-0000-0000-0000-000000000002",
        reasoning_artifact_digest="4" * 64,
        embedding_release_id="10000000-0000-0000-0000-000000000003",
        embedding_artifact_digest="5" * 64,
        reranker_release_id="10000000-0000-0000-0000-000000000004",
        reranker_artifact_digest="6" * 64,
        entailment_release_id="10000000-0000-0000-0000-000000000005",
        entailment_artifact_digest="7" * 64,
    )


def test_complete_operator_configuration_passes_preflight() -> None:
    assert canonical_configuration_errors(complete_settings()) == ()


def test_checked_in_template_covers_every_external_runtime_input() -> None:
    template = Path("deploy/app.env.example").read_text(encoding="utf-8")
    required = {
        "FI_INTEL_LLM_BASE_URL",
        "FI_INTEL_LLM_TRUST_ENV",
        "FI_INTEL_LLM_TLS_VERIFY",
        "FI_INTEL_EMBEDDING_BASE_URL",
        "FI_INTEL_EMBEDDING_BASIC_AUTH_PASSWORD",
        "FI_INTEL_EMBEDDING_TRUST_ENV",
        "FI_INTEL_EMBEDDING_TLS_VERIFY",
        "FI_INTEL_MODEL_EVALUATION_DATASET_DIGEST",
        "FI_INTEL_MODEL_EVALUATION_REPORT_DIGEST",
        "FI_INTEL_EXTRACTION_RELEASE_ID",
        "FI_INTEL_REASONING_RELEASE_ID",
        "FI_INTEL_EMBEDDING_RELEASE_ID",
        "FI_INTEL_RERANKER_RELEASE_ID",
        "FI_INTEL_ENTAILMENT_RELEASE_ID",
        "FI_INTEL_RSS_USER_AGENT",
        "FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS",
        "FI_INTEL_COVERED_ENTITY_LEIS",
        "FI_INTEL_OIDC_ISSUER",
        "FI_INTEL_OIDC_AUDIENCE",
        "FI_INTEL_OIDC_JWKS_URL",
        "FI_INTEL_ACCESS_SUBJECT",
        "FI_INTEL_EMAIL_ENABLED",
        "FI_INTEL_EMAIL_RECIPIENT_ALLOWLIST",
        "FI_INTEL_EMAIL_DESTINATION_KEY",
    }
    configured = {
        line.partition("=")[0] for line in template.splitlines() if line.startswith("FI_INTEL_")
    }

    assert required <= configured
    assert "deploy/app.env" in Path(".gitignore").read_text(encoding="utf-8")


def test_preflight_rejects_partial_basic_auth_and_insecure_non_shadow_transport() -> None:
    settings = complete_settings().model_copy(
        update={
            "analysis_mode": "pilot",
            "embedding_basic_auth_username": "ollama",
            "embedding_tls_verify": False,
            "llm_tls_verify": False,
        }
    )

    errors = canonical_configuration_errors(settings)

    assert any("FI_INTEL_EMBEDDING_BASIC_AUTH_PASSWORD" in error for error in errors)
    assert "FI_INTEL_LLM_TLS_VERIFY must be true outside shadow mode" in errors
    assert "FI_INTEL_EMBEDDING_TLS_VERIFY must be true outside shadow mode" in errors


async def test_configured_model_releases_are_complete_active_and_idempotent() -> None:
    settings = complete_settings()
    plans = configured_release_plans(settings)
    registry = InMemoryModelRegistry()

    first = await synchronize_configured_releases(settings, registry)
    second = await synchronize_configured_releases(settings, registry)

    assert {plan.artifact.component for plan in plans} == set(ModelComponent)
    assert all(
        tuple(transition.to_state for transition in plan.transitions)
        == (
            ReleaseState.CANDIDATE,
            ReleaseState.SHADOW,
            ReleaseState.CANARY,
            ReleaseState.ACTIVE,
        )
        for plan in plans
    )
    assert first == second
    assert all(snapshot.state is ReleaseState.ACTIVE for snapshot in first)
