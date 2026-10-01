"""W3C trace-context helpers for crossing SQS and for user-visible references."""

from opentelemetry import trace
from opentelemetry.trace import Link
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_PROPAGATOR = TraceContextTextMapPropagator()


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier.get("traceparent")


def span_link_from_traceparent(value: str | None) -> Link | None:
    if not value:
        return None
    context = _PROPAGATOR.extract({"traceparent": value})
    span_context = trace.get_current_span(context).get_span_context()
    return Link(span_context) if span_context.is_valid else None


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return f"{ctx.trace_id:032x}" if ctx.is_valid else None
