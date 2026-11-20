"""FastAPI application entry point for the UKZN chat API.

Run with ``fastapi dev main.py`` (development) or ``fastapi run main.py``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from app.chat import router as chat_router
from app.config import get_settings
from app.db import create_pool
from app.llm import OllamaConnector


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared clients at startup and close them at shutdown.

    Stores an ``OllamaConnector`` in ``app.state.llm`` and an open PostgreSQL
    pool in ``app.state.pool``. Startup fails if settings are invalid or the
    database cannot be reached.
    """

    settings = get_settings()
    app.state.llm = OllamaConnector(settings)
    app.state.pool = create_pool(settings)
    await app.state.pool.open()
    yield
    await app.state.pool.close()
    await app.state.llm.close()


app = FastAPI(title="UKN Chat API", lifespan=lifespan)
app.include_router(chat_router)


class HealthResponse(BaseModel):
    """Body returned by ``GET /health``.

    Attributes:
        status: Always ``"ok"`` when the API is serving requests.
        ollama_missing_models: Configured models not installed in Ollama.
    """

    status: str
    ollama_missing_models: list[str]


@app.get("/health")
async def health() -> HealthResponse:
    """Report API status and any configured Ollama models that are not installed.

    Raises:
        httpx.ConnectError: If Ollama cannot be reached.
    """

    llm: OllamaConnector = app.state.llm
    return HealthResponse(
        status="ok",
        ollama_missing_models=await llm.missing_models(),
    )
