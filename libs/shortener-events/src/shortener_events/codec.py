from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from shortener_events.model import EVENT_TYPE, ClickEvent

SUPPORTED_VERSIONS = frozenset({1})


class InvalidEventError(ValueError):
    """The message is not a valid `link.clicked` event this code understands."""


class UnsupportedEventVersionError(InvalidEventError):
    """The message is a `link.clicked` event of a version this code does not support."""


@dataclass(frozen=True)
class SqsMessage:
    body: str
    attributes: dict[str, str] = field(default_factory=dict)


def encode(event: ClickEvent, traceparent: str | None = None) -> SqsMessage:
    attributes = {"type": event.type, "version": str(event.version)}
    if traceparent:
        attributes["traceparent"] = traceparent
    return SqsMessage(body=event.model_dump_json(), attributes=attributes)


def decode(message: SqsMessage) -> ClickEvent:
    event_type = message.attributes.get("type")
    if event_type != EVENT_TYPE:
        raise InvalidEventError(f"unexpected event type: {event_type!r}")
    raw_version = message.attributes.get("version")
    if raw_version is None:
        raise InvalidEventError("missing version attribute")
    try:
        version = int(raw_version)
    except ValueError as exc:
        raise InvalidEventError(f"non-numeric version: {raw_version!r}") from exc
    if version not in SUPPORTED_VERSIONS:
        raise UnsupportedEventVersionError(f"unsupported version: {version}")
    try:
        return ClickEvent.model_validate_json(message.body)
    except ValidationError as exc:
        raise InvalidEventError(str(exc)) from exc


def to_sqs_attributes(attrs: Mapping[str, str]) -> dict[str, dict[str, str]]:
    return {key: {"DataType": "String", "StringValue": value} for key, value in attrs.items()}


def from_sqs_attributes(raw: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    return {key: str(value["StringValue"]) for key, value in raw.items() if "StringValue" in value}
