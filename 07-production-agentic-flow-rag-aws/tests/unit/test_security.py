from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from helpers import ADMIN, ALICE, BOB_BOARD, MALLORY
from lean_rag.config import Settings
from lean_rag.domain.models import Chunk, Document, SearchHit, User
from lean_rag.llm.local import HashingEmbedder, LexicalReranker
from lean_rag.observability.metrics import Metrics
from lean_rag.retrieval.hybrid import HybridRetriever
from lean_rag.retrieval.index import OpenSearchIndex
from lean_rag.security.acl import (
    AccessFilter,
    AuthorizationError,
    can_manage_document,
    can_read_document,
    validate_grants,
)
from lean_rag.security.auth import AuthError, CognitoAuthenticator, LocalAuthenticator, mint_local_token

SECRET = "unit-test-secret-0123456789abcdef"


def _chunk(tenant: str, principals: list[str], cid: str = "c1") -> Chunk:
    return Chunk(
        chunk_id=cid,
        document_id="d",
        tenant_id=tenant,
        document_version=1,
        ordinal=0,
        text="hotel limit",
        acl_principals=principals,
        content_hash="h",
        chunker_version="v",
    )


def test_access_filter_is_default_deny() -> None:
    acl = AccessFilter.for_user(ALICE)
    assert acl.permits(_chunk("acme", ["group:staff"]))
    assert not acl.permits(_chunk("acme", ["group:board"]))
    assert not acl.permits(_chunk("globex", ["group:staff"]))  # same group name, other tenant
    assert not acl.permits(_chunk("acme", []))
    with pytest.raises(AuthorizationError):
        AccessFilter(tenant_id="", principals=("user:x",))
    with pytest.raises(AuthorizationError):
        AccessFilter(tenant_id="acme", principals=())


def test_document_read_and_manage_rules() -> None:
    doc = Document(
        document_id="d",
        tenant_id="acme",
        owner_id="bob",
        filename="m.txt",
        content_type="text/plain",
        allowed_groups=["board"],
    )
    assert can_read_document(BOB_BOARD, doc)
    assert not can_read_document(ALICE, doc)
    assert not can_read_document(MALLORY, doc)
    assert can_manage_document(BOB_BOARD, doc)
    assert can_manage_document(ADMIN, doc)
    assert not can_manage_document(ALICE, doc)
    assert not can_read_document(User(sub="bob", tenant_id="globex"), doc)  # same sub, other tenant


def test_grants_limited_to_own_groups() -> None:
    validate_grants(ALICE, ["staff"], ["carol"])
    with pytest.raises(AuthorizationError, match="not in"):
        validate_grants(ALICE, ["board"], [])
    validate_grants(ADMIN, ["board"], [])
    with pytest.raises(AuthorizationError, match="invalid principal"):
        validate_grants(ALICE, [], ["user:evil"])


class CapturingClient:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        return {"hits": {"hits": []}}


def test_opensearch_queries_always_carry_tenant_and_acl_filter() -> None:
    client = CapturingClient()
    index = OpenSearchIndex(client, "chunks_live", 4)
    acl = AccessFilter.for_user(ALICE)
    index.lexical_search("hotel", acl, 5)
    index.vector_search([0.1, 0.2, 0.3, 0.4], acl, 5, document_id="d1")
    expected = [{"term": {"tenant_id": "acme"}}, {"terms": {"acl_principals": ["user:alice", "group:staff"]}}]
    assert client.bodies[0]["query"]["bool"]["filter"] == expected
    knn_filter = client.bodies[1]["query"]["knn"]["embedding"]["filter"]["bool"]["filter"]
    assert knn_filter == [*expected, {"term": {"document_id": "d1"}}]
    assert client.bodies[0]["_source"] == {"excludes": ["embedding"]}


class LeakyIndex:
    """Simulates a misconfigured index that ignores the ACL filter."""

    alias = "x"

    def live_index(self) -> str:
        return "chunks_v1"

    def lexical_search(self, *args: Any, **kwargs: Any) -> list[SearchHit]:
        return [
            SearchHit(chunk=_chunk("acme", ["group:staff"], "ok"), score=1.0),
            SearchHit(chunk=_chunk("globex", ["group:staff"], "leak"), score=0.9),
        ]


def test_retriever_post_filters_hits_from_a_leaky_index(settings: Settings) -> None:
    metrics = Metrics("t", emit=False)
    retriever = HybridRetriever(
        LeakyIndex(),  # type: ignore[arg-type]
        HashingEmbedder(),
        LexicalReranker(),
        settings.limits,
        metrics,
    )
    hits = retriever.search(["hotel"], AccessFilter.for_user(ALICE), use_vector=False)
    assert [h.chunk.chunk_id for h in hits] == ["ok"]
    assert metrics.counters["AclViolationsBlocked"] == 1


def test_local_tokens_round_trip_and_reject_tampering() -> None:
    auth = LocalAuthenticator(SECRET, "custom:tenant_id", "cognito:groups")
    token = mint_local_token(SECRET, "alice", "acme", ["staff"])
    assert auth.authenticate(token) == ALICE
    with pytest.raises(AuthError):
        auth.authenticate(mint_local_token("another-secret-0123456789abcdef0", "alice", "acme", []))
    with pytest.raises(AuthError):
        auth.authenticate(mint_local_token(SECRET, "alice", "acme", [], ttl_s=-10))
    with pytest.raises(AuthError, match="tenant"):
        auth.authenticate(mint_local_token(SECRET, "alice", "", []))
    with pytest.raises(AuthError, match="malformed tenant"):
        auth.authenticate(mint_local_token(SECRET, "alice", "../acme", []))


class _Key:
    def __init__(self, key: Any) -> None:
        self.key = key


def _cognito(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> tuple[CognitoAuthenticator, Any]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cfg = settings.model_copy(
        update={"cognito_user_pool_id": "eu-west-2_pool", "cognito_app_client_id": "client"}
    )
    auth = CognitoAuthenticator(cfg)
    monkeypatch.setattr(auth._jwks, "get_signing_key_from_jwt", lambda _t: _Key(private.public_key()))
    return auth, private


def _claims(settings: Settings, **overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims = {
        "sub": "alice",
        "custom:tenant_id": "acme",
        "cognito:groups": ["staff"],
        "iss": "https://cognito-idp.eu-west-2.amazonaws.com/eu-west-2_pool",
        "aud": "client",
        "token_use": "id",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return claims


def test_cognito_id_token_verified(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    auth, key = _cognito(settings, monkeypatch)
    token = jwt.encode(_claims(settings), key, algorithm="RS256")
    assert auth.authenticate(token) == ALICE


@pytest.mark.parametrize(
    "override",
    [
        {"aud": "other-client"},
        {"iss": "https://evil.example.com"},
        {"token_use": "access"},
        {"exp": 1},
        {"custom:tenant_id": None},
    ],
)
def test_cognito_rejects_bad_tokens(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, override: dict[str, Any]
) -> None:
    auth, key = _cognito(settings, monkeypatch)
    token = jwt.encode(_claims(settings, **override), key, algorithm="RS256")
    with pytest.raises(AuthError):
        auth.authenticate(token)


def test_cognito_rejects_hs256_downgrade(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    auth, _ = _cognito(settings, monkeypatch)
    token = jwt.encode(_claims(settings), "shared-secret-0123456789abcdef0123", algorithm="HS256")
    with pytest.raises(AuthError):
        auth.authenticate(token)
