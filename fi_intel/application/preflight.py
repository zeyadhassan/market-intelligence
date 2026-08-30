"""Static preflight checks for the one canonical analysis service."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from fi_intel.config import Settings
from fi_intel.entities.identifiers import (
    IdentifierScheme,
    IdentifierValidationError,
    normalize_identifier,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _configured(value: str | None) -> bool:
    if value is None or not value.strip():
        return False
    normalized = value.strip().casefold()
    return "replace_with" not in normalized and "replace-me" not in normalized


def _valid_http_url(value: str | None) -> bool:
    if not _configured(value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _csv(value: str) -> tuple[str, ...]:
    return tuple(sorted({item.strip() for item in value.split(",") if item.strip()}))


def canonical_configuration_errors(settings: Settings) -> tuple[str, ...]:
    """Return startup errors detectable without contacting infrastructure."""

    errors: list[str] = []
    if settings.analysis_mode == "fixture":
        errors.append("FI_INTEL_ANALYSIS_MODE must not be fixture for the canonical service")
    _validate_model_endpoints(settings, errors)
    _validate_identity(settings, errors)
    _validate_source_scope(settings, errors)
    _validate_model_release_configuration(settings, errors)
    _validate_email(settings, errors)
    return tuple(errors)


def _validate_model_endpoints(settings: Settings, errors: list[str]) -> None:
    if not _configured(settings.llm_base_url):
        errors.append("FI_INTEL_LLM_BASE_URL is required")
    elif not _valid_http_url(settings.llm_base_url):
        errors.append("FI_INTEL_LLM_BASE_URL must be an http(s) URL")
    _validate_embedding_endpoint(settings, errors)
    _validate_model_names(settings, errors)
    _validate_model_transport(settings, errors)


def _validate_embedding_endpoint(settings: Settings, errors: list[str]) -> None:
    if not _configured(settings.embedding_base_url):
        errors.append("FI_INTEL_EMBEDDING_BASE_URL is required")
    elif not _valid_http_url(settings.embedding_base_url):
        errors.append("FI_INTEL_EMBEDDING_BASE_URL must be an http(s) URL")
    elif not urlsplit(settings.embedding_base_url or "").path.rstrip("/").endswith("/api"):
        errors.append("FI_INTEL_EMBEDDING_BASE_URL must end with Ollama's /api path")
    if not _configured(settings.embedding_model):
        errors.append("FI_INTEL_EMBEDDING_MODEL is required")


def _validate_model_names(settings: Settings, errors: list[str]) -> None:
    for name, value in (
        ("FI_INTEL_EXTRACTION_MODEL", settings.extraction_model),
        ("FI_INTEL_RESEARCH_MODEL", settings.research_model),
        ("FI_INTEL_ENTAILMENT_MODEL", settings.entailment_model),
        ("FI_INTEL_RERANKER_MODEL", settings.reranker_model),
    ):
        if not _configured(value):
            errors.append(f"{name} is required")
    if settings.embedding_dim != 768:
        errors.append(
            "FI_INTEL_EMBEDDING_DIM must be 768 for nomic-embed-text:v1.5 and migration 0023"
        )
    if settings.embedding_model != "nomic-embed-text:v1.5":
        errors.append("FI_INTEL_EMBEDDING_MODEL must be nomic-embed-text:v1.5")
    if settings.embedding_query_prefix != "search_query: ":
        errors.append('FI_INTEL_EMBEDDING_QUERY_PREFIX must be "search_query: "')
    if settings.embedding_document_prefix != "search_document: ":
        errors.append('FI_INTEL_EMBEDDING_DOCUMENT_PREFIX must be "search_document: "')


def _validate_model_transport(settings: Settings, errors: list[str]) -> None:
    _validate_basic_auth_pair(
        "FI_INTEL_LLM",
        settings.llm_basic_auth_username,
        (
            settings.llm_basic_auth_password.get_secret_value()
            if settings.llm_basic_auth_password is not None
            else None
        ),
        errors,
    )
    _validate_basic_auth_pair(
        "FI_INTEL_EMBEDDING",
        settings.embedding_basic_auth_username,
        (
            settings.embedding_basic_auth_password.get_secret_value()
            if settings.embedding_basic_auth_password is not None
            else None
        ),
        errors,
    )
    if settings.analysis_mode in {"pilot", "production"}:
        if not settings.llm_tls_verify:
            errors.append("FI_INTEL_LLM_TLS_VERIFY must be true outside shadow mode")
        if not settings.embedding_tls_verify:
            errors.append("FI_INTEL_EMBEDDING_TLS_VERIFY must be true outside shadow mode")


def _validate_basic_auth_pair(
    prefix: str,
    username: str | None,
    password: str | None,
    errors: list[str],
) -> None:
    if _configured(username) != _configured(password):
        errors.append(
            f"{prefix}_BASIC_AUTH_USERNAME and {prefix}_BASIC_AUTH_PASSWORD are required together"
        )


def _validate_identity(settings: Settings, errors: list[str]) -> None:
    for name, value in (
        ("FI_INTEL_OIDC_ISSUER", settings.oidc_issuer),
        ("FI_INTEL_OIDC_AUDIENCE", settings.oidc_audience),
        ("FI_INTEL_OIDC_JWKS_URL", settings.oidc_jwks_url),
        ("FI_INTEL_ACCESS_SUBJECT", settings.access_subject),
        ("FI_INTEL_ACCESS_PRINCIPAL_ID", settings.access_principal_id),
        ("FI_INTEL_ACCESS_ENTITLEMENT_GROUP", settings.access_entitlement_group),
        ("FI_INTEL_ACCESS_DESKS", settings.access_desks),
        ("FI_INTEL_ACCESS_ROLES", settings.access_roles),
        ("FI_INTEL_ACCESS_PURPOSES", settings.access_purposes),
    ):
        if not _configured(value):
            errors.append(f"{name} is required")
    for name, value in (
        ("FI_INTEL_OIDC_ISSUER", settings.oidc_issuer),
        ("FI_INTEL_OIDC_JWKS_URL", settings.oidc_jwks_url),
    ):
        if _configured(value) and not _valid_http_url(value):
            errors.append(f"{name} must be an http(s) URL")
    user_agent = settings.rss_user_agent.strip().casefold()
    if (
        not _configured(settings.rss_user_agent)
        or ".invalid" in user_agent
        or "set-fi_intel" in user_agent
    ):
        errors.append("FI_INTEL_RSS_USER_AGENT must identify the operator and contact")


def _validate_source_scope(settings: Settings, errors: list[str]) -> None:
    required_sources = _csv(settings.coverage_required_source_ids)
    if not required_sources or any(not _configured(item) for item in required_sources):
        errors.append("FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS is required")
    else:
        from fi_intel.sources.adapters.gcc_official import GCC_OFFICIAL_SOURCES

        registered = {source.source_id for source in GCC_OFFICIAL_SOURCES}
        unknown = set(required_sources) - registered
        if unknown:
            errors.append(
                "FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS contains unregistered sources: "
                f"{sorted(unknown)}"
            )

    leis = _csv(settings.covered_entity_leis)
    if not leis or any(not _configured(item) for item in leis):
        errors.append("FI_INTEL_COVERED_ENTITY_LEIS is required")
    else:
        invalid_leis: list[str] = []
        for lei in leis:
            try:
                normalize_identifier(IdentifierScheme.LEI, lei)
            except IdentifierValidationError:
                invalid_leis.append(lei)
        if invalid_leis:
            errors.append(
                f"FI_INTEL_COVERED_ENTITY_LEIS contains invalid LEIs: {sorted(invalid_leis)}"
            )


def _validate_model_release_configuration(settings: Settings, errors: list[str]) -> None:
    if not settings.model_quality_gate_passed:
        errors.append("FI_INTEL_MODEL_QUALITY_GATE_PASSED must be true")
    _validate_model_digests(settings, errors)
    _validate_model_release_ids(settings, errors)
    _validate_model_timestamps(settings, errors)
    if not _configured(settings.model_release_created_by):
        errors.append("FI_INTEL_MODEL_RELEASE_CREATED_BY is required")


def _validate_model_digests(settings: Settings, errors: list[str]) -> None:
    for name, value in (
        (
            "FI_INTEL_MODEL_EVALUATION_DATASET_DIGEST",
            settings.model_evaluation_dataset_digest,
        ),
        ("FI_INTEL_MODEL_EVALUATION_REPORT_DIGEST", settings.model_evaluation_report_digest),
        ("FI_INTEL_EXTRACTION_ARTIFACT_DIGEST", settings.extraction_artifact_digest),
        ("FI_INTEL_REASONING_ARTIFACT_DIGEST", settings.reasoning_artifact_digest),
        ("FI_INTEL_EMBEDDING_ARTIFACT_DIGEST", settings.embedding_artifact_digest),
        ("FI_INTEL_RERANKER_ARTIFACT_DIGEST", settings.reranker_artifact_digest),
        ("FI_INTEL_ENTAILMENT_ARTIFACT_DIGEST", settings.entailment_artifact_digest),
    ):
        if _SHA256.fullmatch(value) is None or value == "0" * 64:
            errors.append(f"{name} must be a non-placeholder lowercase SHA-256 digest")


def _validate_model_release_ids(settings: Settings, errors: list[str]) -> None:
    for name, value in (
        ("FI_INTEL_EXTRACTION_RELEASE_ID", settings.extraction_release_id),
        ("FI_INTEL_REASONING_RELEASE_ID", settings.reasoning_release_id),
        ("FI_INTEL_EMBEDDING_RELEASE_ID", settings.embedding_release_id),
        ("FI_INTEL_RERANKER_RELEASE_ID", settings.reranker_release_id),
        ("FI_INTEL_ENTAILMENT_RELEASE_ID", settings.entailment_release_id),
    ):
        try:
            parsed = UUID(value)
        except ValueError:
            parsed = UUID(int=0)
        if parsed.int == 0:
            errors.append(f"{name} must be a non-placeholder UUID")


def _validate_model_timestamps(settings: Settings, errors: list[str]) -> None:
    timestamps: dict[str, datetime] = {}
    for name, value in (
        ("FI_INTEL_MODEL_EVALUATED_AT", settings.model_evaluated_at),
        ("FI_INTEL_MODEL_REGISTERED_AT", settings.model_registered_at),
    ):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            timestamps[name] = parsed
        except ValueError:
            errors.append(f"{name} must be an ISO-8601 timestamp with a timezone")
    if (
        len(timestamps) == 2
        and timestamps["FI_INTEL_MODEL_EVALUATED_AT"] > timestamps["FI_INTEL_MODEL_REGISTERED_AT"]
    ):
        errors.append("FI_INTEL_MODEL_EVALUATED_AT must not follow registration")


def _validate_email(settings: Settings, errors: list[str]) -> None:
    if not settings.email_enabled:
        return
    if not _csv(settings.email_recipient_allowlist):
        errors.append("FI_INTEL_EMAIL_RECIPIENT_ALLOWLIST is required when email is enabled")
    if not _configured(settings.email_destination_key):
        errors.append("FI_INTEL_EMAIL_DESTINATION_KEY is required when email is enabled")
        return
    from fi_intel.application.delivery import DestinationCodec

    try:
        DestinationCodec(settings.email_destination_key or "")
    except ValueError:
        errors.append("FI_INTEL_EMAIL_DESTINATION_KEY must be a valid Fernet key")


__all__ = ["canonical_configuration_errors"]
