"""Strict environment contract for the production analyst API factory."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AnalystApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FI_INTEL_API_",
        extra="ignore",
        case_sensitive=False,
    )

    postgres_dsn: SecretStr
    oidc_issuer: str = Field(min_length=1)
    oidc_audience: str = Field(min_length=1)
    oidc_jwks_url: str = Field(min_length=1)
    service_version: str = "unknown"
    environment: str = "production"
    telemetry_trace_endpoint: str | None = None
    telemetry_metric_endpoint: str | None = None
