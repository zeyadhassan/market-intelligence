"""Start and verify the complete local stack through Podman Compose."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPOSITORY_ROOT / "deploy" / "compose.yml"
COMPOSE_PROJECT = COMPOSE_FILE.parent.name
SERVICES = ("postgres", "neo4j")
PODMAN_BINARY_ENV = "FI_INTEL_PODMAN_BIN"
PODMAN_COMPOSE_PROVIDER_ENV = "FI_INTEL_PODMAN_COMPOSE_PROVIDER"


def _configured_executable(environment_variable: str) -> str | None:
    configured = os.environ.get(environment_variable)
    if not configured:
        return None
    executable = Path(configured).expanduser()
    if not executable.is_file():
        raise RuntimeError(f"{environment_variable} does not point to a file: {executable}")
    return str(executable)


def _windows_podman_candidates() -> tuple[Path, ...]:
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    local_app_data = os.environ.get("LOCALAPPDATA")
    candidates = [program_files / "RedHat" / "Podman" / "podman.exe"]
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Podman" / "podman.exe")
    return tuple(candidates)


def _podman_desktop_installed() -> bool:
    local_app_data = os.environ.get("LOCALAPPDATA")
    return bool(
        local_app_data
        and (Path(local_app_data) / "Programs" / "Podman Desktop" / "Podman Desktop.exe").is_file()
    )


def _podman() -> str:
    configured = _configured_executable(PODMAN_BINARY_ENV)
    if configured:
        return configured
    executable = shutil.which("podman")
    if executable:
        return executable
    if os.name == "nt":
        for candidate in _windows_podman_candidates():
            if candidate.is_file():
                return str(candidate)
        if _podman_desktop_installed():
            raise RuntimeError(
                "Podman Desktop is installed, but its Podman engine/CLI is missing. "
                "Install the Podman engine in Podman Desktop or with "
                "'winget install RedHat.Podman'."
            )
    raise RuntimeError(
        f"The Podman engine/CLI is not installed or is not on PATH. "
        f"Set {PODMAN_BINARY_ENV} to its executable if it is installed elsewhere."
    )


def _podman_compose_provider() -> str:
    configured = _configured_executable(PODMAN_COMPOSE_PROVIDER_ENV)
    if configured:
        return configured
    executable = shutil.which("podman-compose")
    if executable:
        return executable
    adjacent = Path(sys.executable).with_name(
        "podman-compose.exe" if os.name == "nt" else "podman-compose"
    )
    if adjacent.is_file():
        return str(adjacent)
    raise RuntimeError(
        "podman-compose is unavailable. Install the project development dependencies or set "
        f"{PODMAN_COMPOSE_PROVIDER_ENV}; Docker Compose is intentionally not used."
    )


def _podman_environment() -> dict[str, str]:
    environment = os.environ.copy()
    podman = _podman()
    environment["PATH"] = str(Path(podman).parent) + os.pathsep + environment.get("PATH", "")
    environment["PODMAN_COMPOSE_PROVIDER"] = _podman_compose_provider()
    return environment


def _run(*arguments: str, capture: bool = False, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(  # noqa: S603
        arguments,
        cwd=REPOSITORY_ROOT,
        check=True,
        text=True,
        capture_output=capture,
        env=env,
    )
    return completed.stdout.strip() if capture else ""


def _compose(*arguments: str, capture: bool = False) -> str:
    return _run(
        _podman(),
        "compose",
        "--file",
        str(COMPOSE_FILE),
        *arguments,
        capture=capture,
        env=_podman_environment(),
    )


def _assert_engine() -> None:
    try:
        _run(_podman(), "info", "--format", "{{.Host.OS}}", capture=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "The Podman engine is unavailable. On Windows/macOS run 'podman machine start'."
        ) from exc

    try:
        _run(
            _podman(),
            "compose",
            "version",
            capture=True,
            env=_podman_environment(),
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Podman Compose is unavailable. Configure a compose provider for the Podman CLI."
        ) from exc


def _wait_healthy(timeout_seconds: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_states: dict[str, str] = {}
    while time.monotonic() < deadline:
        for service in SERVICES:
            container_id = _run(
                _podman(),
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=io.podman.compose.project={COMPOSE_PROJECT}",
                "--filter",
                f"label=io.podman.compose.service={service}",
                capture=True,
            )
            if not container_id:
                last_states[service] = "missing"
                continue
            try:
                last_states[service] = _run(
                    _podman(),
                    "inspect",
                    "--format",
                    "{{.State.Health.Status}}",
                    container_id,
                    capture=True,
                )
            except subprocess.CalledProcessError:
                last_states[service] = "inspect-failed"
        if all(last_states.get(service) == "healthy" for service in SERVICES):
            return
        time.sleep(2)
    states = ", ".join(f"{service}={last_states.get(service, 'unknown')}" for service in SERVICES)
    raise RuntimeError(f"Podman services did not become healthy within the deadline: {states}")


def _migrate() -> None:
    _run(sys.executable, "-m", "fi_intel.cli", "db", "migrate")


def _test() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "FI_INTEL_TEST_PG_DSN": ("postgresql://fi_intel:fi_intel@localhost:5432/fi_intel"),
            "FI_INTEL_TEST_NEO4J_URI": "bolt://localhost:7687",
            "FI_INTEL_REQUIRE_INFRA": "true",
        }
    )
    _run(sys.executable, "-m", "pytest", "-q", env=environment)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        default="up",
        choices=("up", "app-up", "down", "reset", "status", "migrate", "test"),
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="Required as RESET for the destructive database-volume reset.",
    )
    arguments = parser.parse_args()
    action = arguments.action
    try:
        _assert_engine()
        if action == "up":
            _compose("up", "--detach")
            _wait_healthy()
            _migrate()
        elif action == "app-up":
            _compose("up", "--detach")
            _wait_healthy()
            _migrate()
            _compose("--profile", "app", "up", "--detach", "--build")
        elif action == "down":
            _compose("--profile", "app", "down")
        elif action == "reset":
            if arguments.confirm != "RESET":
                raise RuntimeError("reset requires --confirm RESET")
            _compose("--profile", "app", "down", "--volumes")
        elif action == "status":
            _compose("ps")
            _run(sys.executable, "-m", "fi_intel.cli", "db", "status")
        elif action == "migrate":
            _migrate()
        else:
            _test()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"podman infrastructure error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
