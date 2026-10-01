import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, text

from shortener_observability.db import instrument_engine


def traced_engine():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    engine = create_engine("sqlite://")
    return engine, exporter, instrument_engine(engine, provider)


def query(engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("select 1"))


def db_spans(exporter):
    return [s for s in exporter.get_finished_spans() if s.attributes.get("db.system") == "sqlite"]


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_every_engine_in_the_process_gets_its_own_spans(order):
    traced = [traced_engine(), traced_engine()]
    for index in order:
        query(traced[index][0])
    for engine, exporter, uninstrument in traced:
        assert len(db_spans(exporter)) == 1
        uninstrument()
        engine.dispose()


def test_uninstrument_removes_only_that_engines_listeners():
    (first, first_spans, off_first), (second, second_spans, off_second) = (
        traced_engine(),
        traced_engine(),
    )
    off_first()
    off_first()  # idempotent
    query(first)
    query(second)
    assert db_spans(first_spans) == []
    assert len(db_spans(second_spans)) == 1
    off_second()
