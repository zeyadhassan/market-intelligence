"""Structured logging setup.

Every log line must carry a run_id so that a single ingestion or research
run can be traced end-to-end during an audit. We bind run_id as context
rather than passing loggers around, which keeps call sites honest.
"""

import hashlib
import logging
import re
import uuid
from typing import Any

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def new_run_id() -> str:
    """Mint a run identifier and bind it to the current context."""
    run_id = uuid.uuid4().hex
    bind_run_id(run_id)
    return run_id


def bind_run_id(run_id: str) -> None:
    structlog.contextvars.bind_contextvars(run_id=run_id)


def get_logger(**initial_values: Any) -> Any:
    return structlog.get_logger(**initial_values)


def safe_error_summary(error: BaseException) -> str:
    """Identify an error without persisting credentials or source/model payloads."""

    message = str(error)
    digest = hashlib.sha256(message.encode("utf-8", errors="replace")).hexdigest()
    chain = _error_chain(error)
    reason = next(
        (
            candidate_reason
            for candidate in chain
            if (
                candidate_reason := _safe_error_reason(candidate, str(candidate))
            )
            is not None
        ),
        None,
    )
    reason_field = f"reason={reason}, " if reason is not None else ""
    root = chain[-1]
    cause_field = (
        "cause_chain="
        + ">".join(type(item).__name__ for item in chain[1:])
        + ", "
        if root is not error
        else ""
    )
    database_field = _safe_database_field(root)
    return (
        f"{type(error).__name__} "
        f"({reason_field}{cause_field}{database_field}message_sha256={digest})"
    )


def safe_console_error_message(error: BaseException, *, max_length: int = 1_000) -> str:
    """Return a useful bounded console diagnostic with obvious credentials redacted.

    Unlike :func:`safe_error_summary`, this value is never persisted. It exists
    so an operator looking at the local container console can see a gateway's
    actionable HTTP/schema error instead of only an opaque digest.
    """

    if max_length < 1:
        raise ValueError("console error message limit must be positive")
    message = " ".join(str(error).split())
    substitutions = (
        (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(password\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(https?://)[^/@\s]+@", r"\1[REDACTED]@"),
    )
    for pattern, replacement in substitutions:
        message = re.sub(pattern, replacement, message)
    return message[:max_length]


def _safe_error_reason(error: BaseException, message: str) -> str | None:
    """Classify common infrastructure failures without retaining their raw text."""

    lowered = message.casefold()
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if not isinstance(status_code, int):
        status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        if status_code == 400:
            return "model_or_http_bad_request"
        if status_code == 401:
            return "authentication_failed"
        if status_code == 403:
            return "permission_denied"
        if status_code == 404:
            return "endpoint_or_model_not_found"
        if status_code == 422:
            return "structured_request_rejected"
        if status_code == 429:
            return "rate_limited"
        if status_code >= 500:
            return "upstream_service_error"
    if "proxy authentication required" in lowered or (
        "proxy" in lowered and "407" in lowered
    ):
        return "proxy_authentication_required"
    if any(
        marker in lowered
        for marker in (
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname provided",
            "getaddrinfo failed",
        )
    ):
        return "dns_resolution_failed"
    if "certificate_verify_failed" in lowered or "certificate verify failed" in lowered:
        return "tls_certificate_verification_failed"
    if type(error).__name__ == "SourceResponseTruncatedError":
        return "response_length_mismatch"
    if "timed out" in lowered or "timeout" in lowered:
        return "network_timeout"
    if "connection refused" in lowered:
        return "connection_refused"
    if type(error).__name__ in {"JSONDecodeError", "ValidationError"}:
        return "invalid_structured_model_output"
    sqlstate = getattr(error, "sqlstate", None)
    if isinstance(sqlstate, str):
        if sqlstate.startswith("22"):
            return "database_data_error"
        if sqlstate.startswith("23"):
            return "database_integrity_error"
        if sqlstate.startswith(("53", "54")):
            return "database_capacity_error"
        return "database_error"
    return None


def _error_chain(error: BaseException) -> tuple[BaseException, ...]:
    """Return a bounded, cycle-safe explicit cause chain."""

    chain = [error]
    seen = {id(error)}
    current = error
    while current.__cause__ is not None and len(chain) < 5:
        current = current.__cause__
        if id(current) in seen:
            break
        seen.add(id(current))
        chain.append(current)
    return tuple(chain)


def _safe_database_field(error: BaseException) -> str:
    """Expose only PostgreSQL diagnostic identifiers, never row/query values."""

    sqlstate = getattr(error, "sqlstate", None)
    if not isinstance(sqlstate, str) or not re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
        return ""
    fields = [f"sqlstate={sqlstate}"]
    for label, attribute in (
        ("schema", "schema_name"),
        ("table", "table_name"),
        ("column", "column_name"),
        ("constraint", "constraint_name"),
    ):
        value = getattr(error, attribute, None)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]{0,62}", value):
            fields.append(f"{label}={value}")
    return ", ".join(fields) + ", "
