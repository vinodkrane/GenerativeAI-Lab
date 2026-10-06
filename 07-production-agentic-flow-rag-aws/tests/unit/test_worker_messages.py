from __future__ import annotations

import json

import pytest

from lean_rag.storage.queue import LocalQueue
from lean_rag.worker import IngestionEvent, MalformedMessage, parse_message, parse_object_key, s3_event


def test_parses_s3_notification_with_url_encoded_key() -> None:
    body = json.dumps(
        {
            "Records": [
                {
                    "eventName": "ObjectCreated:Post",
                    "s3": {"object": {"key": "raw/acme/d1/v2/my+file%281%29.pdf", "size": 10}},
                },
                {"eventName": "ObjectRemoved:Delete", "s3": {"object": {"key": "ignored"}}},
            ]
        }
    )
    assert parse_message(body) == [IngestionEvent("acme", "d1", 2)]


def test_test_event_and_reprocess_messages() -> None:
    assert parse_message('{"Event": "s3:TestEvent"}') == []
    assert parse_message('{"type": "reprocess", "tenant_id": "t", "document_id": "d"}') == [
        IngestionEvent("t", "d", None)
    ]


@pytest.mark.parametrize("key", ["other/acme/d/v1/f", "raw/acme/d/vX/f", "raw/acme/d/v1", "raw//d/v1/f"])
def test_rejects_unexpected_keys(key: str) -> None:
    with pytest.raises(MalformedMessage):
        parse_object_key(key)


def test_ignores_non_raw_keys() -> None:
    body = json.dumps(s3_event("derived/acme/d/v1/chunks.json", 5))
    assert parse_message(body) == []


def test_rejects_non_json() -> None:
    with pytest.raises(MalformedMessage):
        parse_message("not json")


def test_local_queue_mirrors_sqs_redrive(tmp_path: object) -> None:
    queue = LocalQueue(f"{tmp_path}/q.db", visibility_timeout_s=60, max_receive_count=2)
    queue.send(s3_event("k", 1))
    first = queue.receive()[0]
    assert queue.receive() == []  # invisible during the visibility timeout
    queue.release(first)
    second = queue.receive()[0]
    assert second.receive_count == 2
    queue.release(second)
    assert queue.receive() == []  # third delivery attempt -> DLQ
    assert queue.dlq_depth() == 1 and queue.depth() == 0
    assert queue.redrive_dlq() == 1 and queue.depth() == 1


def test_local_queue_delete_requires_current_receipt(tmp_path: object) -> None:
    queue = LocalQueue(f"{tmp_path}/q.db", visibility_timeout_s=0, max_receive_count=5)
    queue.send({"a": 1})
    stale = queue.receive()[0]
    fresh = queue.receive()[0]  # redelivered with a new receipt handle
    queue.delete(stale)
    assert queue.depth() == 1  # stale receipt does not delete
    queue.delete(fresh)
    assert queue.depth() == 0
