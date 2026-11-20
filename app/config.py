"""Environment-backed configuration for the API and the ingestion scripts."""

from functools import lru_cache

from pydantic import HttpUrl, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from ``.env`` and environment variables.

    Environment variables take precedence over values in ``.env``; names are
    case-insensitive (``DATABASE_URL`` maps to ``database_url``). Unknown
    variables are ignored. Values are validated at startup, so a missing or
    malformed ``DATABASE_URL`` fails fast instead of on the first request.

    Attributes:
        ollama_host: Base URL of the Ollama server.
        ollama_chat_model: Ollama model that generates chat answers.
        ollama_embed_model: Ollama model that embeds questions and documents.
            It must produce 768-dimension vectors to match ``sql/schema.sql``
            and must be the same model the tables were ingested with.
        database_url: PostgreSQL DSN for the database holding the pgvector
            knowledge-base tables.
        db_pool_min_size: Connections the API pool keeps open when idle.
        db_pool_max_size: Upper bound on concurrent pooled connections, and
            therefore on concurrent ``/chat`` retrievals.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_host: HttpUrl = HttpUrl("http://localhost:11434")
    ollama_chat_model: str = "qwen2.5:1.5b"
    ollama_embed_model: str = "nomic-embed-text"
    database_url: PostgresDsn
    db_pool_min_size: int = 1
    db_pool_max_size: int = 5


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide application settings.

    The result is cached, so ``.env`` is read once per process; restart the
    server after changing configuration.

    Raises:
        pydantic.ValidationError: If a required setting is missing or invalid.
    """

    return Settings()
