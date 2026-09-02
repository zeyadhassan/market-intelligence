"""Start and verify the complete local stack through Podman Compose."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPOSITORY_ROOT / "deploy" / "compose.yml"
APP_ENV_FILE = REPOSITORY_ROOT / "deploy" / "app.env"
CONTAINERFILE = REPOSITORY_ROOT / "deploy" / "Containerfile"
APP_IMAGE = "localhost/fi-intel:dev"
COMPOSE_PROJECT = COMPOSE_FILE.parent.name
SERVICES = ("postgres", "neo4j")
PODMAN_BINARY_ENV = "FI_INTEL_PODMAN_BIN"
PODMAN_COMPOSE_PROVIDER_ENV = "FI_INTEL_PODMAN_COMPOSE_PROVIDER"
SOURCE_PROXY_VARIABLES = {
    "HTTP_PROXY": "FI_INTEL_SOURCE_HTTP_PROXY",
    "HTTPS_PROXY": "FI_INTEL_SOURCE_HTTPS_PROXY",
    "NO_PROXY": "FI_INTEL_SOURCE_NO_PROXY",
}
API_HOST_PORT_ENV = "FI_INTEL_API_HOST_PORT"
LOCAL_API_TOKEN = "fi-intel-local"  # noqa: S105 - built-in local identity, not a secret


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


def _podman_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    environment = (os.environ if base is None else base).copy()
    for standard_name, application_name in SOURCE_PROXY_VARIABLES.items():
        configured = environment.get(application_name)
        if configured:
            environment[standard_name] = configured
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


def _compose(
    *arguments: str,
    capture: bool = False,
    env: dict[str, str] | None = None,
    app_config: bool = False,
) -> str:
    compose_arguments = ["--file", str(COMPOSE_FILE)]
    if app_config:
        compose_arguments.extend(("--env-file", str(APP_ENV_FILE)))
    compose_arguments.extend(arguments)
    return _run(
        _podman(),
        "compose",
        *compose_arguments,
        capture=capture,
        env=_podman_environment(env),
    )


def _build_app_image(environment: dict[str, str]) -> None:
    """Build the application without relying on provider-specific Compose build parsing."""

    arguments = [
        _podman(),
        "build",
        "--file",
        str(CONTAINERFILE),
        "--tag",
        APP_IMAGE,
    ]
    for build_argument, application_name in SOURCE_PROXY_VARIABLES.items():
        configured = environment.get(application_name)
        if configured:
            arguments.extend(("--build-arg", f"{build_argument}={configured}"))
    arguments.append(str(REPOSITORY_ROOT))
    _run(*arguments, env=_podman_environment(environment))


def _load_app_environment(*, required: bool) -> dict[str, str]:
    """Load the one operator-owned env file without accepting shell syntax."""

    if not APP_ENV_FILE.is_file():
        if required:
            raise RuntimeError(
                f"Application configuration is missing: {APP_ENV_FILE}. "
                "Copy deploy/app.env.example to deploy/app.env and fill every required value."
            )
        return os.environ.copy()
    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        APP_ENV_FILE.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator != "=" or not key.startswith("FI_INTEL_"):
            raise RuntimeError(
                f"Invalid deploy/app.env line {line_number}: expected FI_INTEL_NAME=value"
            )
        if key in parsed:
            raise RuntimeError(f"Duplicate deploy/app.env setting on line {line_number}: {key}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        parsed[key] = value
    environment = os.environ.copy()
    environment.update(parsed)
    return environment


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


def _migrate(env: dict[str, str] | None = None) -> None:
    _run(sys.executable, "-m", "fi_intel.cli", "db", "migrate", env=env)


def _api_host_port(environment: dict[str, str]) -> int:
    raw = environment.get(API_HOST_PORT_ENV, "8000")
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{API_HOST_PORT_ENV} must be an integer, got {raw!r}") from exc
    if not 1 <= port <= 65_535:
        raise RuntimeError(f"{API_HOST_PORT_ENV} must be between 1 and 65535")
    return port


def _wait_for_application_identity(
    environment: dict[str, str], *, timeout_seconds: float = 60.0
) -> str:
    """Prove that the published port serves this product, not another local app."""

    port = _api_host_port(environment)
    url = f"http://127.0.0.1:{port}/v1/session"
    deadline = time.monotonic() + timeout_seconds
    last_connection_error = "connection refused"
    while time.monotonic() < deadline:
        request = Request(url, headers={"Authorization": f"Bearer {LOCAL_API_TOKEN}"})
        try:
            with urlopen(request, timeout=3) as response:  # noqa: S310 - fixed loopback URL
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(
                f"Port {port} answered HTTP {exc.code}, but it is not the FI Intel API. "
                f"Set {API_HOST_PORT_ENV} to a free local port."
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError(
                f"Port {port} returned an unexpected response; another application owns it. "
                f"Set {API_HOST_PORT_ENV} to a free local port."
            ) from exc
        except URLError as exc:
            last_connection_error = str(exc.reason)
            time.sleep(1)
            continue
        except TimeoutError as exc:
            last_connection_error = str(exc)
            time.sleep(1)
            continue
        if not isinstance(body, dict) or body.get("principal_id") != "local-analyst":
            raise RuntimeError(
                f"Port {port} is serving a different application identity. "
                f"Set {API_HOST_PORT_ENV} to a free local port."
            )
        return f"http://127.0.0.1:{port}/"
    raise RuntimeError(
        f"FI Intel API did not become reachable on port {port} within the deadline "
        f"(last connection error: {last_connection_error})."
    )


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


def main() -> int:  # noqa: C901 - explicit bounded operator-action dispatch
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        default="up",
        choices=(
            "up",
            "preflight",
            "app-up",
            "down",
            "reset",
            "status",
            "logs",
            "source-check",
            "migrate",
            "test",
        ),
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="Required as RESET for the destructive database-volume reset.",
    )
    parser.add_argument(
        "--no-follow",
        action="store_true",
        help="Print the requested log tail and exit instead of following it.",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=200,
        help="Number of service log lines to print (default: 200).",
    )
    arguments = parser.parse_args()
    action = arguments.action
    try:
        if action == "preflight":
            app_environment = _load_app_environment(required=True)
            _run(sys.executable, "-m", "fi_intel.cli", "preflight", env=app_environment)
            return 0
        _assert_engine()
        if action == "up":
            app_environment = _load_app_environment(required=False)
            _compose("up", "--detach", env=app_environment)
            _wait_healthy()
            _migrate()
        elif action == "app-up":
            app_environment = _load_app_environment(required=True)
            _run(sys.executable, "-m", "fi_intel.cli", "preflight", env=app_environment)
            _compose("up", "--detach", env=app_environment)
            _wait_healthy()
            _migrate(app_environment)
            _build_app_image(app_environment)
            _compose(
                "--profile",
                "app",
                "up",
                "--detach",
                "--force-recreate",
                env=app_environment,
                app_config=True,
            )
            _wait_for_application_identity(app_environment)
        elif action == "down":
            app_environment = _load_app_environment(required=False)
            _compose(
                "--profile",
                "app",
                "down",
                env=app_environment,
                app_config=APP_ENV_FILE.is_file(),
            )
        elif action == "reset":
            if arguments.confirm != "RESET":
                raise RuntimeError("reset requires --confirm RESET")
            _compose("--profile", "app", "down", "--volumes")
        elif action == "status":
            app_environment = _load_app_environment(required=False)
            _compose(
                "--profile",
                "app",
                "ps",
                env=app_environment,
                app_config=APP_ENV_FILE.is_file(),
            )
            _run(sys.executable, "-m", "fi_intel.cli", "db", "status")
        elif action == "logs":
            if arguments.tail < 1:
                raise RuntimeError("--tail must be positive")
            app_environment = _load_app_environment(required=True)
            log_arguments = [
                "--profile",
                "app",
                "logs",
            ]
            if not arguments.no_follow:
                log_arguments.append("--follow")
            log_arguments.extend(("--tail", str(arguments.tail)))
            log_arguments.extend(
                (
                    "source-worker",
                    "projection-worker",
                    "analysis-worker",
                    "api",
                )
            )
            _compose(
                *log_arguments,
                env=app_environment,
                app_config=True,
            )
        elif action == "source-check":
            app_environment = _load_app_environment(required=True)
            _compose(
                "--profile",
                "app",
                "run",
                "--rm",
                "--no-deps",
                "source-worker",
                "worker",
                "source",
                "--once",
                "--force",
                env=app_environment,
                app_config=True,
            )
        elif action == "migrate":
            _migrate()
        else:
            _test()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"podman infrastructure error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
