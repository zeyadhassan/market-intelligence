"""Suite-level gates for explicit complete local infrastructure verification."""

from __future__ import annotations

import os

import pytest


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Turn only missing-infrastructure skips into complete-suite failures."""
    if os.getenv("FI_INTEL_REQUIRE_INFRA", "").lower() != "true":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = reporter.stats.get("skipped", []) if reporter is not None else []
    missing_infra = [report for report in skipped if "FI_INTEL_TEST_" in str(report.longrepr)]
    if missing_infra:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
