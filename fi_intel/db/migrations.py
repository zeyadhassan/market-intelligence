"""Ordered, checksummed PostgreSQL migration runner."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import asyncpg

_MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{4})_(?P<name>[a-z0-9_]+)\.sql$")
_TRANSACTION_CONTROL = re.compile(
    r"^\s*(BEGIN|START\s+TRANSACTION|COMMIT|ROLLBACK)\s*;",
    flags=re.IGNORECASE | re.MULTILINE,
)
_MIGRATION_LOCK_ID = 7_066_948_165_425_703_643


class MigrationPlanError(RuntimeError):
    """Migration files are missing, duplicated, or unsafe to wrap."""


class MigrationDriftError(RuntimeError):
    """An applied migration no longer matches its recorded checksum."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    version_key: str
    filename: str
    description: str
    path: Path
    checksum: str
    sql: str


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version_key: str
    filename: str
    checksum: str
    description: str


def default_deploy_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "deploy"


def discover_migrations(deploy_directory: Path | None = None) -> tuple[Migration, ...]:
    """Discover baseline plus numbered migrations and reject ambiguous order."""
    deploy = (deploy_directory or default_deploy_directory()).resolve()
    baseline = deploy / "init.sql"
    migration_directory = deploy / "migrations"
    if not baseline.is_file():
        raise MigrationPlanError(f"baseline migration is missing: {baseline}")
    if not migration_directory.is_dir():
        raise MigrationPlanError(
            f"migration directory is missing: {migration_directory}"
        )

    paths: list[tuple[int, Path, str]] = [(1, baseline, "baseline schema")]
    seen = {1: baseline.name}
    for path in sorted(migration_directory.glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationPlanError(f"invalid migration filename: {path.name}")
        version = int(match.group("version"))
        if version in seen:
            raise MigrationPlanError(
                f"duplicate migration version {version:04d}: "
                f"{seen[version]} and {path.name}"
            )
        seen[version] = path.name
        paths.append((version, path, match.group("name").replace("_", " ")))

    paths.sort(key=lambda item: item[0])
    actual_versions = [version for version, _, _ in paths]
    expected_versions = list(range(1, actual_versions[-1] + 1))
    if actual_versions != expected_versions:
        raise MigrationPlanError(
            f"migration versions are not contiguous: {actual_versions}"
        )

    migrations: list[Migration] = []
    for version, path, description in paths:
        raw = path.read_bytes()
        sql = raw.decode("utf-8")
        if _TRANSACTION_CONTROL.search(sql):
            raise MigrationPlanError(
                f"{path.name} contains transaction control; the runner owns transactions"
            )
        migrations.append(
            Migration(
                version=version,
                version_key=f"{version:04d}",
                filename=path.name,
                description=description,
                path=path,
                checksum=hashlib.sha256(raw).hexdigest(),
                sql=sql,
            )
        )
    return tuple(migrations)


class PostgresMigrationRunner:
    """Apply each migration once under a process-independent advisory lock."""

    def __init__(self, dsn: str, deploy_directory: Path | None = None) -> None:
        self._dsn = dsn
        self._deploy_directory = deploy_directory

    def plan(self) -> tuple[Migration, ...]:
        return discover_migrations(self._deploy_directory)

    async def status(self) -> tuple[list[AppliedMigration], list[Migration]]:
        plan = self.plan()
        conn = await asyncpg.connect(self._dsn)
        try:
            await self._lock(conn)
            await self._bootstrap_ledger(conn)
            applied = await self._load_applied(conn, plan)
            await self._harden_ledger(conn)
            self._assert_no_drift(plan, applied)
            applied_keys = {item.version_key for item in applied}
            pending = [item for item in plan if item.version_key not in applied_keys]
            return applied, pending
        finally:
            await self._unlock(conn)
            await conn.close()

    async def apply(self) -> list[AppliedMigration]:
        plan = self.plan()
        conn = await asyncpg.connect(self._dsn)
        applied_now: list[AppliedMigration] = []
        try:
            await self._lock(conn)
            await self._bootstrap_ledger(conn)
            applied = await self._load_applied(conn, plan)
            await self._harden_ledger(conn)
            self._assert_no_drift(plan, applied)
            applied_keys = {item.version_key for item in applied}
            for migration in plan:
                if migration.version_key in applied_keys:
                    continue
                async with conn.transaction():
                    await conn.execute(migration.sql)
                    await conn.execute(
                        """
                        INSERT INTO schema_migration (
                            version, filename, checksum, description
                        ) VALUES ($1, $2, $3, $4)
                        """,
                        migration.version_key,
                        migration.filename,
                        migration.checksum,
                        migration.description,
                    )
                applied_now.append(
                    AppliedMigration(
                        version_key=migration.version_key,
                        filename=migration.filename,
                        checksum=migration.checksum,
                        description=migration.description,
                    )
                )
            return applied_now
        finally:
            await self._unlock(conn)
            await conn.close()

    @staticmethod
    async def _lock(conn: asyncpg.Connection) -> None:
        await conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_ID)

    @staticmethod
    async def _unlock(conn: asyncpg.Connection) -> None:
        if not conn.is_closed():
            await conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_ID)

    @staticmethod
    async def _bootstrap_ledger(conn: asyncpg.Connection) -> None:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migration (
                version TEXT PRIMARY KEY,
                filename TEXT,
                checksum CHAR(64),
                description TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            ALTER TABLE schema_migration ADD COLUMN IF NOT EXISTS filename TEXT;
            ALTER TABLE schema_migration ADD COLUMN IF NOT EXISTS checksum CHAR(64)
            """
        )

    @staticmethod
    async def _load_applied(
        conn: asyncpg.Connection, plan: tuple[Migration, ...]
    ) -> list[AppliedMigration]:
        rows = await conn.fetch(
            """
            SELECT version, filename, checksum, description
            FROM schema_migration ORDER BY version
            """
        )
        by_version = {item.version_key: item for item in plan}
        applied: list[AppliedMigration] = []
        for row in rows:
            version_key = row["version"]
            expected = by_version.get(version_key)
            if expected is None:
                raise MigrationDriftError(
                    f"database contains unknown migration version {version_key}"
                )
            filename = row["filename"]
            checksum = row["checksum"]
            if filename is None or checksum is None:
                await conn.execute(
                    """
                    UPDATE schema_migration SET filename = $2, checksum = $3
                    WHERE version = $1 AND (filename IS NULL OR checksum IS NULL)
                    """,
                    version_key,
                    expected.filename,
                    expected.checksum,
                )
                filename = expected.filename
                checksum = expected.checksum
            applied.append(
                AppliedMigration(
                    version_key=version_key,
                    filename=filename,
                    checksum=checksum,
                    description=row["description"],
                )
            )
        return applied

    @staticmethod
    async def _harden_ledger(conn: asyncpg.Connection) -> None:
        await conn.execute(
            """
            ALTER TABLE schema_migration ALTER COLUMN filename SET NOT NULL;
            ALTER TABLE schema_migration ALTER COLUMN checksum SET NOT NULL;
            DO $$
            BEGIN
                ALTER TABLE schema_migration
                    ADD CONSTRAINT schema_migration_checksum_format
                    CHECK (checksum ~ '^[0-9a-f]{64}$');
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
            """
        )

    @staticmethod
    def _assert_no_drift(
        plan: tuple[Migration, ...], applied: list[AppliedMigration]
    ) -> None:
        expected = {item.version_key: item for item in plan}
        for record in applied:
            migration = expected[record.version_key]
            if record.filename != migration.filename:
                raise MigrationDriftError(
                    f"migration {record.version_key} filename drift: "
                    f"{record.filename} != {migration.filename}"
                )
            if record.checksum != migration.checksum:
                raise MigrationDriftError(
                    f"migration {record.version_key} checksum drift in {record.filename}"
                )
