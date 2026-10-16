"""JSON lines to stdout. Only allowlisted fields are written: never bodies, query strings,
secrets or exception messages."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import IO, Any

ALLOWED_FIELDS = (
    "method", "path", "status", "duration_ms", "request_id", "error_type",
)  # fmt: skip
QUIET = ("httpx", "httpx2", "httpcore", "alembic")  # chatty at INFO; URLs include query strings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "at": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", {})
        line.update((key, fields[key]) for key in ALLOWED_FIELDS if key in fields)
        return json.dumps(line, separators=(",", ":"))


def configure_logging(level: str, stream: IO[str] | None = None) -> None:
    """One JSON handler on the root logger. Uvicorn's access log is off: it prints query strings."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
