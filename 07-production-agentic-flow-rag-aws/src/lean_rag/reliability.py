"""Error taxonomy, bounded retries and per-run budgets."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError

from lean_rag.observability.logging import log_event

logger = logging.getLogger(__name__)


class TransientError(Exception):
    """Retry later: throttling, timeouts, unavailable dependencies. SQS will redeliver."""


class PermanentError(Exception):
    """Retrying cannot help: malformed or unsupported input. The document is marked FAILED."""


class BudgetExceeded(Exception):
    """A run hit its LLM-call or token ceiling."""


_RETRYABLE_AWS_CODES = {
    "ThrottlingException",
    "Throttling",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "ModelNotReadyException",
    "InternalServerException",
    "ProvisionedThroughputExceededException",
    "RequestTimeout",
    "SlowDown",
}


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, TransientError | TimeoutError | ConnectTimeoutError | ReadTimeoutError):
        return True
    if isinstance(exc, EndpointConnectionError):
        return True
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code") in _RETRYABLE_AWS_CODES
    status = getattr(exc, "status_code", None)  # opensearch-py TransportError
    return isinstance(status, int) and status in (429, 502, 503, 504)


def with_retries[T](
    fn: Callable[[], T],
    *,
    op: str,
    max_retries: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``fn`` with capped exponential backoff + full jitter on retryable errors only.

    After ``max_retries`` retries the last retryable error is re-raised as ``TransientError``
    so callers (the worker) can leave the message for SQS redelivery / DLQ.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            if not is_retryable(exc):
                raise
            if attempt >= max_retries:
                raise TransientError(f"{op} failed after {attempt + 1} attempts: {exc}") from exc
            delay = min(max_delay, base_delay * (2**attempt)) * random.uniform(0.5, 1.0)
            log_event(
                logger, "retrying", logging.WARNING, op=op, attempt=attempt + 1, delay_s=round(delay, 3)
            )
            sleep(delay)
            attempt += 1


@dataclass
class Budget:
    """Caps LLM calls and tokens for one query or one ingestion attempt."""

    max_calls: int
    max_tokens: int
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    by_model: dict[str, int] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def check(self) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"LLM call budget exhausted ({self.max_calls})")
        if self.tokens >= self.max_tokens:
            raise BudgetExceeded(f"token budget exhausted ({self.max_tokens})")

    def record(self, model: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd += cost_usd
        self.by_model[model] = self.by_model.get(model, 0) + 1
