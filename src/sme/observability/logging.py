"""Structured logging with secret redaction. Never log credentials."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

_SECRET_PATTERNS = [
    # RPC credentials may be embedded in userinfo, arbitrary paths, or queries.
    (re.compile(r"(?:https?|wss?)://[^\s\"'<>]+", re.I), "***REDACTED_URL***"),
    (re.compile(r"(bot)([0-9]{6,}:[A-Za-z0-9_-]{20,})", re.I), r"\1***REDACTED***"),
    (re.compile(r"(api[_-]?key|token|secret|password|webhook)([\"'=:\s]+)([^\s\"']{8,})", re.I), r"\1\2***REDACTED***"),
    (re.compile(r"(discord\.com/api/webhooks/)(\d+/[A-Za-z0-9_-]+)", re.I), r"\1***REDACTED***"),
    (re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]+)", re.I), r"\1***REDACTED***"),
]


def redact_secrets(text: str) -> str:
    if not text:
        return text
    out = text
    for pattern, repl in _SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact_secrets(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_secrets(str(a)) if isinstance(a, str) else a for a in record.args
                )
        return True


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Filtering record.msg alone misses URL-bearing exception tracebacks.
        return redact_secrets(super().format(record))


def _redact_processor(logger: Any, method_name: str, event_dict: dict) -> dict:
    for key, val in list(event_dict.items()):
        if key.lower() in ("token", "api_key", "password", "secret", "webhook_url", "bot_token"):
            event_dict[key] = "***REDACTED***"
        elif isinstance(val, str):
            event_dict[key] = redact_secrets(val)
    return event_dict


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter("%(message)s"))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "sme"):
    return structlog.get_logger(name)
