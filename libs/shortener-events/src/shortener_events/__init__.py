"""Click event contract shared by producers and the click processor."""

from shortener_events.codec import (
    InvalidEventError,
    SqsMessage,
    UnsupportedEventVersionError,
    decode,
    encode,
    from_sqs_attributes,
    to_sqs_attributes,
)
from shortener_events.model import EVENT_TYPE, REFERRER_MAX, USER_AGENT_MAX, ClickEvent

__all__ = [
    "EVENT_TYPE",
    "REFERRER_MAX",
    "USER_AGENT_MAX",
    "ClickEvent",
    "InvalidEventError",
    "SqsMessage",
    "UnsupportedEventVersionError",
    "decode",
    "encode",
    "from_sqs_attributes",
    "to_sqs_attributes",
]
