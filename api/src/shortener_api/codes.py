"""Short-code generation (spec §4.4). Pure: no framework imports."""

import re
import string
from collections.abc import Sequence
from typing import Protocol

ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 7
CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{1,32}$")
RESERVED_WORDS = frozenset(
    {"api", "admin", "docs", "redoc", "openapi", "healthz", "readyz", "static", "favicon", "robots"}
)


class RandomSource(Protocol):
    def choice(self, seq: Sequence[str]) -> str: ...


def is_reserved(code: str) -> bool:
    return code.lower() in RESERVED_WORDS


def generate_code(rng: RandomSource, length: int = CODE_LENGTH) -> str:
    """Random base62 code that never spells a reserved route name."""
    while True:
        code = "".join(rng.choice(ALPHABET) for _ in range(length))
        if not is_reserved(code):
            return code
