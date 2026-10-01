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
