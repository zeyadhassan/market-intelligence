"""One-command configuration and startup for the local product."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from fi_intel.application.preflight import canonical_configuration_errors
from fi_intel.config import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
APP_ENV_FILE = REPOSITORY_ROOT / "deploy" / "app.env"
APP_ENV_TEMPLATE = REPOSITORY_ROOT / "deploy" / "app.env.example"
PODMAN_INFRA = REPOSITORY_ROOT / "deploy" / "podman_infra.py"
MODEL_SMOKE = REPOSITORY_ROOT / "deploy" / "model_smoke.py"
MAILPIT_URL = "http://127.0.0.1:8025/"


def _parse_environment(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator != "=" or not key.startswith("FI_INTEL_"):
            raise RuntimeError(
                f"Invalid application environment line {line_number}: expected FI_INTEL_NAME=value"
            )
        if key in parsed:
            raise RuntimeError(f"Duplicate application setting on line {line_number}: {key}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        parsed[key] = value
    return parsed


def _render_environment(template: str, values: dict[str, str]) -> str:
    """Render only current template keys, dropping obsolete setup fields."""

    rendered: list[str] = []
    for raw_line in template.splitlines():
        stripped = raw_line.strip()
        key, separator, _value = stripped.partition("=")
        if separator == "=" and key.startswith("FI_INTEL_") and key in values:
            value = values[key]
            if "\n" in value or "\r" in value:
                raise RuntimeError(f"{key} cannot contain a newline")
            rendered.append(f"{key}={value}")
        else:
            rendered.append(raw_line)
    return "\n".join(rendered) + "\n"


def _settings(values: dict[str, str]) -> Settings:
    field_values = {
        field_name: values[f"FI_INTEL_{field_name.upper()}"]
        for field_name in Settings.model_fields
        if f"FI_INTEL_{field_name.upper()}" in values
    }
    return Settings.model_validate(field_values)


def _product_url(settings: Settings) -> str:
    return f"http://127.0.0.1:{settings.api_host_port}/"


def configure_environment(
    env_path: Path = APP_ENV_FILE,
    template_path: Path = APP_ENV_TEMPLATE,
) -> Settings:
    """Create or upgrade the tiny private env file without prompting."""

    template = template_path.read_text(encoding="utf-8")
    template_values = _parse_environment(template)
    if env_path.is_file():
        existing_values = _parse_environment(env_path.read_text(encoding="utf-8"))
        values = {
            key: existing_values.get(key, default) for key, default in template_values.items()
        }
        env_path.write_text(_render_environment(template, values), encoding="utf-8")
    else:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template_path, env_path)
        values = template_values

    try:
        settings = _settings(values)
    except ValidationError as exc:
        raise RuntimeError(f"deploy/app.env has an invalid model setting: {exc}") from exc
    errors = canonical_configuration_errors(settings)
    if errors:
        details = "\n  - ".join(errors)
        raise RuntimeError(
            "deploy/app.env needs a configuration correction:\n"
            f"  - {details}\n"
            "Check the chat, embedding, and source-network sections."
        )
    print(f"Configuration ready: {env_path}")
    return settings


def _run(*arguments: str) -> None:
    subprocess.run(arguments, cwd=REPOSITORY_ROOT, check=True)  # noqa: S603


def _podman_binary() -> str:
    module_name = "deploy.podman_infra" if __package__ else "podman_infra"
    module = importlib.import_module(module_name)
    locate_podman = cast(Callable[[], str], module.__dict__["_podman"])
    return locate_podman()


def _ensure_podman_engine() -> None:
    podman = _podman_binary()
    available = subprocess.run(  # noqa: S603
        (podman, "info"), cwd=REPOSITORY_ROOT, capture_output=True, check=False
    )
    if available.returncode == 0:
        return
    if os.name != "nt" and sys.platform != "darwin":
        raise RuntimeError("Podman is installed but its engine is unavailable")
    print("Starting the Podman machine ...")
    machines = subprocess.run(  # noqa: S603
        (podman, "machine", "list", "--format", "json"),
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    try:
        machine_list = json.loads(machines.stdout) if machines.returncode == 0 else []
    except json.JSONDecodeError:
        machine_list = []
    if not machine_list:
        _run(podman, "machine", "init")
    _run(podman, "machine", "start")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--configure-only",
        action="store_true",
        help="Create and validate deploy/app.env without starting services.",
    )
    parser.add_argument(
        "--skip-model-smoke",
        action="store_true",
        help="Skip live model connectivity checks.",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open the product URL.")
    arguments = parser.parse_args(argv)
    try:
        settings = configure_environment()
        if arguments.configure_only:
            return 0
        if not arguments.skip_model_smoke:
            print("\nChecking the model gateways ...")
            _run(sys.executable, str(MODEL_SMOKE))
        _ensure_podman_engine()
        print("\nStarting the product ...")
        _run(sys.executable, str(PODMAN_INFRA), "app-up")
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"product startup error: {exc}\n")

    product_url = _product_url(settings)
    print("\nProduct is ready:")
    print(f"  Product:           {product_url}")
    print(f"  Development email: {MAILPIT_URL}")
    print(f"  Live diagnostics:  {sys.executable} deploy/podman_infra.py logs")
    if not arguments.no_browser:
        webbrowser.open(product_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
