import logging

from shortener_observability.setup import configure_telemetry


def test_configure_without_endpoint_exports_nothing_and_works(capsys):
    telemetry = configure_telemetry(
        service_name="shortener-api", service_version="abc123", environment="local",
        otlp_endpoint=None, install_globals=False,
    )  # fmt: skip
    attrs = telemetry.tracer_provider.resource.attributes
    assert (attrs["service.name"], attrs["service.version"], attrs["deployment.environment"]) == (
        "shortener-api", "abc123", "local",
    )  # fmt: skip
    assert telemetry.logger_provider is None
    with telemetry.tracer("t").start_as_current_span("s"):
        logging.getLogger("x").warning("inside")
    telemetry.meter("m").create_counter("c").add(1)
    assert '"message": "inside"' in capsys.readouterr().out
    telemetry.shutdown()
    telemetry.shutdown()  # idempotent


def test_configure_with_endpoint_wires_otlp_logs_without_network():
    telemetry = configure_telemetry(
        service_name="shortener-api", service_version="v", environment="local",
        otlp_endpoint="http://127.0.0.1:1", install_globals=False,
    )  # fmt: skip
    assert telemetry.logger_provider is not None
    assert any(type(h).__name__ == "LoggingHandler" for h in logging.getLogger().handlers)
    telemetry.shutdown()  # must not raise even though the collector is unreachable


def test_unreachable_collector_never_blocks_logging_or_shutdown():
    import time

    telemetry = configure_telemetry(
        service_name="shortener-api", service_version="v", environment="local",
        otlp_endpoint="http://127.0.0.1:9", install_globals=False,
    )  # fmt: skip
    with telemetry.tracer("t").start_as_current_span("s"):
        logging.getLogger("app").warning("still works")
    telemetry.meter("m").create_counter("c").add(1)
    otlp = next(h for h in logging.getLogger().handlers if type(h).__name__ == "LoggingHandler")
    own = logging.LogRecord("opentelemetry.exporter.otlp", logging.ERROR, "f", 1, "x", None, None)
    app = logging.LogRecord("app", logging.ERROR, "f", 1, "x", None, None)
    assert not otlp.filter(own)  # exporter failure logs are not fed back into the OTLP pipeline
    assert otlp.filter(app)
    started = time.monotonic()
    telemetry.shutdown()
    assert time.monotonic() - started < 5


def _otlp_handler():
    return next(h for h in logging.getLogger().handlers if type(h).__name__ == "LoggingHandler")


def test_otlp_handler_rejects_exporter_transport_and_suppressed_records():
    from opentelemetry.context import _SUPPRESS_INSTRUMENTATION_KEY, attach, detach, set_value

    telemetry = configure_telemetry(
        service_name="shortener-api", service_version="v", environment="local",
        otlp_endpoint="http://127.0.0.1:9", log_level="DEBUG", install_globals=False,
    )  # fmt: skip
    otlp = _otlp_handler()

    def record(name):
        return logging.LogRecord(name, logging.DEBUG, "f", 1, "x", None, None)

    assert not otlp.filter(record("urllib3.connectionpool"))
    assert otlp.filter(record("app"))
    token = attach(set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))  # what the exporter threads do
    try:
        assert not otlp.filter(record("anything.else"))
    finally:
        detach(token)
    assert otlp.filter(record("anything.else"))
    telemetry.shutdown()


def test_debug_logging_against_dead_endpoint_does_not_reingest_exporter_logs():
    telemetry = configure_telemetry(
        service_name="shortener-api", service_version="v", environment="local",
        otlp_endpoint="http://127.0.0.1:9", metric_export_interval_ms=100, log_level="DEBUG",
        install_globals=False,
    )  # fmt: skip
    seen: list[str] = []
    # Record what passes the handler's filters instead of exporting it, so a regression fails
    # with the leaked logger names rather than looping forever.
    _otlp_handler().emit = lambda r: seen.append(r.name)  # type: ignore[method-assign]
    telemetry.meter("m").create_counter("c").add(1)
    logging.getLogger("app").debug("one")
    # Run an export cycle now (no sleeping): it fails against the dead endpoint and logs at DEBUG.
    telemetry.meter_provider.force_flush()
    telemetry.shutdown()
    assert seen == ["app"], seen


def _links_kept(telemetry, count: int) -> int:
    from opentelemetry.trace import Link, SpanContext, TraceFlags

    links = [
        Link(SpanContext(trace_id=i + 1, span_id=i + 1, is_remote=True, trace_flags=TraceFlags(1)))
        for i in range(count)
    ]
    with telemetry.tracer("t").start_as_current_span("batch", links=links) as span:
        return len(span.links)


def test_max_span_links_raises_the_link_limit():
    telemetry = configure_telemetry(
        service_name="shortener-click-processor", service_version="v", environment="local",
        otlp_endpoint=None, install_globals=False, max_span_links=500,
    )  # fmt: skip
    assert _links_kept(telemetry, 500) == 500
    telemetry.shutdown()


def test_default_link_limit_is_the_sdk_default():
    telemetry = configure_telemetry(
        service_name="shortener-api", service_version="v", environment="local",
        otlp_endpoint=None, install_globals=False,
    )  # fmt: skip
    assert _links_kept(telemetry, 500) == 128
    telemetry.shutdown()
