"""Structured logging setup.

Every log line must carry a run_id so that a single ingestion or research
run can be traced end-to-end during an audit. We bind run_id as context
rather than passing loggers around, which keeps call sites honest.
"""

import hashlib
import logging
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
    reason = _safe_error_reason(error, message)
    reason_field = f"reason={reason}, " if reason is not None else ""
    return f"{type(error).__name__} ({reason_field}message_sha256={digest})"


def _safe_error_reason(error: BaseException, message: str) -> str | None:
    """Classify common infrastructure failures without retaining their raw text."""

    lowered = message.casefold()
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
    return None
