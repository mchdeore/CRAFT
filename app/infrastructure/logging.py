"""JSON lines to stdout. Only allowlisted fields are written: never bodies, query strings,
secrets or exception messages."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import IO, Any

ALLOWED_FIELDS = (
    "method", "path", "status", "duration_ms", "request_id", "error_type",
    "kind", "name", "origin", "outcome", "error_code", "db_statements", "db_ms",
    "call_id", "parent_call_id", "user_id", "auth_session_id",
    "conversation_id", "run_id", "tool_call_id", "job_id", "queue", "attempt", "scheduled",
)  # fmt: skip
QUIET = (
    "httpx",
    "httpx2",
    "httpcore",
    "alembic",
    "mcp",
)  # chatty at INFO; URLs include query strings
# procrastinate's own lines carry job arguments; app.infrastructure.queue logs jobs instead.
PAYLOAD_LOGGERS = (
    "openai",
    "azure",
    "agent_framework",
    "httpx",
    "httpx2",
    "httpcore",
    "procrastinate",
)


class PayloadFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not any(record.name.startswith(name) for name in PAYLOAD_LOGGERS)


class ProtocolPayloadFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name != "root" and not (
            record.name == "mcp" or record.name.startswith("mcp.")
        )


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
        return json.dumps(line, separators=(",", ":"), default=str)


def configure_logging(level: str, stream: IO[str] | None = None) -> None:
    """One JSON handler on the root logger. Uvicorn's access log is off: it prints query strings."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(PayloadFilter())
    handler.addFilter(ProtocolPayloadFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.filters[:] = [f for f in root.filters if not isinstance(f, ProtocolPayloadFilter)]
    root.addFilter(ProtocolPayloadFilter())
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.CRITICAL + 1)
    for name in PAYLOAD_LOGGERS:
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
    for name, logger in logging.Logger.manager.loggerDict.items():
        if isinstance(logger, logging.Logger) and any(name.startswith(p) for p in PAYLOAD_LOGGERS):
            logger.setLevel(logging.CRITICAL + 1)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def instrument_sql(engine: Any) -> None:
    """Count statements and elapsed time without reading SQL or parameters."""
    from sqlalchemy import event

    from app.infrastructure.operations.context import end_statement, start_statement

    def before(*_: Any) -> None:
        start_statement()

    def after(*_: Any) -> None:
        end_statement()

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "handle_error", after)
