from collections.abc import Sequence
from typing import Protocol


class RandomSource(Protocol):
    def choice(self, seq: Sequence[str]) -> str: ...
