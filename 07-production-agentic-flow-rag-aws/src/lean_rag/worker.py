"""Ingestion worker: long-polls SQS and drives documents through the ingestion supervisor.

Delivery is at-least-once. Safety comes from: compare-and-set status transitions, a per
document lease, deterministic chunk ids (upserts overwrite), and S3-persisted step output.
A message is deleted only after the document reaches a terminal state. Transient failures
leave the message in the queue; after ``max_receive_count`` deliveries SQS moves it to the
DLQ and the document is marked FAILED("retries exhausted").
"""

from __future__ import annotations

import json
import logging
import signal
import socket
import threading
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote_plus

from lean_rag.config import get_settings
from lean_rag.container import Container, build_container
from lean_rag.observability.logging import configure_logging, log_context, log_event
from lean_rag.storage.queue import Message

logger = logging.getLogger(__name__)


class MalformedMessage(Exception):
    pass


@dataclass(frozen=True)
class IngestionEvent:
    tenant_id: str
    document_id: str
    version: int | None  # None for operator reprocess requests


def parse_object_key(key: str) -> tuple[str, str, int]:
    """``raw/{tenant}/{document_id}/v{version}/{filename}``"""
    parts = key.split("/")
    if len(parts) < 5 or parts[0] != "raw" or not parts[3].startswith("v") or not all(parts[:5]):
        raise MalformedMessage(f"unexpected object key layout: {key}")
    try:
        version = int(parts[3][1:])
    except ValueError as exc:
        raise MalformedMessage(f"bad version in key: {key}") from exc
    return parts[1], parts[2], version


def parse_message(body: str) -> list[IngestionEvent]:
    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as exc:
        raise MalformedMessage("message body is not JSON") from exc
    if payload.get("Event") == "s3:TestEvent":
        return []
    if payload.get("type") == "reprocess":
        return [IngestionEvent(str(payload["tenant_id"]), str(payload["document_id"]), None)]
    records = payload.get("Records")
    if not isinstance(records, list):
        raise MalformedMessage("message has no Records")
    events: list[IngestionEvent] = []
    for record in records:
        if not str(record.get("eventName", "")).startswith("ObjectCreated"):
            continue
        key = unquote_plus(record["s3"]["object"]["key"])
        if not key.startswith("raw/"):
            continue  # only raw uploads start ingestion (the notification filter should ensure this)
        tenant, document_id, version = parse_object_key(key)
        events.append(IngestionEvent(tenant, document_id, version))
    return events


def s3_event(key: str, size: int) -> dict[str, Any]:
    """S3-notification-shaped body, used by the local upload endpoint to mimic S3 -> SQS."""
    return {"Records": [{"eventName": "ObjectCreated:Put", "s3": {"object": {"key": key, "size": size}}}]}


class Worker:
    def __init__(self, container: Container, worker_id: str | None = None) -> None:
        self.c = container
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self.stop = threading.Event()

    def handle(self, message: Message) -> bool:
        """Process one message. Returns True if it was deleted (done or not retryable)."""
        limits = self.c.settings.limits
        with log_context(request_id=message.message_id):
            try:
                events = parse_message(message.body)
            except (MalformedMessage, KeyError, TypeError) as exc:
                # Leave it: it will reach the DLQ after max_receive_count for inspection.
                log_event(logger, "malformed_message", logging.ERROR, error=str(exc))
                return False
            try:
                for event in events:
                    self._process_event(event)
            except Exception as exc:
                final = message.receive_count >= limits.max_receive_count
                log_event(
                    logger,
                    "ingestion_attempt_failed",
                    logging.ERROR if final else logging.WARNING,
                    receive_count=message.receive_count,
                    final_attempt=final,
                    error=f"{type(exc).__name__}: {exc}",
                )
                if final:
                    for event in events:
                        self.c.ingestion.mark_retries_exhausted(
                            event.document_id, f"{type(exc).__name__}: {exc}"
                        )
                return False
            self.c.queue.delete(message)
            return True

    def _process_event(self, event: IngestionEvent) -> None:
        with log_context(document_id=event.document_id, tenant_id=event.tenant_id):
            if not self.c.repo.claim(
                event.document_id, self.worker_id, self.c.settings.sqs_visibility_timeout_s
            ):
                # Another worker holds the lease and owns the retry for its own message.
                log_event(logger, "document_leased_elsewhere")
                return
            try:
                if event.version is None:
                    self.c.ingestion.process(event.document_id)
                else:
                    self.c.ingestion.handle_object_created(event.tenant_id, event.document_id, event.version)
            finally:
                self.c.repo.release(event.document_id, self.worker_id)

    def poll_once(self, wait_s: int = 0) -> int:
        messages = self.c.queue.receive(max_messages=1, wait_s=wait_s)
        for message in messages:
            self.handle(message)
        return len(messages)

    def drain(self, max_messages: int = 1000) -> int:
        """Process until the queue has nothing visible (used by local runs, tests and evals)."""
        processed = 0
        while processed < max_messages and self.poll_once(wait_s=0):
            processed += 1
        return processed

    def run_forever(self) -> None:
        log_event(logger, "worker_started", worker_id=self.worker_id)
        polls = 0
        while not self.stop.is_set():
            self.poll_once(wait_s=self.c.settings.sqs_wait_time_s)
            polls += 1
            if polls % 30 == 0 and self.c.settings.queue == "local":
                # SQS publishes these natively in AWS; emit them locally for parity.
                self.c.metrics.put("QueueDepth", self.c.queue.depth())
                self.c.metrics.put("DlqDepth", self.c.queue.dlq_depth())
        log_event(logger, "worker_stopped", worker_id=self.worker_id)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker = Worker(build_container(settings))
    signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
    worker.run_forever()


if __name__ == "__main__":
    main()
