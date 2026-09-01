"""Static preflight checks for the one canonical analysis service."""

from __future__ import annotations

from urllib.parse import urlsplit

from fi_intel.config import Settings


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
    _validate_source_scope(settings, errors)
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
    elif not urlsplit(settings.embedding_base_url or "").path.rstrip("/").endswith("/v1"):
        errors.append("FI_INTEL_EMBEDDING_BASE_URL must end with the gateway's /v1 path")
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
    if settings.embedding_dim != 2048:
        errors.append(
            "FI_INTEL_EMBEDDING_DIM must be 2048 for "
            "nvidia/llama-3.2-nv-embedqa-1b-v2 and migration 0024"
        )
    if settings.embedding_model != "nvidia/llama-3.2-nv-embedqa-1b-v2":
        errors.append("FI_INTEL_EMBEDDING_MODEL must be nvidia/llama-3.2-nv-embedqa-1b-v2")
    if settings.embedding_query_prefix:
        errors.append("FI_INTEL_EMBEDDING_QUERY_PREFIX must be empty for NVIDIA NIM")
    if settings.embedding_document_prefix:
        errors.append("FI_INTEL_EMBEDDING_DOCUMENT_PREFIX must be empty for NVIDIA NIM")


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


def _validate_source_scope(settings: Settings, errors: list[str]) -> None:
    required_sources = _csv(settings.coverage_required_source_ids)
    if required_sources:
        from fi_intel.sources.adapters.gcc_official import GCC_OFFICIAL_SOURCES

        registered = {source.source_id for source in GCC_OFFICIAL_SOURCES}
        unknown = set(required_sources) - registered
        if unknown:
            errors.append(
                "FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS contains unregistered sources: "
                f"{sorted(unknown)}"
            )


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
