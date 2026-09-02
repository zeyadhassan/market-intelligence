"""Static safeguards for the Podman-owned local infrastructure path."""

import socket
from pathlib import Path

import pytest

from deploy import podman_infra


def test_podman_compose_uses_migration_only_bootstrap_and_qualified_images() -> None:
    compose = Path("deploy/compose.yml").read_text(encoding="utf-8")

    assert "build:" not in compose
    assert "image: localhost/fi-intel:dev" in compose
    assert "docker-entrypoint-initdb.d" not in compose
    assert "docker.io/pgvector/pgvector:pg16" in compose
    assert "docker.io/library/neo4j:5.26-community" in compose
    assert "docker.io/axllent/mailpit:v1.27" in compose
    assert "cypher-shell" in compose
    assert "../.fi-intel/archive:/app/.fi-intel/archive:Z" in compose
    assert "FI_INTEL_OIDC_ISSUER" not in compose
    assert "FI_INTEL_MODEL_EVALUATION_DATASET_DIGEST" not in compose
    assert "FI_INTEL_COVERED_ENTITY_LEIS" not in compose
    assert '"127.0.0.1:${FI_INTEL_API_HOST_PORT:-8000}:8000"' in compose
    assert 'profiles: ["app"]' in compose
    assert (
        "FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS: "
        "${FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS:-}" in compose
    )
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
    assert "<<: *app-environment" in source_block
    assert "HTTP_PROXY: ${FI_INTEL_SOURCE_HTTP_PROXY:-}" in source_block
    assert "HTTPS_PROXY: ${FI_INTEL_SOURCE_HTTPS_PROXY:-}" in source_block
    assert "NO_PROXY: ${FI_INTEL_SOURCE_NO_PROXY:-}" in source_block
    assert (
        "FI_INTEL_SOURCE_HTTP_PROXY: ${FI_INTEL_SOURCE_HTTP_PROXY:-}" in source_block
    )
    assert (
        "FI_INTEL_SOURCE_HTTPS_PROXY: ${FI_INTEL_SOURCE_HTTPS_PROXY:-}" in source_block
    )
    assert "FI_INTEL_SOURCE_NO_PROXY: ${FI_INTEL_SOURCE_NO_PROXY:-}" in source_block
    assert (
        "FI_INTEL_SOURCE_TLS_VERIFY: ${FI_INTEL_SOURCE_TLS_VERIFY:-true}"
        in source_block
    )
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
    assert '"logs"' in launcher
    assert '"source-check"' in launcher
    assert 'environment["PODMAN_COMPOSE_PROVIDER"]' in launcher
    assert "Docker Compose is intentionally not used" in launcher
    assert "label=io.podman.compose.service=" in launcher
    assert '"preflight"' in launcher
    assert '"sync-access",' not in launcher
    assert '"sync-models",' not in launcher
    assert '"--env-file", str(APP_ENV_FILE)' in launcher
    assert '"build"' in launcher
    assert "str(CONTAINERFILE)" in launcher
    assert '"--build"' not in launcher

    one_command_launcher = Path("run.cmd").read_text(encoding="utf-8")
    assert "deploy\\product.py" in one_command_launcher
    assert "deploy\\bootstrap.py" in one_command_launcher

    bootstrap = Path("deploy/bootstrap.py").read_text(encoding="utf-8")
    assert '"pip", "install", "-e", ".[dev]"' in bootstrap
    assert "FI_INTEL_SOURCE_HTTP_PROXY" in bootstrap


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


def test_podman_environment_owns_source_proxy_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(podman_infra, "_podman", lambda: "podman")
    monkeypatch.setattr(podman_infra, "_podman_compose_provider", lambda: "podman-compose")

    environment = podman_infra._podman_environment(
        {
            "PATH": "existing-path",
            "FI_INTEL_SOURCE_HTTP_PROXY": "http://proxy.example:3128",
            "FI_INTEL_SOURCE_HTTPS_PROXY": "http://proxy.example:3128",
            "FI_INTEL_SOURCE_NO_PROXY": "localhost,internal.example",
        }
    )

    assert environment["HTTP_PROXY"] == "http://proxy.example:3128"
    assert environment["HTTPS_PROXY"] == "http://proxy.example:3128"
    assert environment["NO_PROXY"] == "localhost,internal.example"
    assert environment["PODMAN_COMPOSE_PROVIDER"] == "podman-compose"


def test_windows_podman_proxy_uses_host_resolved_ip_without_losing_auth() -> None:
    environment = {
        "FI_INTEL_SOURCE_HTTP_PROXY": "http://proxy-user:p%40ss@proxy.example:3128",
        "FI_INTEL_SOURCE_HTTPS_PROXY": "http://proxy.example:3128",
        "FI_INTEL_SOURCE_NO_PROXY": "localhost,internal.example",
    }

    prepared = podman_infra._container_source_proxy_environment(
        environment,
        windows=True,
        resolver=lambda hostname: (
            "10.20.30.40" if hostname == "proxy.example" else "unexpected"
        ),
    )

    assert prepared["FI_INTEL_SOURCE_HTTP_PROXY"] == (
        "http://proxy-user:p%40ss@10.20.30.40:3128"
    )
    assert prepared["FI_INTEL_SOURCE_HTTPS_PROXY"] == "http://10.20.30.40:3128"
    assert prepared["FI_INTEL_SOURCE_NO_PROXY"] == "localhost,internal.example"
    assert environment["FI_INTEL_SOURCE_HTTP_PROXY"].endswith("proxy.example:3128")


def test_windows_podman_proxy_resolution_failure_has_operator_guidance() -> None:
    def fail_resolution(_hostname: str) -> str:
        raise socket.gaierror("not known")

    with pytest.raises(RuntimeError, match="corporate VPN/DNS"):
        podman_infra._container_source_proxy_environment(
            {"FI_INTEL_SOURCE_HTTPS_PROXY": "http://missing.example:3128"},
            windows=True,
            resolver=fail_resolution,
        )


def test_app_image_build_uses_deploy_containerfile_and_configured_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, str] | None]] = []
    monkeypatch.setattr(podman_infra, "_podman", lambda: "podman")
    monkeypatch.setattr(podman_infra, "_podman_compose_provider", lambda: "podman-compose")
    monkeypatch.setattr(
        podman_infra,
        "_run",
        lambda *arguments, **options: calls.append((arguments, options.get("env"))),
    )

    podman_infra._build_app_image(
        {
            "PATH": "existing-path",
            "FI_INTEL_SOURCE_HTTP_PROXY": "http://proxy.example:3128",
            "FI_INTEL_SOURCE_HTTPS_PROXY": "http://proxy.example:3128",
            "FI_INTEL_SOURCE_NO_PROXY": "localhost,internal.example",
        }
    )

    arguments, environment = calls[0]
    assert arguments[:2] == ("podman", "build")
    assert arguments[arguments.index("--file") + 1] == str(podman_infra.CONTAINERFILE)
    assert arguments[arguments.index("--tag") + 1] == podman_infra.APP_IMAGE
    assert "HTTP_PROXY=http://proxy.example:3128" in arguments
    assert arguments[-1] == str(podman_infra.REPOSITORY_ROOT)
    assert environment is not None
    assert environment["HTTP_PROXY"] == "http://proxy.example:3128"


def test_app_up_builds_explicitly_and_passes_proxy_to_infrastructure_pulls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {"FI_INTEL_SOURCE_HTTP_PROXY": "http://proxy.example:3128"}
    compose_calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    builds: list[dict[str, str]] = []
    monkeypatch.setattr(podman_infra.sys, "argv", ["podman_infra.py", "app-up"])
    monkeypatch.setattr(podman_infra, "_load_app_environment", lambda *, required: environment)
    monkeypatch.setattr(podman_infra, "_assert_engine", lambda: None)
    monkeypatch.setattr(podman_infra, "_run", lambda *arguments, **options: "")
    monkeypatch.setattr(podman_infra, "_wait_healthy", lambda: None)
    monkeypatch.setattr(podman_infra, "_migrate", lambda env=None: None)
    monkeypatch.setattr(podman_infra, "_wait_for_application_identity", lambda env: None)
    monkeypatch.setattr(
        podman_infra,
        "_container_source_proxy_environment",
        lambda value: value,
    )
    monkeypatch.setattr(
        podman_infra,
        "_compose",
        lambda *arguments, **options: compose_calls.append((arguments, options)),
    )
    monkeypatch.setattr(podman_infra, "_build_app_image", builds.append)

    assert podman_infra.main() == 0

    assert builds == [environment]
    assert compose_calls[0] == (("up", "--detach"), {"env": environment})
    assert compose_calls[1][0] == (
        "--profile",
        "app",
        "up",
        "--detach",
        "--force-recreate",
    )
    assert compose_calls[1][1]["env"] is environment
    assert "--build" not in compose_calls[1][0]


def test_application_identity_check_rejects_another_service_on_the_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WrongResponse:
        def __enter__(self) -> "WrongResponse":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"principal_id":"some-other-app"}'

    monkeypatch.setattr(podman_infra, "urlopen", lambda *_args, **_kwargs: WrongResponse())

    with pytest.raises(RuntimeError, match="different application identity"):
        podman_infra._wait_for_application_identity(
            {"FI_INTEL_API_HOST_PORT": "8123"}, timeout_seconds=0.1
        )
