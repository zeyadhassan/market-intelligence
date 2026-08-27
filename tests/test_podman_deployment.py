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
    assert "FI_INTEL_OIDC_ISSUER" in compose
    assert "FI_INTEL_OIDC_AUDIENCE" in compose
    assert "FI_INTEL_OIDC_JWKS_URL" in compose
    assert "FI_INTEL_ACCESS_SUBJECT" in compose
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


def test_release_gate_uses_the_same_podman_launcher() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "services:" not in workflow
    assert "python deploy/podman_infra.py up" in workflow
    assert "python deploy/podman_infra.py test" in workflow
    assert "podman-compose" in workflow
