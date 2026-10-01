from shortener_observability.propagation import (
    current_trace_id,
    current_traceparent,
    span_link_from_traceparent,
)


def test_no_traceparent_or_trace_id_outside_a_span():
    assert current_traceparent() is None
    assert current_trace_id() is None


def test_traceparent_inside_a_span(tracer):
    with tracer.start_as_current_span("work") as span:
        value = current_traceparent()
        trace_id = current_trace_id()
    ctx = span.get_span_context()
    assert value == f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{int(ctx.trace_flags):02x}"
    assert trace_id == f"{ctx.trace_id:032x}"


def test_span_link_round_trip(tracer):
    with tracer.start_as_current_span("producer") as span:
        value = current_traceparent()
    link = span_link_from_traceparent(value)
    assert link is not None
    assert link.context.trace_id == span.get_span_context().trace_id
    assert link.context.span_id == span.get_span_context().span_id


def test_invalid_traceparents_give_no_link():
    for value in (None, "", "garbage", "00-" + "0" * 32 + "-" + "0" * 16 + "-01", "01-zz-yy-00"):
        assert span_link_from_traceparent(value) is None
