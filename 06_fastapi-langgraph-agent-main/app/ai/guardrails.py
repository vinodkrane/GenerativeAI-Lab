"""Simple input and output guardrails.

These checks help control what goes into and comes out of the LLM pipeline.

Input guardrails:
- Check user messages before sending them to the LLM.
- Block unsafe or suspicious requests.

Output guardrails:
- Check LLM responses before returning them to users.
- Remove sensitive information like emails or credit card numbers.

In production, these rules can be replaced or extended with:
- moderation APIs
- PII detection models
- policy checking services
"""

from __future__ import annotations

import re

from app.core.exceptions import InputGuardrailError, OutputGuardrailError


# Maximum size allowed for a user message.
_MAX_INPUT_CHARS = 8000

# Patterns to detect common prompt injection attempts.
# Example: "ignore previous instructions"
_PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore (all|any) (previous|prior) instructions", re.IGNORECASE),
    re.compile(r"disregard (your|the) (system|previous) prompt", re.IGNORECASE),
    re.compile(r"reveal (your|the) system prompt", re.IGNORECASE),
]

# Patterns to block unsafe requests.
_BLOCKED_TOPICS_PATTERNS = [
    re.compile(
        r"\bhow to (make|build|synthesi[sz]e)\b.*\b(bomb|explosive|nerve agent)\b",
        re.IGNORECASE,
    ),
]

# Simple PII detection rules.
# Used before returning LLM output to hide sensitive information.
_PII_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
}


# Check user input before sending it to the LLM.
def check_input(message: str) -> None:
    """Raise InputGuardrailError if the user message violates policy."""
    if len(message) > _MAX_INPUT_CHARS:
        raise InputGuardrailError(
            "Message exceeds maximum allowed length.",
            details={"max_chars": _MAX_INPUT_CHARS, "actual_chars": len(message)},
        )

    for pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(message):
            raise InputGuardrailError(
                "Message appears to attempt to override system instructions.",
                details={"rule": pattern.pattern},
            )

    for pattern in _BLOCKED_TOPICS_PATTERNS:
        if pattern.search(message):
            raise InputGuardrailError(
                "Message requests disallowed content.",
                details={"rule": pattern.pattern},
            )


# Remove sensitive information from text.
def redact_pii(text: str) -> str:
    redacted = text
    for label, pattern in _PII_PATTERNS.items():
        redacted = pattern.sub(f"[REDACTED_{label.upper()}]", redacted)
    return redacted


# Validate and clean the LLM response.
def check_output(message: str) -> str:
    """Validate + sanitize the LLM's final response.

    Returns the (possibly redacted) message on success, or raises
    OutputGuardrailError if the response cannot be safely returned at all.
    """
    if not message or not message.strip():
        raise OutputGuardrailError("Model produced an empty response.")

    for pattern in _BLOCKED_TOPICS_PATTERNS:
        if pattern.search(message):
            raise OutputGuardrailError(
                "Generated response violates content policy.",
                details={"rule": pattern.pattern},
            )

    return redact_pii(message)
