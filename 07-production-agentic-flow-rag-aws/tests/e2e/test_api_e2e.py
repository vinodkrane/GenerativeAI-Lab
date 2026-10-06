"""End-to-end through HTTP: upload -> ingest -> index -> ask -> retrieve -> generate ->
validate citations -> answer. Uses local backends; the same flow runs against AWS backends."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from helpers import BOARD_TXT, POLICY_MD
from lean_rag.api.app import create_app
from lean_rag.config import Settings
from lean_rag.container import Container
from lean_rag.security.auth import mint_local_token
from lean_rag.worker import Worker

pytestmark = pytest.mark.e2e


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container)) as c:
        yield c


def _auth(settings: Settings, sub: str, tenant: str, groups: list[str]) -> dict[str, str]:
    assert settings.local_jwt_secret
    return {"Authorization": f"Bearer {mint_local_token(settings.local_jwt_secret, sub, tenant, groups)}"}


def _upload(
    client: TestClient,
    container: Container,
    headers: dict[str, str],
    name: str,
    data: bytes,
    groups: list[str],
) -> str:
    resp = client.post("/documents", json={"filename": name, "allowed_groups": groups}, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["document"]["status"] == "PENDING_UPLOAD"
    upload = body["upload"]
    assert upload["method"] == "PUT"
    put = client.put(urlparse(upload["url"]).path, content=data, headers=upload["fields"])
    assert put.status_code == 204, put.text
    Worker(container).drain()
    return str(body["document"]["document_id"])


def test_upload_ingest_query_flow(client: TestClient, container: Container) -> None:
    alice = _auth(container.settings, "alice", "acme", ["staff"])
    doc_id = _upload(client, container, alice, "policy.md", POLICY_MD, ["staff"])

    status = client.get(f"/documents/{doc_id}", headers=alice).json()
    assert status["status"] == "INDEXED" and status["chunk_count"] == 3

    resp = client.post(
        "/query", json={"question": "What is the nightly hotel limit in London?"}, headers=alice
    )
    assert resp.status_code == 200
    answer = resp.json()
    assert not answer["abstained"]
    assert "180" in answer["answer"]
    assert answer["citations"][0]["document_id"] == doc_id
    assert answer["citations"][0]["evidence_id"] in answer["answer"]
    assert resp.headers["x-request-id"]

    unsupported = client.post(
        "/query", json={"question": "Who won the 1966 World Cup final?"}, headers=alice
    ).json()
    assert unsupported["abstained"] and unsupported["citations"] == []


def test_acl_is_enforced_over_http(client: TestClient, container: Container) -> None:
    bob = _auth(container.settings, "bob", "acme", ["board", "staff"])
    alice = _auth(container.settings, "alice", "acme", ["staff"])
    mallory = _auth(container.settings, "mallory", "globex", ["board"])
    doc_id = _upload(client, container, bob, "board.txt", BOARD_TXT, ["board"])

    assert client.get(f"/documents/{doc_id}", headers=alice).status_code == 404
    assert client.get(f"/documents/{doc_id}", headers=mallory).status_code == 404
    assert client.get("/documents", headers=alice).json() == []

    for headers in (alice, mallory):
        result = client.post(
            "/query", json={"question": "What acquisition did the board approve?"}, headers=headers
        )
        assert result.json()["abstained"]
    allowed = client.post("/query", json={"question": "What acquisition did the board approve?"}, headers=bob)
    assert "Brightline" in allowed.json()["answer"]


def test_requests_without_valid_tokens_are_rejected(client: TestClient, container: Container) -> None:
    assert client.post("/query", json={"question": "anything at all"}).status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.get("/documents", headers=bad).status_code == 401


def test_cannot_grant_access_to_foreign_groups_or_spoof_tenant(
    client: TestClient, container: Container
) -> None:
    alice = _auth(container.settings, "alice", "acme", ["staff"])
    resp = client.post("/documents", json={"filename": "x.md", "allowed_groups": ["board"]}, headers=alice)
    assert resp.status_code == 403
    resp = client.post("/documents", json={"filename": "x.md", "tenant_id": "globex"}, headers=alice)
    assert resp.status_code == 422  # unknown fields rejected
    assert client.post("/documents", json={"filename": "x.exe"}, headers=alice).status_code == 415


def test_upload_token_cannot_be_reused_for_other_content_types(
    client: TestClient, container: Container
) -> None:
    alice = _auth(container.settings, "alice", "acme", ["staff"])
    upload = client.post("/documents", json={"filename": "a.md"}, headers=alice).json()["upload"]
    path = urlparse(upload["url"]).path
    assert client.put(path, content=b"x", headers={"Content-Type": "text/html"}).status_code == 400
    assert client.put("/local-upload/forged", content=b"x", headers=upload["fields"]).status_code == 403


def test_versions_delete_and_reprocess(client: TestClient, container: Container) -> None:
    alice = _auth(container.settings, "alice", "acme", ["staff"])
    other = _auth(container.settings, "carol", "acme", ["staff"])
    admin = _auth(container.settings, "root", "acme", ["admin", "staff"])
    doc_id = _upload(client, container, alice, "policy.md", POLICY_MD, ["staff"])

    assert client.post(f"/documents/{doc_id}/versions", json={}, headers=other).status_code == 403
    v2 = client.post(f"/documents/{doc_id}/versions", json={}, headers=alice).json()
    assert v2["document"]["version"] == 2
    client.put(
        urlparse(v2["upload"]["url"]).path,
        content=b"# Policy\n\nHotel limit is 200 GBP.",
        headers=v2["upload"]["fields"],
    )
    Worker(container).drain()
    assert client.get(f"/documents/{doc_id}", headers=alice).json()["status"] == "INDEXED"
    answer = client.post("/query", json={"question": "What is the hotel limit?"}, headers=alice).json()
    assert "200" in answer["answer"] and "180" not in answer["answer"]

    assert client.post(f"/documents/{doc_id}/reprocess", headers=alice).status_code == 403
    assert client.post(f"/documents/{doc_id}/reprocess", headers=admin).status_code == 409  # INDEXED

    assert client.delete(f"/documents/{doc_id}", headers=other).status_code == 403
    assert client.delete(f"/documents/{doc_id}", headers=alice).status_code == 204
    assert client.get(f"/documents/{doc_id}", headers=alice).status_code == 404
    assert container.index.chunk_ids(doc_id) == set()
    gone = client.post("/query", json={"question": "What is the hotel limit?"}, headers=alice).json()
    assert gone["abstained"]


def test_health_endpoints(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
