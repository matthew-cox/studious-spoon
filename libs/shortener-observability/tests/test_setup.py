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
