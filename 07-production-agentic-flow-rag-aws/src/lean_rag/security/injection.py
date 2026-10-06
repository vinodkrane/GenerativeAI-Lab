"""Prompt-injection handling for untrusted document text.

The primary defence is structural: document text is only ever placed inside delimited data
blocks, agents have no tools that act on the world, every agent output is schema-validated,
and authorization is outside the model's reach. Detection here is a secondary signal: suspect
chunks are flagged in metadata and metrics, not silently dropped (dropping would let an
attacker suppress legitimate content).
"""

from __future__ import annotations

import re

_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts|rules)",
    r"disregard (all |any )?(the )?(previous|prior|above|system) ",
    r"(reveal|print|show|repeat|output) (your|the) (system )?(prompt|instructions)",
    r"you are now (a|an|in) ",
    r"\bsystem prompt\b",
    r"\b(call|invoke|use|run) (the |an? )?(admin|administrative|delete|tool|function)",
    r"\bdelete (this|the|all) (document|documents|index|data)",
    r"\b(other|another) (tenant|user)'?s? (documents?|data)",
    r"</?(system|instructions|untrusted_document|evidence)\b",
]
_INJECTION = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.IGNORECASE)

# Tags we use to fence untrusted text. Any occurrence inside the text is neutralised so a
# document cannot "close" its own data block and start writing instructions.
_FENCE_TAG = re.compile(r"<(/?)(untrusted_document|evidence|system|instructions)\b", re.IGNORECASE)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


def neutralise(text: str) -> str:
    return _FENCE_TAG.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}", text)
