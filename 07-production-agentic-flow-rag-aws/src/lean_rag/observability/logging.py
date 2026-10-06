"""Structured JSON logging with correlation ids carried in context variables.

CloudWatch Logs ingests one JSON object per line, so Logs Insights can filter on
``tenant_id``, ``document_id``, ``query_id`` etc. Document text is never logged.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

CORRELATION_FIELDS = ("request_id", "query_id", "document_id", "tenant_id", "agent_run_id")
_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("log_context", default={})  # noqa: B039


def bind(**fields: str | None) -> contextvars.Token[dict[str, str]]:
    current = dict(_context.get())
    current.update({k: v for k, v in fields.items() if v is not None})
    return _context.set(current)


@contextlib.contextmanager
def log_context(**fields: str | None) -> Iterator[None]:
    token = bind(**fields)
    try:
        yield
    finally:
        _context.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **_context.get(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("botocore", "urllib3", "opensearch", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, msg: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, msg, extra={"fields": fields})
