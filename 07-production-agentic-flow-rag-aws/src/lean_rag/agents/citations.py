"""Deterministic answer validation. Runs on every generated answer, LLM or heuristic."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lean_rag.agents.prompts import PROMPT_CANARY
from lean_rag.domain.models import Chunk
from lean_rag.textutil import content_terms, coverage

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
MIN_SUPPORT = 0.5  # share of a sentence's content terms that must appear in its cited evidence


@dataclass(frozen=True)
class Sentence:
    text: str
    citations: tuple[str, ...]


@dataclass
class ValidationResult:
    ok: bool
    problems: list[str] = field(default_factory=list)
    groundedness: float = 0.0  # fraction of sentences supported by their citations


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def validate_answer(
    sentences: list[Sentence], evidence: dict[str, Chunk], min_support: float = MIN_SUPPORT
) -> ValidationResult:
    if not sentences:
        return ValidationResult(False, ["answer has no sentences"])
    problems: list[str] = []
    supported = 0
    for i, s in enumerate(sentences, start=1):
        if PROMPT_CANARY in s.text:
            problems.append(f"sentence {i} leaks internal instructions")
            continue
        if not s.citations:
            problems.append(f"sentence {i} has no citation")
            continue
        unknown = [c for c in s.citations if c not in evidence]
        if unknown:
            problems.append(f"sentence {i} cites unknown evidence {unknown}")
            continue
        cited_text = "\n".join(evidence[c].text for c in s.citations)
        if content_terms(s.text) and coverage(s.text, cited_text) < min_support:
            problems.append(f"sentence {i} is not supported by its cited evidence")
            continue
        missing_numbers = _numbers(s.text) - _numbers(cited_text)
        if missing_numbers:
            problems.append(f"sentence {i} contains numbers not in evidence: {sorted(missing_numbers)}")
            continue
        supported += 1
    return ValidationResult(not problems, problems, supported / len(sentences))
