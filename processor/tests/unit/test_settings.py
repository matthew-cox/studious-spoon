import pytest
from pydantic import ValidationError

from shortener_processor.settings import load_processor_settings

URL = "postgresql+psycopg://processor_user:pw@db:5432/shortener"


def test_missing_database_url_fails_fast_naming_it(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        load_processor_settings()


def test_defaults_match_the_spec(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    settings = load_processor_settings()
    assert settings.batch_max_messages == 100
    assert settings.batch_window_seconds == 1.0
    assert settings.receive_wait_seconds == 20
    assert settings.queue_depth_interval_seconds == 30.0
    assert settings.click_events_queue_name == "click-events"
    assert settings.click_events_dlq_name == "click-events-dlq"
    assert settings.health_port == 8002


@pytest.mark.parametrize(
    ("name", "value"),
    [("RECEIVE_WAIT_SECONDS", "21"), ("BATCH_MAX_MESSAGES", "0"), ("BATCH_WINDOW_SECONDS", "0")],
)
def test_out_of_range_values_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        load_processor_settings()


def test_receive_wait_of_zero_is_rejected_to_avoid_busy_polling(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("RECEIVE_WAIT_SECONDS", "0")
    with pytest.raises(ValidationError):
        load_processor_settings()
