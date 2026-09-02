"""Create the local virtual environment using the repository-owned proxy settings."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
APP_ENV_FILE = REPOSITORY_ROOT / "deploy" / "app.env"
APP_ENV_TEMPLATE = REPOSITORY_ROOT / "deploy" / "app.env.example"
VENV_PATH = REPOSITORY_ROOT / ".venv"
VENV_PYTHON = VENV_PATH / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
SOURCE_PROXY_VARIABLES = {
    "HTTP_PROXY": "FI_INTEL_SOURCE_HTTP_PROXY",
    "HTTPS_PROXY": "FI_INTEL_SOURCE_HTTPS_PROXY",
    "NO_PROXY": "FI_INTEL_SOURCE_NO_PROXY",
}


def _parse_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _bootstrap_environment(
    app_env_path: Path = APP_ENV_FILE,
    template_path: Path = APP_ENV_TEMPLATE,
    *,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = (os.environ if base is None else base).copy()
    values = _parse_environment(template_path)
    if app_env_path.is_file():
        values.update(_parse_environment(app_env_path))
    for standard_name, application_name in SOURCE_PROXY_VARIABLES.items():
        configured = values.get(application_name)
        if configured:
            environment[standard_name] = configured
    return environment


def main() -> int:
    print("Creating the local Python environment...")  # noqa: T201
    subprocess.run(  # noqa: S603
        (sys.executable, "-m", "venv", str(VENV_PATH)),
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    subprocess.run(  # noqa: S603
        (str(VENV_PYTHON), "-m", "pip", "install", "-e", ".[dev]"),
        cwd=REPOSITORY_ROOT,
        env=_bootstrap_environment(),
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
