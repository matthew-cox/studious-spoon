from shortener_api.telemetry import ApiTelemetry


def test_instruments_use_spec_names(meter, metric_value):
    telemetry = ApiTelemetry(meter)
    telemetry.redirects.add(1, {"result": "ok"})
    telemetry.redirect_duration.record(0.01, {"result": "ok"})
    telemetry.links_created.add(1)
    telemetry.links_blocked.add(1)
    telemetry.click_events_published.add(3)
    telemetry.click_events_dropped.add(2, {"reason": "buffer_full"})
    telemetry.observe_buffer_size(lambda: 42)
    assert metric_value("shortener.redirects", {"result": "ok"}) == 1
    assert metric_value("shortener.redirect.duration", {"result": "ok"}) == 1
    assert metric_value("shortener.links.created") == 1
    assert metric_value("shortener.links.blocked") == 1
    assert metric_value("shortener.click_events.published") == 3
    assert metric_value("shortener.click_events.dropped", {"reason": "buffer_full"}) == 2
    assert metric_value("shortener.click_events.buffer_size") == 42
