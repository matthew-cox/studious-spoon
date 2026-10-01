"""Processor metrics (spec §10). No per-link attributes."""

from collections.abc import Iterable

from opentelemetry.metrics import CallbackOptions, Meter, Observation


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
