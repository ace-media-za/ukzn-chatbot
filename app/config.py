"""Environment-backed configuration for the direct Ollama chat service."""

from functools import lru_cache

from pydantic import HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from ``.env`` and environment variables.

    Environment variables take precedence over values in ``.env``. Unknown
    values are ignored, so legacy database variables are harmless.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_host: HttpUrl = HttpUrl("http://localhost:11434")
    ollama_chat_model: str = "qwen2.5:1.5b"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached application settings."""

    return Settings()
