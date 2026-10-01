import pytest

from shortener_processor.settings import ProcessorSettings


@pytest.fixture
def settings() -> ProcessorSettings:
    return ProcessorSettings(
        database_url="postgresql+psycopg://processor_user:x@127.0.0.1:1/shortener",
        sqs_endpoint_url="http://127.0.0.1:1",
        health_host="127.0.0.1",
        health_port=0,
        shutdown_grace_seconds=0.2,
        queue_depth_interval_seconds=0.01,
    )
