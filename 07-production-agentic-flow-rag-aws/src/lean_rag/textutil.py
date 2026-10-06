"""Small deterministic text helpers shared by chunking, local models and validation."""

from __future__ import annotations

import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")

STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how i if in into is it
    its me my no not of on or our please should so than that the their them then there these they this
    to tell us was we were what when where which who why will with would you your about any all also
    give show list find explain describe much many more most""".split()
)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def content_terms(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOPWORDS and len(t) > 1]


def stem(term: str) -> str:
    """Tiny suffix stripper - enough to match 'refunds'/'refund' without a dependency."""
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if term.endswith(suffix) and len(term) - len(suffix) >= 3:
            return term[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return term


def stemmed_terms(text: str) -> set[str]:
    return {stem(t) for t in content_terms(text)}


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]


def top_keywords(text: str, n: int = 8) -> list[str]:
    counts = Counter(t for t in content_terms(text) if not t.isdigit())
    return [w for w, _ in counts.most_common(n)]


def coverage(query: str, text: str) -> float:
    """Fraction of the query's content terms present in ``text`` (stemmed)."""
    q = stemmed_terms(query)
    if not q:
        return 0.0
    return len(q & stemmed_terms(text)) / len(q)
