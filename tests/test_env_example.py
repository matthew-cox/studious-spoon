"""Config contract between docker-compose.yml and .env.example (spec §15.1 III)."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text()
# ${NAME}, ${NAME:-default}, ${NAME:?message}
REFERENCE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(:[-?][^}]*)?\}")


def env_example_names() -> set[str]:
    names = set()
    for line in (REPO_ROOT / ".env.example").read_text().splitlines():
        line = line.lstrip("# ").strip()
        if re.match(r"^[A-Z_][A-Z0-9_]*=", line):
            names.add(line.split("=", 1)[0])
    return names


def references() -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2) or "") for m in REFERENCE.finditer(COMPOSE)]


def test_variables_without_defaults_fail_fast():
    lenient = sorted({name for name, mod in references() if not mod})
    assert lenient == [], f"use ${{NAME:?...}} so compose refuses to start without: {lenient}"


def test_every_compose_variable_is_documented_in_env_example():
    missing = sorted({name for name, _ in references()} - env_example_names())
    assert missing == [], f"add to .env.example (commented out if optional): {missing}"
