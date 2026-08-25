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

    # Open-web RSS/Atom sources (fi_intel/sources/adapters/rss.py). SEC.gov
    # rejects unidentified traffic with 403; the User-Agent must name an
    # organization and contact per SEC's fair-access policy. The default is
    # a deliberately unusable placeholder, not a real contact, so demo runs
    # fail loudly instead of impersonating whoever happens to run this repo.
    rss_user_agent: str = "market-intelligence-demo set-FI_INTEL_RSS_USER_AGENT@example.invalid"
    sec_edgar_feed_url: str = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        "?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=100&output=atom"
    )
    fed_press_feed_url: str = "https://www.federalreserve.gov/feeds/press_all.xml"
