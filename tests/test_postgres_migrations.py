"""Migration plan unit tests and optional live-PostgreSQL smoke test."""

import os
from pathlib import Path

import asyncpg
import pytest

from fi_intel.db.migrations import (
    MigrationPlanError,
    PostgresMigrationRunner,
    discover_migrations,
)

PG_DSN = os.environ.get("FI_INTEL_TEST_PG_DSN")


def test_repository_migration_plan_is_contiguous_and_checksummed() -> None:
    plan = discover_migrations()
    assert [item.version for item in plan] == list(range(1, len(plan) + 1))
    assert plan[0].filename == "init.sql"
    assert plan[-1].filename == "0023_nomic_embedding_dimension.sql"
    assert all(len(item.checksum) == 64 for item in plan)
    assert any(item.filename == "0004_replayable_ingestion.sql" for item in plan)
    nomic_migration = Path("deploy/migrations/0023_nomic_embedding_dimension.sql").read_text(
        encoding="utf-8"
    )
    assert "TRUNCATE TABLE document_chunk CASCADE" in nomic_migration
    assert "TYPE vector(768)" in nomic_migration


def test_discovery_rejects_duplicate_versions(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy"
    migrations = deploy / "migrations"
    migrations.mkdir(parents=True)
    (deploy / "init.sql").write_text("SELECT 1;", encoding="utf-8")
    (migrations / "0002_one.sql").write_text("SELECT 2;", encoding="utf-8")
    (migrations / "0002_two.sql").write_text("SELECT 3;", encoding="utf-8")
    with pytest.raises(MigrationPlanError, match="duplicate migration version"):
        discover_migrations(deploy)


def test_discovery_rejects_embedded_transaction_control(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy"
    migrations = deploy / "migrations"
    migrations.mkdir(parents=True)
    (deploy / "init.sql").write_text("SELECT 1;", encoding="utf-8")
    (migrations / "0002_bad.sql").write_text("BEGIN;\nSELECT 2;\nCOMMIT;", encoding="utf-8")
    with pytest.raises(MigrationPlanError, match="runner owns transactions"):
        discover_migrations(deploy)


@pytest.mark.skipif(PG_DSN is None, reason="FI_INTEL_TEST_PG_DSN not set")
async def test_live_postgres_migrations_are_idempotent() -> None:
    assert PG_DSN is not None
    runner = PostgresMigrationRunner(PG_DSN)
    await runner.apply()
    assert await runner.apply() == []
    _, pending = await runner.status()
    assert pending == []

    conn = await asyncpg.connect(PG_DSN)
    try:
        tables = await conn.fetchval(
            """
            SELECT COUNT(*) FROM pg_class
            WHERE relname IN (
                'raw_asset', 'document_version', 'transactional_outbox',
                'ingest_run_v2', 'ingest_job_v2', 'ingest_watermark_v2'
            ) AND relkind = 'r'
            """
        )
        assert tables == 6
    finally:
        await conn.close()
