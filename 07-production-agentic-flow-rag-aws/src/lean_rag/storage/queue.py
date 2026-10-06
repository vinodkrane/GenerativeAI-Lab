"""Ingestion queue. Production: SQS with a redrive policy to a DLQ.

The local queue implements the SQS semantics the worker relies on - visibility timeout,
receive counts, and moving a message to the DLQ once ``max_receive_count`` is exceeded -
so retry and DLQ behaviour can be exercised without AWS.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Message:
    message_id: str
    body: str
    receipt_handle: str
    receive_count: int


class Queue(Protocol):
    def send(self, body: dict[str, Any]) -> None: ...
    def receive(self, max_messages: int = 1, wait_s: int = 0) -> list[Message]: ...
    def delete(self, message: Message) -> None: ...
    def depth(self) -> int: ...
    def dlq_depth(self) -> int: ...


class SqsQueue:
    def __init__(self, client: Any, queue_url: str, dlq_url: str, visibility_timeout_s: int) -> None:
        self.client = client
        self.queue_url = queue_url
        self.dlq_url = dlq_url
        self.visibility_timeout_s = visibility_timeout_s

    def send(self, body: dict[str, Any]) -> None:
        self.client.send_message(QueueUrl=self.queue_url, MessageBody=json.dumps(body))

    def receive(self, max_messages: int = 1, wait_s: int = 0) -> list[Message]:
        resp = self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_s,
            VisibilityTimeout=self.visibility_timeout_s,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        return [
            Message(
                message_id=m["MessageId"],
                body=m["Body"],
                receipt_handle=m["ReceiptHandle"],
                receive_count=int(m.get("Attributes", {}).get("ApproximateReceiveCount", "1")),
            )
            for m in resp.get("Messages", [])
        ]

    def delete(self, message: Message) -> None:
        self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=message.receipt_handle)

    def _count(self, url: str) -> int:
        attrs = self.client.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"])
        return int(attrs["Attributes"]["ApproximateNumberOfMessages"])

    def depth(self) -> int:
        return self._count(self.queue_url)

    def dlq_depth(self) -> int:
        return self._count(self.dlq_url) if self.dlq_url else 0


class LocalQueue:
    """SQLite-backed queue with SQS-like visibility timeout and DLQ redrive (dev/test only)."""

    def __init__(self, path: str | Path, visibility_timeout_s: int, max_receive_count: int) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self.visibility_timeout_s = visibility_timeout_s
        self.max_receive_count = max_receive_count
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, body TEXT NOT NULL, "
            "visible_at REAL NOT NULL, receive_count INTEGER NOT NULL DEFAULT 0, "
            "receipt TEXT, dlq INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)"
        )

    def send(self, body: dict[str, Any]) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (id, body, visible_at, created_at) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), json.dumps(body), now, now),
            )

    def receive(self, max_messages: int = 1, wait_s: int = 0) -> list[Message]:
        deadline = time.time() + wait_s
        while True:
            messages = self._receive_now(max_messages)
            if messages or time.time() >= deadline:
                return messages
            time.sleep(0.2)

    def _receive_now(self, max_messages: int) -> list[Message]:
        now = time.time()
        out: list[Message] = []
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, body, receive_count FROM messages WHERE dlq = 0 AND visible_at <= ? "
                "ORDER BY created_at LIMIT ?",
                (now, max_messages * 4),
            ).fetchall()
            for msg_id, body, count in rows:
                if count >= self.max_receive_count:
                    # Same rule as an SQS redrive policy: exceeded maxReceiveCount -> DLQ.
                    self._conn.execute("UPDATE messages SET dlq = 1 WHERE id = ?", (msg_id,))
                    continue
                receipt = str(uuid.uuid4())
                self._conn.execute(
                    "UPDATE messages SET receive_count = receive_count + 1, visible_at = ?, receipt = ? "
                    "WHERE id = ?",
                    (now + self.visibility_timeout_s, receipt, msg_id),
                )
                out.append(Message(msg_id, body, receipt, count + 1))
                if len(out) >= max_messages:
                    break
        return out

    def delete(self, message: Message) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM messages WHERE id = ? AND receipt = ?",
                (message.message_id, message.receipt_handle),
            )

    def release(self, message: Message) -> None:
        """Make a message visible again immediately (tests use this to simulate timeout expiry)."""
        with self._lock:
            self._conn.execute("UPDATE messages SET visible_at = 0 WHERE id = ?", (message.message_id,))

    def depth(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM messages WHERE dlq = 0").fetchone()[0])

    def dlq_depth(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM messages WHERE dlq = 1").fetchone()[0])

    def redrive_dlq(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE messages SET dlq = 0, receive_count = 0, visible_at = 0 WHERE dlq = 1"
            )
            return int(cur.rowcount)
