"""Contracts for the single operator-owned runtime configuration."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fi_intel.application.preflight import canonical_configuration_errors
from fi_intel.cli import app
from fi_intel.config import Settings


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
        "FI_INTEL_EMBEDDING_MODEL",
        "FI_INTEL_EMBEDDING_DIM",
        "FI_INTEL_EMBEDDING_BATCH_SIZE",
        "FI_INTEL_EMBEDDING_MAX_ATTEMPTS",
        "FI_INTEL_EMBEDDING_RETRY_BASE_SECONDS",
        "FI_INTEL_SOURCE_TLS_VERIFY",
        "FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS",
    }
    configured = {
        line.partition("=")[0] for line in template.splitlines() if line.startswith("FI_INTEL_")
    }

    assert required <= configured
    assert "deploy/app.env" in Path(".gitignore").read_text(encoding="utf-8")


def test_coverage_source_filter_is_normalized() -> None:
    settings = Settings(
        coverage_required_source_ids=" sa_sama_news,om_cbo_news,sa_sama_news "
    )

    assert settings.configured_coverage_source_ids == {
        "om_cbo_news",
        "sa_sama_news",
    }


def test_preflight_prints_selected_smoke_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS", "sa_sama_news")

    result = CliRunner().invoke(app, ["preflight"])

    assert result.exit_code == 0
    assert "sources: sa_sama_news" in result.stdout


def test_preflight_rejects_partial_basic_auth_and_insecure_non_shadow_transport() -> None:
    settings = complete_settings().model_copy(
        update={
            "analysis_mode": "pilot",
            "embedding_basic_auth_username": "ollama",
            "embedding_tls_verify": False,
            "llm_tls_verify": False,
            "source_tls_verify": False,
        }
    )

    errors = canonical_configuration_errors(settings)

    assert any("FI_INTEL_EMBEDDING_BASIC_AUTH_PASSWORD" in error for error in errors)
    assert "FI_INTEL_LLM_TLS_VERIFY must be true outside shadow mode" in errors
    assert "FI_INTEL_EMBEDDING_TLS_VERIFY must be true outside shadow mode" in errors
    assert "FI_INTEL_SOURCE_TLS_VERIFY must be true outside shadow mode" in errors
