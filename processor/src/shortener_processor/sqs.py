"""SQS adapter (ElasticMQ locally; Amazon SQS on AWS)."""

import asyncio
from collections.abc import Sequence
from typing import Any

from botocore.config import Config

from shortener_events import from_sqs_attributes
from shortener_processor.queue import ReceivedMessage

# read_timeout must exceed the 20 s long poll; bounded retries keep a sick SQS from stalling us.
SQS_CONFIG = Config(
    connect_timeout=2, read_timeout=25, retries={"total_max_attempts": 3, "mode": "standard"}
)
_DELETE_CHUNK = 10


class SqsQueueClient:
    def __init__(self, client: Any, queue_name: str) -> None:
        self._client = client
        self._queue_name = queue_name
        self._urls: dict[str, str] = {}

    async def _url(self, name: str) -> str:
        if name not in self._urls:
            response = await asyncio.to_thread(self._client.get_queue_url, QueueName=name)
            self._urls[name] = str(response["QueueUrl"])
        return self._urls[name]

    async def receive(self, max_messages: int, wait_seconds: int) -> list[ReceivedMessage]:
        response = await asyncio.to_thread(
            self._client.receive_message,
            QueueUrl=await self._url(self._queue_name),
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
            MessageAttributeNames=["All"],
        )
        return [
            ReceivedMessage(
                message_id=str(m["MessageId"]),
                receipt_handle=str(m["ReceiptHandle"]),
                body=str(m["Body"]),
                attributes=from_sqs_attributes(m.get("MessageAttributes", {})),
            )
            for m in response.get("Messages", [])
        ]

    async def delete(self, receipt_handles: Sequence[str]) -> list[str]:
        url = await self._url(self._queue_name)
        failed: list[str] = []
        for start in range(0, len(receipt_handles), _DELETE_CHUNK):
            chunk = list(receipt_handles[start : start + _DELETE_CHUNK])
            response = await asyncio.to_thread(
                self._client.delete_message_batch,
                QueueUrl=url,
                Entries=[{"Id": str(i), "ReceiptHandle": h} for i, h in enumerate(chunk)],
            )
            failed += [chunk[int(f["Id"])] for f in response.get("Failed", [])]
        return failed

    async def depth(self, queue_name: str) -> int:
        response = await asyncio.to_thread(
            self._client.get_queue_attributes,
            QueueUrl=await self._url(queue_name),
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        return int(response["Attributes"]["ApproximateNumberOfMessages"])
