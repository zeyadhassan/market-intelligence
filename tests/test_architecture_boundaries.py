"""Static dependency and runtime-owner guards for the one canonical path."""

import ast
from pathlib import Path

import pytest

from fi_intel.runtime import (
    AnalysisMode,
    ExecutionPath,
    RuntimeCapabilities,
    RuntimePolicyError,
    validate_runtime_mode,
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_application_layer_never_imports_the_http_api() -> None:
    violations = {
        str(path): sorted(name for name in _imports(path) if name.startswith("fi_intel.api"))
        for path in Path("fi_intel/application").rglob("*.py")
    }

    assert not {path: names for path, names in violations.items() if names}


def test_canonical_processes_do_not_import_fixture_or_removed_live_paths() -> None:
    canonical = (
        "daily_worker.py",
        "delivery.py",
        "scheduler.py",
        "search.py",
        "workers.py",
    )
    forbidden = ("fi_intel.demo", "fi_intel.sources.fixture")
    for filename in canonical:
        names = _imports(Path("fi_intel/application") / filename)
        assert not any(name.startswith(forbidden) for name in names)

    assert not Path("fi_intel/demo/gcc_live.py").exists()
    assert not Path("fi_intel/demo/stage_one_live_app.py").exists()
    assert not Path("fi_intel/demo/stage_one_canonical_app.py").exists()
    assert not Path("fi_intel/api/config.py").exists()


def test_only_graph_client_touches_the_private_driver() -> None:
    offenders = []
    for path in Path("fi_intel").rglob("*.py"):
        if path == Path("fi_intel/graph/client.py"):
            continue
        if "._driver" in path.read_text(encoding="utf-8"):
            offenders.append(str(path))
    assert offenders == []


def test_nonfixture_runtime_rejects_authoritative_graph_writes() -> None:
    capabilities = RuntimeCapabilities(
        execution_path=ExecutionPath.UNIFIED_PIPELINE,
        all_models_registry_routed=True,
        coverage_computed_server_side=True,
        durable_step_store=True,
        authoritative_neo4j_writes=True,
    )

    with pytest.raises(RuntimePolicyError, match="forbids direct authoritative Neo4j writes"):
        validate_runtime_mode(AnalysisMode.SHADOW, capabilities)


def test_cli_exposes_independent_process_and_recovery_entrypoints() -> None:
    source = Path("fi_intel/cli.py").read_text(encoding="utf-8")
    for decorator in (
        '@worker_app.command("source")',
        '@worker_app.command("projection")',
        '@worker_app.command("analysis")',
        '@worker_app.command("search")',
        '@worker_app.command("delivery")',
        '@scheduler_app.command("run")',
        '@operator_app.command("dead-letters")',
        '@operator_app.command("sync-access")',
        '@operator_app.command("replay-outbox")',
        '@operator_app.command("replay-document")',
        '@operator_app.command("rebuild-graph")',
    ):
        assert decorator in source
    for removed in (
        'app.add_typer(demo_app, name="demo")',
        '@app.command("search")',
        'app.add_typer(ingest_app, name="ingest")',
        'app.add_typer(index_app, name="index")',
        'app.add_typer(patterns_app, name="patterns")',
    ):
        assert removed not in source
    assert "fi_intel.api.app:create_production_app" in source
