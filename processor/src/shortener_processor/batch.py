"""One batch: decode → resolve → aggregate → commit → delete (spec §5.3)."""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from opentelemetry import trace
from opentelemetry.trace import Tracer

from shortener_events import ClickEvent, InvalidEventError, SqsMessage, decode
from shortener_observability import span_link_from_traceparent
from shortener_processor.aggregate import ResolvedClick, aggregate
from shortener_processor.link_resolver import LinkResolver
from shortener_processor.queue import QueueClient, ReceivedMessage
from shortener_processor.referrers import has_control_char, referrer_host
from shortener_processor.rollup_store import CommitResult, RollupStore
from shortener_processor.telemetry import ProcessorTelemetry

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class BatchOutcome:
    ok: int
    invalid: int
    unknown_link: int
    deleted: int
    delete_failures: int


class BatchProcessor:
    def __init__(
        self,
        queue: QueueClient,
        resolver: LinkResolver,
        store: RollupStore,
        telemetry: ProcessorTelemetry,
        *,
        clock: Callable[[], datetime] = utc_now,
        perf_counter: Callable[[], float] = time.perf_counter,
        tracer: Tracer | None = None,
        destination_name: str = "click-events",
    ) -> None:
        self._queue = queue
        self._resolver = resolver
        self._store = store
        self._telemetry = telemetry
        self._clock = clock
        self._perf_counter = perf_counter
        self._tracer = tracer or trace.get_tracer(__name__)
        self._destination_name = destination_name

    def _decode(
        self, messages: Sequence[ReceivedMessage]
    ) -> tuple[list[tuple[ReceivedMessage, ClickEvent]], list[ReceivedMessage], int]:
        """Valid (message, event) pairs, duplicate copies to delete, and the invalid count."""
        valid: list[tuple[ReceivedMessage, ClickEvent]] = []
        duplicates: list[ReceivedMessage] = []
        seen: set[str] = set()
        invalid = 0
        for message in messages:
            try:
                event = decode(SqsMessage(message.body, message.attributes))
            except InvalidEventError as exc:
                invalid += 1
                logger.warning(
                    "invalid click message %s left for redrive: %s", message.message_id, exc
                )
                continue
            if has_control_char(event.code):  # would abort the whole commit in Postgres
                invalid += 1
                logger.warning(
                    "click message %s has control characters in its code; left for redrive",
                    message.message_id,
                )
                continue
            if event.event_id in seen:
                duplicates.append(message)
                continue
            seen.add(event.event_id)
            valid.append((message, event))
        return valid, duplicates, invalid

    async def process(self, messages: Sequence[ReceivedMessage]) -> BatchOutcome:
        links = [
            link
            for message in messages
            if (link := span_link_from_traceparent(message.attributes.get("traceparent")))
            is not None
        ]
        with self._tracer.start_as_current_span(
            "process click batch",
            links=links,
            attributes={
                "messaging.system": "aws_sqs",
                "messaging.destination.name": self._destination_name,
                "messaging.batch.message_count": len(messages),
            },
        ):
            return await self._process(messages)

    async def _process(self, messages: Sequence[ReceivedMessage]) -> BatchOutcome:
        if not messages:
            return BatchOutcome(0, 0, 0, 0, 0)
        started = self._perf_counter()
        valid, duplicates, invalid = self._decode(messages)

        codes = {event.code for _, event in valid if event.link_id is None}
        resolved = await self._resolver.resolve(codes) if codes else {}
        located: list[tuple[ReceivedMessage, ClickEvent, UUID]] = []
        unknown: list[ReceivedMessage] = []
        for message, event in valid:
            link_id = event.link_id or resolved.get(event.code)
            if link_id is None:
                unknown.append(message)
            else:
                located.append((message, event, link_id))

        deltas = aggregate(
            ResolvedClick(link_id, event.occurred_at, referrer_host(event.referrer))
            for _, event, link_id in located
        )
        now = self._clock()
        result = (
            CommitResult(frozenset(), frozenset())
            if deltas.is_empty
            else await self._store.commit(deltas, now)  # raises → nothing below runs
        )
        committed = [(m, e) for m, e, link_id in located if link_id not in result.skipped_links]
        unknown += [m for m, _, link_id in located if link_id in result.skipped_links]

        handles = [m.receipt_handle for m, _ in committed]
        handles += [m.receipt_handle for m in unknown + duplicates]
        failed = await self._queue.delete(handles) if handles else []
        if failed:
            logger.warning(
                "%d processed messages could not be deleted; they will be recounted", len(failed)
            )
        # Nothing sits between commit and delete; metrics are recorded afterwards.
        self._telemetry.messages.add(len(committed), {"result": "ok"})
        self._telemetry.messages.add(invalid, {"result": "invalid"})
        self._telemetry.messages.add(len(unknown), {"result": "unknown_link"})
        for _, event in committed:
            self._telemetry.event_lag.record(max(0.0, (now - event.occurred_at).total_seconds()))
        self._telemetry.batch_duration.record(self._perf_counter() - started)
        return BatchOutcome(
            ok=len(committed),
            invalid=invalid,
            unknown_link=len(unknown),
            deleted=len(handles) - len(failed),
            delete_failures=len(failed),
        )
