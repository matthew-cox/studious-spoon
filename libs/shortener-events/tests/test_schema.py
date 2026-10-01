import json
from pathlib import Path

from shortener_events.schema import json_schema, json_schema_text

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "link.clicked.v1.json"


def test_committed_schema_matches_model():
    assert json.loads(SCHEMA_PATH.read_text()) == json_schema()


def test_schema_text_is_sorted_pretty_json_with_trailing_newline():
    text = json_schema_text()
    assert text.endswith("}\n")
    assert json.loads(text) == json_schema()
    assert text == json.dumps(json_schema(), indent=2, sort_keys=True) + "\n"
