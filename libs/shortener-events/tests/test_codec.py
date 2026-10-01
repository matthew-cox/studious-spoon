import pytest

from shortener_events import (
    InvalidEventError,
    SqsMessage,
    UnsupportedEventVersionError,
    decode,
    encode,
    from_sqs_attributes,
    to_sqs_attributes,
)

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_round_trip_preserves_event(make_event):
    event = make_event()
    assert decode(encode(event, traceparent=TRACEPARENT)) == event


def test_encode_sets_type_version_and_traceparent_attributes(make_event):
    message = encode(make_event(), traceparent=TRACEPARENT)
    assert message.attributes == {
        "type": "link.clicked",
        "version": "1",
        "traceparent": TRACEPARENT,
    }


def test_encode_without_traceparent_omits_it(make_event):
    assert "traceparent" not in encode(make_event()).attributes


def test_sqs_attribute_conversion_round_trips():
    attrs = {"type": "link.clicked", "version": "1"}
    raw = to_sqs_attributes(attrs)
    assert raw["type"] == {"DataType": "String", "StringValue": "link.clicked"}
    assert from_sqs_attributes(raw) == attrs


def test_from_sqs_attributes_ignores_non_string_values():
    raw = {"blob": {"DataType": "Binary", "BinaryValue": b"\x00"}}
    assert from_sqs_attributes(raw) == {}


def test_wrong_type_is_invalid(make_event):
    body = encode(make_event()).body
    with pytest.raises(InvalidEventError, match="type"):
        decode(SqsMessage(body=body, attributes={"type": "link.created", "version": "1"}))


def test_missing_attributes_are_invalid(make_event):
    with pytest.raises(InvalidEventError):
        decode(SqsMessage(body=encode(make_event()).body, attributes={}))


def test_missing_version_is_invalid(make_event):
    body = encode(make_event()).body
    with pytest.raises(InvalidEventError, match="version"):
        decode(SqsMessage(body=body, attributes={"type": "link.clicked"}))


def test_non_numeric_version_is_invalid(make_event):
    body = encode(make_event()).body
    with pytest.raises(InvalidEventError, match="version"):
        decode(SqsMessage(body=body, attributes={"type": "link.clicked", "version": "one"}))


def test_unknown_version_is_unsupported(make_event):
    body = encode(make_event()).body
    with pytest.raises(UnsupportedEventVersionError):
        decode(SqsMessage(body=body, attributes={"type": "link.clicked", "version": "2"}))


def test_unsupported_version_is_an_invalid_event():
    assert issubclass(UnsupportedEventVersionError, InvalidEventError)


def test_malformed_json_is_invalid():
    with pytest.raises(InvalidEventError):
        decode(SqsMessage(body="{not json", attributes={"type": "link.clicked", "version": "1"}))


def test_body_failing_validation_is_invalid():
    body = (
        '{"type":"link.clicked","version":1,"event_id":"e","occurred_at":"2026-10-01T12:00:00",'
        '"source":"api","code":"abc"}'
    )
    with pytest.raises(InvalidEventError):
        decode(SqsMessage(body=body, attributes={"type": "link.clicked", "version": "1"}))


@pytest.mark.parametrize("occurred_at", ["9999-12-31T23:30:00-05:00", "0001-01-01T00:30:00+05:00"])
def test_hostile_occurred_at_is_invalid_not_a_crash(occurred_at):
    body = (
        '{"type":"link.clicked","version":1,"event_id":"e","occurred_at":"' + occurred_at + '",'
        '"source":"api","code":"abc"}'
    )
    with pytest.raises(InvalidEventError):
        decode(SqsMessage(body=body, attributes={"type": "link.clicked", "version": "1"}))
