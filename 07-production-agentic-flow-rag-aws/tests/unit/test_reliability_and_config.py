from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from lean_rag.config import Settings
from lean_rag.reliability import Budget, BudgetExceeded, TransientError, is_retryable, with_retries


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "Op")


def test_retries_throttling_then_succeeds() -> None:
    calls: list[int] = []
    delays: list[float] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise _client_error("ThrottlingException")
        return "ok"

    assert with_retries(flaky, op="t", max_retries=4, base_delay=1.0, sleep=delays.append) == "ok"
    assert len(calls) == 3
    assert len(delays) == 2
    assert 0.5 <= delays[0] <= 1.0 and 1.0 <= delays[1] <= 2.0  # exponential with jitter


def test_retries_are_bounded() -> None:
    calls: list[int] = []

    def always_throttled() -> None:
        calls.append(1)
        raise TimeoutError("slow")

    with pytest.raises(TransientError, match="after 3 attempts"):
        with_retries(always_throttled, op="t", max_retries=2, sleep=lambda _s: None)
    assert len(calls) == 3


def test_non_retryable_errors_are_raised_immediately() -> None:
    calls: list[int] = []

    def bad() -> None:
        calls.append(1)
        raise _client_error("ValidationException")

    with pytest.raises(ClientError):
        with_retries(bad, op="t", max_retries=5, sleep=lambda _s: None)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_client_error("ThrottlingException"), True),
        (_client_error("AccessDeniedException"), False),
        (TimeoutError(), True),
        (ValueError(), False),
    ],
)
def test_retryable_classification(exc: Exception, expected: bool) -> None:
    assert is_retryable(exc) is expected


def test_budget_caps_calls_and_tokens() -> None:
    budget = Budget(max_calls=2, max_tokens=100)
    budget.check()
    budget.record("m", 30, 20, 0.01)
    budget.check()
    budget.record("m", 40, 20, 0.01)
    with pytest.raises(BudgetExceeded):
        budget.check()
    assert budget.tokens == 110 and budget.cost_usd == pytest.approx(0.02)


def _base(**kw: object) -> dict[str, object]:
    return {"local_jwt_secret": "s" * 32, "local_upload_secret": "u" * 32, "_env_file": None, **kw}


def test_production_refuses_local_backends() -> None:
    with pytest.raises(ValueError, match="production cannot use local backends"):
        Settings(**_base(environment="prod"))  # type: ignore[arg-type]


def test_production_config_accepted_with_aws_backends() -> None:
    s = Settings(
        environment="prod",
        object_store="s3",
        queue="sqs",
        search="opensearch",
        models="bedrock",
        auth_mode="cognito",
        ingestion_queue_url="https://sqs/q",
        cognito_user_pool_id="eu-west-2_x",
        _env_file=None,  # type: ignore[call-arg]
    )
    assert s.cognito_issuer == "https://cognito-idp.eu-west-2.amazonaws.com/eu-west-2_x"


def test_local_auth_requires_secret() -> None:
    with pytest.raises(ValueError, match="RAG_LOCAL_JWT_SECRET"):
        Settings(local_upload_secret="u" * 32, _env_file=None)  # type: ignore[call-arg]


def test_limits_can_be_overridden_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_LIMITS__MAX_RETRIES", "7")
    monkeypatch.setenv("RAG_LIMITS__GENERATION_ATTEMPTS", "3")
    s = Settings(**_base())  # type: ignore[arg-type]
    assert s.limits.max_retries == 7 and s.limits.generation_attempts == 3


def test_database_url_built_from_parts_with_escaped_password() -> None:
    s = Settings(**_base(db_host="db.internal", db_user="rag", db_password="p@ss:w/rd"))  # type: ignore[arg-type]
    assert s.database_url == "postgresql+psycopg://rag:p%40ss%3Aw%2Frd@db.internal:5432/rag?sslmode=require"


def test_emf_publishes_aggregate_and_detailed_dimensions(capsys: pytest.CaptureFixture[str]) -> None:
    import json

    from lean_rag.observability.metrics import Metrics

    Metrics("svc").put("AgentCalls", 1, Agent="retrieval")
    record = json.loads(capsys.readouterr().out)
    assert record["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["Service"], ["Service", "Agent"]]
    assert record["AgentCalls"] == 1 and record["Agent"] == "retrieval"
