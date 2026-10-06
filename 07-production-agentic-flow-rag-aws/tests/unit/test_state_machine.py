from __future__ import annotations

import itertools

import pytest

from lean_rag.container import Container
from lean_rag.domain.models import DocStatus as S
from lean_rag.domain.models import Document
from lean_rag.domain.state_machine import (
    IN_PROGRESS,
    TERMINAL,
    TRANSITIONS,
    IllegalTransition,
    assert_transition,
    can_transition,
)

LEGAL = {
    (S.PENDING_UPLOAD, S.RECEIVED),
    (S.PENDING_UPLOAD, S.FAILED),
    (S.PENDING_UPLOAD, S.DELETED),
    (S.RECEIVED, S.PARSING),
    (S.RECEIVED, S.DUPLICATE),
    (S.RECEIVED, S.FAILED),
    (S.RECEIVED, S.DELETED),
    (S.PARSING, S.CHUNKING),
    (S.PARSING, S.FAILED),
    (S.CHUNKING, S.EMBEDDING),
    (S.CHUNKING, S.FAILED),
    (S.EMBEDDING, S.INDEXING),
    (S.EMBEDDING, S.FAILED),
    (S.INDEXING, S.VERIFYING),
    (S.INDEXING, S.FAILED),
    (S.VERIFYING, S.INDEXED),
    (S.VERIFYING, S.NEEDS_REVIEW),
    (S.VERIFYING, S.CHUNKING),
    (S.VERIFYING, S.FAILED),
    (S.INDEXED, S.PENDING_UPLOAD),
    (S.INDEXED, S.DELETED),
    (S.NEEDS_REVIEW, S.PENDING_UPLOAD),
    (S.NEEDS_REVIEW, S.RECEIVED),
    (S.NEEDS_REVIEW, S.DELETED),
    (S.DUPLICATE, S.PENDING_UPLOAD),
    (S.DUPLICATE, S.DELETED),
    (S.FAILED, S.PENDING_UPLOAD),
    (S.FAILED, S.RECEIVED),
    (S.FAILED, S.DELETED),
}
ALL_PAIRS = list(itertools.product(S, S))


def test_table_matches_documented_edges() -> None:
    actual = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}
    assert actual == LEGAL
    assert set(TRANSITIONS) == set(S)


@pytest.mark.parametrize(("current", "target"), sorted(LEGAL))
def test_legal_transitions_are_allowed(current: S, target: S) -> None:
    assert can_transition(current, target)
    assert_transition(current, target)


@pytest.mark.parametrize(("current", "target"), [p for p in ALL_PAIRS if p not in LEGAL])
def test_illegal_transitions_raise(current: S, target: S) -> None:
    assert not can_transition(current, target)
    with pytest.raises(IllegalTransition):
        assert_transition(current, target)


def test_deleted_is_final_and_in_progress_states_cannot_be_deleted() -> None:
    assert TRANSITIONS[S.DELETED] == frozenset()
    for state in IN_PROGRESS:
        assert not can_transition(state, S.DELETED)
    assert IN_PROGRESS.isdisjoint(TERMINAL)


def _doc(container: Container) -> Document:
    doc = Document(
        document_id="d1", tenant_id="t", owner_id="u", filename="a.md", content_type="text/markdown"
    )
    container.repo.create(doc)
    return doc


def test_repository_rejects_illegal_transition(container: Container) -> None:
    _doc(container)
    with pytest.raises(IllegalTransition):
        container.repo.transition("d1", S.PENDING_UPLOAD, S.INDEXED)
    stored = container.repo.get("d1")
    assert stored is not None and stored.status is S.PENDING_UPLOAD


def test_repository_transition_is_compare_and_set(container: Container) -> None:
    _doc(container)
    assert container.repo.transition("d1", S.PENDING_UPLOAD, S.RECEIVED)
    # A second actor working from the stale state loses the race instead of double-applying.
    assert not container.repo.transition("d1", S.PENDING_UPLOAD, S.RECEIVED)
    assert container.repo.events("d1") == [(None, "PENDING_UPLOAD"), ("PENDING_UPLOAD", "RECEIVED")]


def test_status_cannot_be_changed_outside_transition(container: Container) -> None:
    _doc(container)
    with pytest.raises(ValueError, match="transition"):
        container.repo.update_fields("d1", status=S.INDEXED)


def test_lease_prevents_concurrent_workers(container: Container) -> None:
    _doc(container)
    assert container.repo.claim("d1", "worker-a", lease_s=60)
    assert not container.repo.claim("d1", "worker-b", lease_s=60)
    assert container.repo.claim("d1", "worker-a", lease_s=60)  # re-entrant for the holder
    container.repo.release("d1", "worker-a")
    assert container.repo.claim("d1", "worker-b", lease_s=60)


def test_expired_lease_can_be_taken_over(container: Container) -> None:
    _doc(container)
    assert container.repo.claim("d1", "worker-a", lease_s=-1)  # already expired
    assert container.repo.claim("d1", "worker-b", lease_s=60)
