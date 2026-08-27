"""Static preflight checks for the one canonical analysis service."""

from fi_intel.config import Settings


def canonical_configuration_errors(settings: Settings) -> tuple[str, ...]:
    """Return startup errors detectable without contacting infrastructure."""

    errors: list[str] = []
    if settings.analysis_mode == "fixture":
        errors.append("FI_INTEL_ANALYSIS_MODE must not be fixture for the canonical service")
    if not settings.llm_base_url:
        errors.append("FI_INTEL_LLM_BASE_URL is required")
    if not settings.embedding_base_url:
        errors.append("FI_INTEL_EMBEDDING_BASE_URL is required")
    if not settings.embedding_model:
        errors.append("FI_INTEL_EMBEDDING_MODEL is required")
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
        if not value or not value.strip():
            errors.append(f"{name} is required")
    user_agent = settings.rss_user_agent.strip().casefold()
    if not user_agent or ".invalid" in user_agent or "set-fi_intel" in user_agent:
        errors.append("FI_INTEL_RSS_USER_AGENT must identify the operator and contact")
    return tuple(errors)


__all__ = ["canonical_configuration_errors"]
