from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ReceivedMessage:
    message_id: str
    receipt_handle: str
    body: str
    attributes: dict[str, str] = field(default_factory=dict)


class QueueClient(Protocol):
    async def receive(self, max_messages: int, wait_seconds: int) -> list[ReceivedMessage]: ...

    async def delete(self, receipt_handles: Sequence[str]) -> list[str]:
        """Delete messages; return the receipt handles that could NOT be deleted."""
        ...

    async def depth(self, queue_name: str) -> int: ...
