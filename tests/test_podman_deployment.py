"""Static safeguards for the Podman-owned local infrastructure path."""

from pathlib import Path

import pytest

from deploy import podman_infra


def test_podman_compose_uses_migration_only_bootstrap_and_qualified_images() -> None:
    compose = Path("deploy/compose.yml").read_text(encoding="utf-8")

    assert "docker-entrypoint-initdb.d" not in compose
    assert "docker.io/pgvector/pgvector:pg16" in compose
    assert "docker.io/library/neo4j:5.26-community" in compose
    assert "docker.io/axllent/mailpit:v1.27" in compose
    assert "cypher-shell" in compose
    assert "../.fi-intel/archive:/app/.fi-intel/archive:Z" in compose
    assert "FI_INTEL_OIDC_ISSUER" not in compose
    assert "FI_INTEL_MODEL_EVALUATION_DATASET_DIGEST" not in compose
    assert "FI_INTEL_COVERED_ENTITY_LEIS" not in compose
    assert '"127.0.0.1:8000:8000"' in compose
    assert 'profiles: ["app"]' in compose
    for service in (
        "source-worker:",
        "projection-worker:",
        "analysis-worker:",
        "search-worker:",
        "delivery-worker:",
        "scheduler:",
    ):
        assert service in compose
    source_block = compose.split("source-worker:", 1)[1].split("projection-worker:", 1)[0]
    delivery_block = compose.split("delivery-worker:", 1)[1].split("volumes:", 1)[0]
    assert "neo4j:" not in source_block
    assert "neo4j:" not in delivery_block
    for graph_worker, next_service in (
        ("projection-worker:", "analysis-worker:"),
        ("analysis-worker:", "search-worker:"),
        ("search-worker:", "delivery-worker:"),
    ):
        block = compose.split(graph_worker, 1)[1].split(next_service, 1)[0]
        assert "neo4j:" in block

    containerfile = Path("deploy/Containerfile").read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["fi-intel"]' in containerfile


def test_podman_launcher_enforces_infrastructure_suite() -> None:
    launcher = Path("deploy/podman_infra.py").read_text(encoding="utf-8")

    assert '"FI_INTEL_REQUIRE_INFRA": "true"' in launcher
    assert '"db", "migrate"' in launcher
    assert '"fi_intel.cli", "migrate"' not in launcher
    assert '"app-up"' in launcher
    assert 'environment["PODMAN_COMPOSE_PROVIDER"]' in launcher
    assert "Docker Compose is intentionally not used" in launcher
    assert "label=io.podman.compose.service=" in launcher
    assert '"preflight"' in launcher
    assert '"sync-access",' not in launcher
    assert '"sync-models",' not in launcher
    assert '"--env-file", str(APP_ENV_FILE)' in launcher

    one_command_launcher = Path("run.cmd").read_text(encoding="utf-8")
    assert "deploy\\product.py" in one_command_launcher
    assert "pip install -e" in one_command_launcher


def test_podman_launcher_accepts_explicit_binary_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "podman.exe"
    executable.touch()
    monkeypatch.setenv("FI_INTEL_PODMAN_BIN", str(executable))

    assert podman_infra._podman() == str(executable)


def test_podman_launcher_rejects_missing_compose_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FI_INTEL_PODMAN_COMPOSE_PROVIDER", raising=False)
    monkeypatch.setattr(podman_infra.shutil, "which", lambda _name: None)
    monkeypatch.setattr(podman_infra.sys, "executable", str(tmp_path / "python.exe"))

    with pytest.raises(RuntimeError, match="Docker Compose is intentionally not used"):
        podman_infra._podman_compose_provider()


def test_app_environment_parser_is_file_owned_and_rejects_shell_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "app.env"
    config.write_text("FI_INTEL_ANALYSIS_MODE=shadow\nFI_INTEL_LLM_API_KEY=file-key\n")
    monkeypatch.setattr(podman_infra, "APP_ENV_FILE", config)
    monkeypatch.setenv("FI_INTEL_LLM_API_KEY", "process-key")

    environment = podman_infra._load_app_environment(required=True)

    assert environment["FI_INTEL_LLM_API_KEY"] == "file-key"
    config.write_text("export FI_INTEL_ANALYSIS_MODE=shadow\n")
    with pytest.raises(RuntimeError, match="expected FI_INTEL_NAME=value"):
        podman_infra._load_app_environment(required=True)
