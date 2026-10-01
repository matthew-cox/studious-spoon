import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def meter(metric_reader: InMemoryMetricReader) -> Any:
    return MeterProvider(metric_readers=[metric_reader]).get_meter("test")


@pytest.fixture
def metric_points(metric_reader: InMemoryMetricReader) -> Callable[[str], list[Any]]:
    def _points(name: str) -> list[Any]:
        data = metric_reader.get_metrics_data()
        if data is None:
            return []
        return [
            point
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
            if metric.name == name
            for point in metric.data.data_points
        ]

    return _points


@pytest.fixture
def metric_value(metric_points: Callable[[str], list[Any]]) -> Callable[..., float]:
    """Counter sum / histogram count / gauge value for points whose attributes match exactly."""

    def _value(name: str, attributes: dict[str, str] | None = None) -> float:
        total = 0.0
        for point in metric_points(name):
            if attributes is not None and dict(point.attributes or {}) != attributes:
                continue
            total += getattr(point, "value", None) or getattr(point, "count", 0)
        return total

    return _value


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """build_runtime() rewires the root logger (JSON handler); undo it after each test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
