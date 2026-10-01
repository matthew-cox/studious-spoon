import json
import logging
import sys

from shortener_observability.logs import JsonFormatter, configure_logging


def record(message: str = "hello", exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("svc.module", logging.WARNING, __file__, 1, message, None, exc_info)


def test_json_formatter_outside_span():
    line = json.loads(JsonFormatter("shortener-api").format(record()))
    assert (
        line["level"] == "WARNING" and line["logger"] == "svc.module" and line["message"] == "hello"
    )
    assert line["service"] == "shortener-api"
    assert line["ts"].endswith("Z")
    assert "trace_id" not in line and "span_id" not in line


def test_json_formatter_inside_span(tracer):
    with tracer.start_as_current_span("work") as span:
        line = json.loads(JsonFormatter("shortener-api").format(record()))
    ctx = span.get_span_context()
    assert line["trace_id"] == f"{ctx.trace_id:032x}"
    assert line["span_id"] == f"{ctx.span_id:016x}"


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        line = json.loads(JsonFormatter("svc").format(record(exc_info=sys.exc_info())))
    assert "ValueError: boom" in line["exception"]


def test_configure_logging_writes_json_to_stdout_and_captures_uvicorn(capsys):
    configure_logging("shortener-api", level="INFO")
    logging.getLogger("uvicorn.access").info("GET / 200")
    logging.getLogger("app").debug("hidden at INFO")
    out = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["message"] for line in out] == ["GET / 200"]
    assert logging.getLogger("uvicorn.access").propagate is True
