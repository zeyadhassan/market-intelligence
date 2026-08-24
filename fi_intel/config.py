"""Runtime configuration.

All configuration flows through Pydantic settings objects. Module-level
globals are forbidden by project coding standards because they make
configuration invisible to tests and impossible to scope per-run.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings, overridable via FI_INTEL_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="FI_INTEL_", frozen=True)

    # Defaults are local-dev only, matching deploy/docker-compose.yml.
    # Real credentials arrive via FI_INTEL_* environment variables.
    postgres_dsn: str = "postgresql://fi_intel:fi_intel@localhost:5432/fi_intel"  # noqa: S105
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "fi_intel"  # noqa: S105
    log_level: str = "INFO"
