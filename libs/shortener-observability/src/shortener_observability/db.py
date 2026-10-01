"""Per-engine SQLAlchemy tracing that does not depend on the process-wide instrumentor singleton."""

from collections.abc import Callable

from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer
from opentelemetry.instrumentation.sqlalchemy.version import __version__
from opentelemetry.metrics import MeterProvider, get_meter
from opentelemetry.semconv.metrics import MetricInstruments
from opentelemetry.trace import TracerProvider
from sqlalchemy import Engine, event

_SCOPE = "opentelemetry.instrumentation.sqlalchemy"


def instrument_engine(
    engine: Engine,
    tracer_provider: TracerProvider,
    meter_provider: MeterProvider | None = None,
) -> Callable[[], None]:
    """Trace one engine's queries; return a callable that removes exactly its listeners.

    SQLAlchemyInstrumentor is a singleton: a second instrument(engine=...) in the same process is a
    no-op. EngineTracer is what it uses underneath, so each engine gets its own tracer here.
    """
    connections_usage = get_meter(_SCOPE, __version__, meter_provider).create_up_down_counter(
        MetricInstruments.DB_CLIENT_CONNECTIONS_USAGE, unit="connections"
    )
    registry = EngineTracer._remove_event_listener_params  # class-level; no per-engine API
    start = len(registry)
    EngineTracer(  # type: ignore[no-untyped-call]  # upstream is untyped
        tracer_provider.get_tracer(_SCOPE, __version__), engine, connections_usage
    )
    mine = registry[start:]

    def uninstrument() -> None:
        for entry in mine:
            ref, identifier, func = entry
            target = ref()
            if target is not None and event.contains(target, identifier, func):
                event.remove(target, identifier, func)
            if entry in registry:
                registry.remove(entry)
        mine.clear()

    return uninstrument
