"""LLM chat-model factory.

Selects the LLM provider based on the application settings.

The rest of the application calls `get_chat_model()` instead of
creating provider-specific chat models directly.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import settings


# Cache the chat model so it is created only once for each temperature.
@lru_cache
def get_chat_model(temperature: float = 0.2) -> BaseChatModel:
    provider = settings.model_provider.lower()

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.model_name,
            api_key=settings.openai_api_key,
            temperature=temperature,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.model_name,
            api_key=settings.anthropic_api_key,
            temperature=temperature,
        )

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.model_name,
            google_api_key=settings.google_api_key,
            temperature=temperature,
        )

    raise ValueError(f"Unsupported MODEL_PROVIDER: {settings.model_provider!r}")
