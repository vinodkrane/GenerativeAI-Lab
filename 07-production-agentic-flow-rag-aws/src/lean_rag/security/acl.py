"""Deterministic, default-deny authorization.

``AccessFilter`` is built from the authenticated ``User`` before any retrieval happens and is
passed to the search layer as a separate, immutable object. No agent output schema contains
tenant or ACL fields, so a model cannot widen, replace or drop the filter.
"""

from __future__ import annotations

from dataclasses import dataclass

from lean_rag.domain.models import Chunk, Document, User


class AuthorizationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AccessFilter:
    tenant_id: str
    principals: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.principals:
            raise AuthorizationError("empty access filter")  # default deny

    @classmethod
    def for_user(cls, user: User) -> AccessFilter:
        return cls(tenant_id=user.tenant_id, principals=user.principals)

    def permits(self, chunk: Chunk) -> bool:
        return chunk.tenant_id == self.tenant_id and bool(set(chunk.acl_principals) & set(self.principals))


def can_read_document(user: User, doc: Document) -> bool:
    if doc.tenant_id != user.tenant_id:
        return False
    return bool(set(doc.acl_principals()) & set(user.principals))


def can_manage_document(user: User, doc: Document) -> bool:
    return doc.tenant_id == user.tenant_id and (doc.owner_id == user.sub or user.is_admin)


def validate_grants(user: User, groups: list[str], users: list[str]) -> None:
    """Uploaders may share only with groups they belong to (admins: any group in tenant).

    User grants are scoped by the tenant boundary on every chunk, so a grant to a user in
    another tenant is inert.
    """
    if not user.is_admin:
        foreign = sorted(set(groups) - set(user.groups))
        if foreign:
            raise AuthorizationError(f"cannot grant access to groups you are not in: {foreign}")
    for value in (*groups, *users):
        if not value or len(value) > 128 or ":" in value:
            raise AuthorizationError(f"invalid principal {value!r}")
