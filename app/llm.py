"""Asynchronous connector to Ollama for chat generation and text embeddings."""

from collections.abc import AsyncIterator, Sequence

from fastapi import Request
from ollama import AsyncClient, Message

from app.config import Settings


class OllamaConnector:
    """Manage one reusable asynchronous connection to the configured Ollama host.

    The API creates one instance during FastAPI startup and closes it during
    shutdown; ``scripts/ingest.py`` creates its own for the duration of a run.
    """

    def __init__(self, settings: Settings) -> None:
        """Create a connector for the host, chat model and embedding model in ``settings``.

        Args:
            settings: Application settings providing the Ollama configuration.
        """

        self._client = AsyncClient(host=str(settings.ollama_host))
        self._chat_model = settings.ollama_chat_model
        self._embed_model = settings.ollama_embed_model

    async def stream_chat(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        """Yield non-empty text chunks from Ollama's streaming chat response.

        Args:
            messages: System and conversation messages in Ollama's message
                format.

        Yields:
            Text fragments in generation order.

        Raises:
            httpx.HTTPError: If Ollama cannot be reached mid-stream.
            ollama.ResponseError: If Ollama rejects the request, for example
                because the model is not installed.
        """

        stream = await self._client.chat(model=self._chat_model, messages=messages, stream=True)
        async for chunk in stream:
            if chunk.message.content:
                yield chunk.message.content

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts with the configured embedding model.

        Callers add the ``nomic-embed-text`` task prefix themselves
        (``search_query: `` or ``search_document: ``, see ``app.knowledge``).

        Args:
            texts: Texts to embed in one request.

        Returns:
            One embedding vector per input text, in input order.

        Raises:
            ConnectionError: If Ollama cannot be reached.
            ollama.ResponseError: If Ollama rejects the request.
        """

        response = await self._client.embed(model=self._embed_model, input=list(texts))
        return [list(vector) for vector in response.embeddings]

    async def missing_models(self) -> list[str]:
        """List configured chat and embedding models that Ollama does not have installed.

        Returns:
            Sorted model names; empty when everything is installed.
        """

        installed = {m.model for m in (await self._client.list()).models}
        required = {self._chat_model, self._embed_model}
        # Ollama reports untagged names as ":latest"
        return sorted(m for m in required if m not in installed and f"{m}:latest" not in installed)

    async def close(self) -> None:
        """Close the underlying HTTP client during application shutdown."""

        await self._client.close()


def get_llm(request: Request) -> OllamaConnector:
    """FastAPI dependency that returns the application-scoped Ollama connector.

    Args:
        request: Current request, used to reach ``app.state``.
    """

    return request.app.state.llm
