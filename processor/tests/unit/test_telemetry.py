from shortener_processor.telemetry import ProcessorTelemetry


def test_instruments_use_spec_names(meter, metric_value):
    telemetry = ProcessorTelemetry(meter)
    telemetry.messages.add(3, {"result": "ok"})
    telemetry.messages.add(1, {"result": "invalid"})
    telemetry.batch_duration.record(0.02)
    telemetry.event_lag.record(1.5)
    telemetry.set_queue_depth("click-events", 7)
    telemetry.set_queue_depth("click-events-dlq", 1)
    assert metric_value("shortener.processor.messages", {"result": "ok"}) == 3
    assert metric_value("shortener.processor.messages", {"result": "invalid"}) == 1
    assert metric_value("shortener.processor.batch.duration") == 1
    assert metric_value("shortener.processor.event_lag") == 1
    assert metric_value("shortener.queue.depth", {"queue": "click-events"}) == 7
    assert metric_value("shortener.queue.depth", {"queue": "click-events-dlq"}) == 1


def test_queue_depth_is_silent_until_first_poll(meter, metric_points):
    ProcessorTelemetry(meter)
    assert metric_points("shortener.queue.depth") == []
