"""API metrics (spec §10). No per-link attributes, ever."""

from collections.abc import Callable, Iterable

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import CallbackOptions, Meter, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from shortener_api.settings import ApiSettings


class ApiTelemetry:
    def __init__(self, meter: Meter) -> None:
        self.redirects = meter.create_counter(
            "shortener.redirects", description="Redirect requests by result"
        )
        self.redirect_duration = meter.create_histogram(
            "shortener.redirect.duration", unit="s", description="Redirect handling time"
        )
        self.links_created = meter.create_counter("shortener.links.created")
        self.links_blocked = meter.create_counter("shortener.links.blocked")
        self.click_events_published = meter.create_counter("shortener.click_events.published")
        self.click_events_dropped = meter.create_counter("shortener.click_events.dropped")
        self._buffer_size: Callable[[], int] = lambda: 0
        meter.create_observable_gauge(
            "shortener.click_events.buffer_size", callbacks=[self._observe_buffer]
        )

    def observe_buffer_size(self, fn: Callable[[], int]) -> None:
        self._buffer_size = fn

    def _observe_buffer(self, _: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self._buffer_size())


def configure_meter_provider(settings: ApiSettings) -> MeterProvider:
    resource = Resource.create(
        {
            "service.name": "shortener-api",
            "service.version": settings.service_version,
            "deployment.environment": settings.deployment_environment,
        }
    )
    readers: list[MetricReader] = []
    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPMetricExporter(
            endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/metrics"
        )
        readers.append(PeriodicExportingMetricReader(exporter, export_interval_millis=10_000))
    return MeterProvider(resource=resource, metric_readers=readers)
