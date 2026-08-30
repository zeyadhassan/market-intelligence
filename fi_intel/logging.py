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

    digest = hashlib.sha256(str(error).encode("utf-8", errors="replace")).hexdigest()
    return f"{type(error).__name__} (message_sha256={digest})"
