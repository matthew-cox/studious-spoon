"""JSON Schema for `link.clicked` v1. Regenerate the committed copy: `scripts/gen-event-schema`."""

import json
from typing import Any

from shortener_events.model import ClickEvent


def json_schema() -> dict[str, Any]:
    return ClickEvent.model_json_schema(mode="serialization")


def json_schema_text() -> str:
    return json.dumps(json_schema(), indent=2, sort_keys=True) + "\n"
