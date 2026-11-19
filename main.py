from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from app.chat import router as chat_router
from app.config import get_settings
from app.llm import OllamaConnector


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.llm = OllamaConnector(settings)
    yield
    await app.state.llm.close()


app = FastAPI(title="UKN Chat API", lifespan=lifespan)
app.include_router(chat_router)


class HealthResponse(BaseModel):
    status: str
    ollama_missing_models: list[str]


@app.get("/health")
async def health() -> HealthResponse:
    llm: OllamaConnector = app.state.llm
    return HealthResponse(
        status="ok",
        ollama_missing_models=await llm.missing_models(),
    )
