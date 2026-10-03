"""Centralised application configuration.

All settings come from environment variables or the .env file.
Other modules should import `settings` from here instead of reading
environment variables directly.

             .env
              |
              v
        Settings class
              |
          settings object
              |
     ---------------------
     |        |          |
 service   database   auth
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Load values from .env file
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application settings ---
    environment: str = "development"
    app_name: str = "fastapi-langgraph-agent"
    log_level: str = "INFO"

    # --- LLM settings ---
    model_provider: str = "openai"  # openai | anthropic | google
    model_name: str = "gpt-4.1"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None

    # --- Authentication settings ---
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    api_key: str

    # --- Database and cache settings ---
    redis_url: str = "redis://localhost:6379/0"
    postgres_url: str = "postgresql+asyncpg://user:password@localhost:5432/llmdb"

    # --- API rate limit ---
    rate_limit_per_minute: int = 30

    # --- CORS - Frontend URLs allowed to call this API ---
    cors_allowed_origins: str = "http://localhost:3000"

    # --- LangChain  Observability settings ---
    langchain_tracing_v2: bool = False
    langchain_api_key: str | None = None
    langchain_project: str = "enterprise-agent"

    @property
    def cors_origins_list(self) -> list[str]:
        # Convert comma-separated URLs into a list.
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor - Settings() is only constructed once."""
    return Settings()


settings = get_settings()
