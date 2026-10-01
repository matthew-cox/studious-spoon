"""One call per service: resource, traces, metrics, logs (OTLP/HTTP when an endpoint is set)."""

import logging
import warnings
from dataclasses import dataclass, field

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

from shortener_observability.logs import configure_logging

_EXPORT_TIMEOUT_S = 2  # bounds retries against an unreachable collector, so shutdown stays quick


@dataclass
class Telemetry:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider | None
    _shut_down: bool = field(default=False, repr=False)

    def tracer(self, name: str) -> Tracer:
        return self.tracer_provider.get_tracer(name)

    def meter(self, name: str) -> Meter:
        return self.meter_provider.get_meter(name)

    def shutdown(self) -> None:
        if self._shut_down:
            return
        self._shut_down = True
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()
        if self.logger_provider is not None:
            self.logger_provider.shutdown()


def configure_telemetry(
    *,
    service_name: str,
    service_version: str,
    environment: str,
    otlp_endpoint: str | None,
    metric_export_interval_ms: int = 10_000,
    log_level: str = "INFO",
    install_globals: bool = True,
) -> Telemetry:
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )
    base = otlp_endpoint.rstrip("/") if otlp_endpoint else None

    tracer_provider = TracerProvider(resource=resource)
    readers: list[MetricReader] = []
    logger_provider: LoggerProvider | None = None
    extra_handlers: list[logging.Handler] = []
    if base:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{base}/v1/traces", timeout=_EXPORT_TIMEOUT_S)
            )
        )
        readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{base}/v1/metrics", timeout=_EXPORT_TIMEOUT_S),
                export_interval_millis=metric_export_interval_ms,
            )
        )
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=f"{base}/v1/logs", timeout=_EXPORT_TIMEOUT_S)
            )
        )
        with warnings.catch_warnings():
            # SDK 1.45 deprecates this handler in favour of instrumentation-logging, which would
            # add a dependency; the SDK handler is the supported path until that is removed.
            warnings.simplefilter("ignore", DeprecationWarning)
            otlp_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        # The exporters log their own delivery failures; re-ingesting those would feed the pipeline.
        otlp_handler.addFilter(lambda record: not record.name.startswith("opentelemetry"))
        extra_handlers.append(otlp_handler)
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)

    configure_logging(service_name, level=log_level, extra_handlers=extra_handlers)
    if install_globals:
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
    return Telemetry(tracer_provider, meter_provider, logger_provider)
