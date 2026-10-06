from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from lean_rag.config import Limits, Settings
from lean_rag.container import Container, build_container
from lean_rag.llm.base import LLMClient

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        local_data_dir=str(tmp_path / "data"),
        local_jwt_secret="test-jwt-secret-0123456789abcdef",
        local_upload_secret="test-upload-secret-0123456789abc",
        sqs_visibility_timeout_s=0,
        embedding_dimensions=384,
        limits=Limits(retry_base_delay_s=0.0, retry_max_delay_s=0.0),
        _env_file=None,  # never pick up a developer's .env
    )


@pytest.fixture
def container(settings: Settings) -> Container:
    return build_container(settings, emit_metrics=False)


@pytest.fixture
def llm_container(settings: Settings) -> Callable[[LLMClient], Container]:
    """Container whose agents use the LLM path with a scripted client."""

    def make(client: LLMClient) -> Container:
        return build_container(settings, llm_client=client, emit_metrics=False)

    return make
