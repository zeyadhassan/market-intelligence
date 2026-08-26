"""CLI output must not disclose deployment credentials."""

from typer.testing import CliRunner

from fi_intel.cli import _postgres_target, app


def test_postgres_target_redacts_credentials_and_connection_options() -> None:
    dsn = (
        "postgresql://analyst:top-secret@db.internal:6432/intelligence"
        "?sslmode=require&application_name=fi-intel"
    )

    target = _postgres_target(dsn)

    assert target == "db.internal:6432/intelligence"
    assert "analyst" not in target
    assert "top-secret" not in target
    assert "sslmode" not in target


def test_version_command_does_not_print_the_postgres_dsn(monkeypatch) -> None:
    dsn = "postgresql://analyst:top-secret@db.internal:6432/intelligence"
    monkeypatch.setenv("FI_INTEL_POSTGRES_DSN", dsn)

    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert "postgres: db.internal:6432/intelligence" in result.stdout
    assert dsn not in result.stdout
    assert "analyst" not in result.stdout
    assert "top-secret" not in result.stdout
