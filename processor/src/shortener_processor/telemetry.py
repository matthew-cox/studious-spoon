"""Processor metrics (spec §10). No per-link attributes."""

from collections.abc import Iterable

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import CallbackOptions, Meter, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from shortener_processor.settings import ProcessorSettings


class ProcessorTelemetry:
    def __init__(self, meter: Meter) -> None:
        self.messages = meter.create_counter(
            "shortener.processor.messages", description="Messages handled by result"
        )
        self.batch_duration = meter.create_histogram("shortener.processor.batch.duration", unit="s")
        self.event_lag = meter.create_histogram(
            "shortener.processor.event_lag", unit="s", description="occurred_at → commit"
        )
        self._depths: dict[str, int] = {}
        meter.create_observable_gauge("shortener.queue.depth", callbacks=[self._observe_depths])

    def set_queue_depth(self, queue: str, depth: int) -> None:
        self._depths[queue] = depth

    def _observe_depths(self, _: CallbackOptions) -> Iterable[Observation]:
        for queue, depth in self._depths.items():
            yield Observation(depth, {"queue": queue})


def configure_meter_provider(settings: ProcessorSettings) -> MeterProvider:
    resource = Resource.create(
        {
            "service.name": "shortener-click-processor",
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
