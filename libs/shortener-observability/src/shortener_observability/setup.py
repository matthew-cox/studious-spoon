"""One call per service: resource, traces, metrics, logs (OTLP/HTTP when an endpoint is set)."""

import logging
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field

from opentelemetry import metrics, trace
from opentelemetry.context import _SUPPRESS_INSTRUMENTATION_KEY, Context, get_value
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits, TracerProvider, sampling
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult
from opentelemetry.trace import Link, SpanKind, Tracer, TraceState
from opentelemetry.util.types import Attributes

from shortener_observability.logs import configure_logging

_EXPORT_TIMEOUT_S = 2  # bounds retries against an unreachable collector, so shutdown stays quick


# Old-semconv HTTP server metrics copy the request Host header into `http.server_name` (plus
# host/port keys), which is attacker-controlled and would create unbounded series. Keep only
# bounded keys; `http.target` is the route template, not the raw path.
_HTTP_SERVER_METRIC_KEYS = frozenset(
    {
        "http.method",
        "http.scheme",
        "http.status_code",
        "http.flavor",
        "http.target",
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "url.scheme",
        "error.type",
    }
)


def http_server_metric_views() -> list[View]:
    """Views bounding the cardinality of every `http.server.*` metric (duration, size, active)."""
    return [View(instrument_name="http.server.*", attribute_keys=set(_HTTP_SERVER_METRIC_KEYS))]


_TRANSPORT_LOGGERS = ("opentelemetry", "urllib3", "requests")


def _not_exporter_noise(record: logging.LogRecord) -> bool:
    """Keep the exporters' own activity out of the OTLP log pipeline (it would feed itself).

    The batch processors and the periodic reader run exports with instrumentation suppressed, so
    that covers their urllib3/requests DEBUG lines; the logger-name check is a second guard.
    """
    if get_value(_SUPPRESS_INSTRUMENTATION_KEY):
        return False
    return not record.name.startswith(_TRANSPORT_LOGGERS)


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


class SkipSqsPollingRoots(Sampler):
    """The configured sampler (OTEL_TRACES_SAMPLER, default ParentBased(always-on)), except
    SQS polling calls never start a trace of their own.

    Empty long-polls and queue-depth checks would otherwise each become a root trace and bury
    the real ones in Tempo. Inside an existing trace they are still recorded. A receive that
    does return messages is skipped too (the sampler can't see the response); its batch is
    traced by the processor's "process click batch" span.
    """

    POLLING = frozenset({"ReceiveMessage", "GetQueueAttributes", "GetQueueUrl"})

    def __init__(self) -> None:
        # Same env-driven default a bare TracerProvider uses (private helper; pinned by a test).
        self._delegate = sampling._get_from_env_or_default()

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        parent = trace.get_current_span(parent_context).get_span_context()
        if (
            not parent.is_valid
            and attributes
            and attributes.get("rpc.service") == "SQS"
            and attributes.get("rpc.method") in self.POLLING
        ):
            return SamplingResult(Decision.DROP)
        return self._delegate.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return f"SkipSqsPollingRoots({self._delegate.get_description()})"


def configure_telemetry(
    *,
    service_name: str,
    service_version: str,
    environment: str,
    otlp_endpoint: str | None,
    metric_export_interval_ms: int = 10_000,
    log_level: str = "INFO",
    install_globals: bool = True,
    max_span_links: int | None = None,
) -> Telemetry:
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )
    base = otlp_endpoint.rstrip("/") if otlp_endpoint else None

    # The processor links one batch span to every message's producer: raise (never lower) the
    # SDK/env link limit to fit a full batch. None keeps the SDK limit.
    limits = SpanLimits()
    if max_span_links and (limits.max_links is None or max_span_links > limits.max_links):
        limits = SpanLimits(max_links=max_span_links)
    tracer_provider = TracerProvider(
        resource=resource, span_limits=limits, sampler=SkipSqsPollingRoots()
    )
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
        otlp_handler.addFilter(_not_exporter_noise)
        extra_handlers.append(otlp_handler)
    meter_provider = MeterProvider(
        resource=resource, metric_readers=readers, views=http_server_metric_views()
    )

    configure_logging(service_name, level=log_level, extra_handlers=extra_handlers)
    if install_globals:
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
    return Telemetry(tracer_provider, meter_provider, logger_provider)
