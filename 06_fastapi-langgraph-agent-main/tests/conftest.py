import pytest


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """Point the app at safe defaults so importing settings never fails
    just because a real .env with production secrets isn't present."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
