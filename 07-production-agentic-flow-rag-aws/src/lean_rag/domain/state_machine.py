"""Deterministic document lifecycle.

The ingestion supervisor may only move a document along these edges. Anything else raises
``IllegalTransition`` - there is no LLM anywhere in this decision.
"""

from __future__ import annotations

from lean_rag.domain.models import DocStatus as S

TERMINAL: frozenset[S] = frozenset({S.INDEXED, S.NEEDS_REVIEW, S.DUPLICATE, S.FAILED, S.DELETED})
IN_PROGRESS: frozenset[S] = frozenset({S.PARSING, S.CHUNKING, S.EMBEDDING, S.INDEXING, S.VERIFYING})

# A new version may be uploaded for any settled document; previous chunks stay live until
# the new version is indexed, then stale chunks are removed.
_NEW_VERSION = S.PENDING_UPLOAD

TRANSITIONS: dict[S, frozenset[S]] = {
    S.PENDING_UPLOAD: frozenset({S.RECEIVED, S.FAILED, S.DELETED}),
    S.RECEIVED: frozenset({S.PARSING, S.DUPLICATE, S.FAILED, S.DELETED}),
    S.PARSING: frozenset({S.CHUNKING, S.FAILED}),
    S.CHUNKING: frozenset({S.EMBEDDING, S.FAILED}),
    S.EMBEDDING: frozenset({S.INDEXING, S.FAILED}),
    S.INDEXING: frozenset({S.VERIFYING, S.FAILED}),
    # VERIFYING -> CHUNKING is the bounded re-chunk path chosen when probes fail.
    S.VERIFYING: frozenset({S.INDEXED, S.NEEDS_REVIEW, S.CHUNKING, S.FAILED}),
    S.INDEXED: frozenset({_NEW_VERSION, S.DELETED}),
    S.NEEDS_REVIEW: frozenset({_NEW_VERSION, S.RECEIVED, S.DELETED}),
    S.DUPLICATE: frozenset({_NEW_VERSION, S.DELETED}),
    # FAILED -> RECEIVED is an operator-initiated reprocess (e.g. after a DLQ redrive).
    S.FAILED: frozenset({_NEW_VERSION, S.RECEIVED, S.DELETED}),
    S.DELETED: frozenset(),
}


class IllegalTransition(Exception):
    def __init__(self, current: S, target: S) -> None:
        super().__init__(f"illegal transition {current} -> {target}")
        self.current = current
        self.target = target


def can_transition(current: S, target: S) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: S, target: S) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(current, target)
