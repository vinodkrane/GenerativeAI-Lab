"""JWT authentication.

Production verifies Cognito ID tokens (RS256, JWKS, issuer, audience, expiry, token_use).
Local development uses HS256 tokens minted by ``scripts/dev_token.py``. Identity, tenant and
groups come only from the verified token - never from request bodies or model output.
"""

from __future__ import annotations

import re
import time
from typing import Any, Protocol

import jwt

from lean_rag.config import Settings
from lean_rag.domain.models import User

LOCAL_ISSUER = "lean-rag-local"
LOCAL_AUDIENCE = "lean-rag-local"
_TENANT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # used in S3 keys, so kept path-safe


class AuthError(Exception):
    pass


class Authenticator(Protocol):
    def authenticate(self, token: str) -> User: ...


def _user_from_claims(claims: dict[str, Any], tenant_claim: str, groups_claim: str) -> User:
    sub = claims.get("sub")
    tenant = claims.get(tenant_claim)
    if not isinstance(sub, str) or not sub:
        raise AuthError("token has no subject")
    if not isinstance(tenant, str) or not tenant:
        raise AuthError("token has no tenant")  # default deny: no tenant, no access
    if not _TENANT_ID.match(tenant):
        raise AuthError("malformed tenant id")
    groups = claims.get(groups_claim) or []
    if isinstance(groups, str):
        groups = [groups]
    if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        raise AuthError("malformed groups claim")
    return User(sub=sub, tenant_id=tenant, groups=tuple(sorted(groups)))


class CognitoAuthenticator:
    def __init__(self, settings: Settings) -> None:
        self._issuer = settings.cognito_issuer
        self._audience = settings.cognito_app_client_id
        self._tenant_claim = settings.tenant_claim
        self._groups_claim = settings.groups_claim
        self._jwks = jwt.PyJWKClient(f"{self._issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)

    def authenticate(self, token: str) -> User:
        try:
            key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {exc}") from exc
        if claims.get("token_use") != "id":
            # Custom attributes such as the tenant id are only present on ID tokens.
            raise AuthError("expected a Cognito ID token")
        return _user_from_claims(claims, self._tenant_claim, self._groups_claim)


class LocalAuthenticator:
    def __init__(self, secret: str, tenant_claim: str, groups_claim: str) -> None:
        self._secret = secret
        self._tenant_claim = tenant_claim
        self._groups_claim = groups_claim

    def authenticate(self, token: str) -> User:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=LOCAL_AUDIENCE,
                issuer=LOCAL_ISSUER,
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {exc}") from exc
        return _user_from_claims(claims, self._tenant_claim, self._groups_claim)


def mint_local_token(
    secret: str,
    sub: str,
    tenant_id: str,
    groups: list[str],
    *,
    tenant_claim: str = "custom:tenant_id",
    groups_claim: str = "cognito:groups",
    ttl_s: int = 3600,
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        tenant_claim: tenant_id,
        groups_claim: groups,
        "iss": LOCAL_ISSUER,
        "aud": LOCAL_AUDIENCE,
        "iat": now,
        "exp": now + ttl_s,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def build_authenticator(settings: Settings) -> Authenticator:
    if settings.auth_mode == "cognito":
        return CognitoAuthenticator(settings)
    assert settings.local_jwt_secret  # enforced by Settings validation
    return LocalAuthenticator(settings.local_jwt_secret, settings.tenant_claim, settings.groups_claim)
