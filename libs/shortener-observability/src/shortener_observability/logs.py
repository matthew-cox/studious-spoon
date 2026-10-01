"""JSON-lines logging on stdout with the active span's ids (spec §10)."""

import json
import logging
import sys
from collections.abc import Iterable
from datetime import UTC, datetime

from opentelemetry import trace

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class RedactAccessQuery(logging.Filter):
    """Drop the query string from uvicorn access lines: OIDC callbacks carry code and state.

    uvicorn logs '%s - "%s %s HTTP/%s" %d' with args (client, method, path_with_query, ...).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service = service_name

    def format(self, record: logging.LogRecord) -> str:
        moment = datetime.fromtimestamp(record.created, UTC)
        payload: dict[str, object] = {
            "ts": moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
        }
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            payload["trace_id"] = f"{ctx.trace_id:032x}"
            payload["span_id"] = f"{ctx.span_id:016x}"
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    service_name: str, *, level: str = "INFO", extra_handlers: Iterable[logging.Handler] = ()
) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(JsonFormatter(service_name))
    root.addHandler(stream)
    for handler in extra_handlers:
        root.addHandler(handler)
    root.setLevel(level)
    for name in _UVICORN_LOGGERS:  # uvicorn installs its own text handlers; route them through ours
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactAccessQuery) for f in access.filters):
        access.addFilter(RedactAccessQuery())
