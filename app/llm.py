"""Small asynchronous client wrapper around the local Ollama chat API."""

from collections.abc import AsyncIterator, Sequence

from fastapi import Request
from ollama import AsyncClient, Message

from app.config import Settings


class OllamaConnector:
    """Manage one reusable asynchronous connection to the configured Ollama host.

    Instances are created during FastAPI startup and closed during shutdown.
    """

    def __init__(self, settings: Settings) -> None:
        """Create a connector using the host and chat model in ``settings``."""

        self._client = AsyncClient(host=str(settings.ollama_host))
        self._chat_model = settings.ollama_chat_model

    async def stream_chat(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        """Yield non-empty text chunks from Ollama's streaming chat response.

        Args:
            messages: System and conversation messages in Ollama's message
                format.
        """

        stream = await self._client.chat(model=self._chat_model, messages=messages, stream=True)
        async for chunk in stream:
            if chunk.message.content:
                yield chunk.message.content

    async def missing_models(self) -> list[str]:
        """Return configured model names that are not installed in Ollama."""

        installed = {m.model for m in (await self._client.list()).models}
        required = {self._chat_model}
        # Ollama reports untagged names as ":latest"
        return sorted(m for m in required if m not in installed and f"{m}:latest" not in installed)

    async def close(self) -> None:
        """Close the underlying HTTP client during application shutdown."""

        await self._client.close()


def get_llm(request: Request) -> OllamaConnector:
    """FastAPI dependency that returns the application-scoped Ollama connector."""

    return request.app.state.llm
