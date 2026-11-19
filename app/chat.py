"""HTTP endpoint and request models for streaming direct Ollama chats.

The ``/chat`` endpoint receives the full client-managed conversation history and
returns a plain-text streaming response.
"""

from collections.abc import AsyncIterator
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from ollama import Message, ResponseError
from pydantic import BaseModel, Field

from app.llm import OllamaConnector, get_llm

router = APIRouter()

SYSTEM_PROMPT = """You are a helpful assistant for the University of KwaZulu-Natal.
Answer clearly and concisely."""


class ChatMessage(BaseModel):
    """One message in a client-managed conversation.

    ``system`` is deliberately excluded so clients cannot override the server
    prompt. Send earlier assistant responses back with the next request to
    preserve conversational context.
    """

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    """Payload accepted by :http:post:`/chat`.

    At least one item must have ``role="user"``. The last user message starts
    the next model response.
    """

    messages: list[ChatMessage] = Field(min_length=1, max_length=50)


async def _prepend(first: str, rest: AsyncIterator[str]) -> AsyncIterator[str]:
    """Yield an already-read chunk followed by the remainder of a stream."""

    yield first
    async for chunk in rest:
        yield chunk


@router.post("/chat", response_class=StreamingResponse)
async def chat(
    body: ChatRequest,
    llm: Annotated[OllamaConnector, Depends(get_llm)],
) -> StreamingResponse:
    """Stream an Ollama response for a conversation.

    The first model chunk is read before response headers are sent. This lets
    unavailable Ollama instances become an HTTP 502 instead of a partially
    completed streaming response.

    Args:
        body: Conversation messages supplied by the client.
        llm: Application-scoped Ollama connector injected by FastAPI.

    Raises:
        HTTPException: A 422 error when no user message is present, or a 502
            error when Ollama cannot be reached.
    """

    question = next((m.content for m in reversed(body.messages) if m.role == "user"), None)
    if question is None:
        raise HTTPException(status_code=422, detail="At least one user message is required")
    # The first chunk runs before headers are sent, so LLM failures become a proper 502.
    try:
        messages = [
            Message(role="system", content=SYSTEM_PROMPT),
            *(Message(role=m.role, content=m.content) for m in body.messages),
        ]
        stream = llm.stream_chat(messages)
        first = await anext(stream, "")
    # The streaming path surfaces raw httpx errors instead of ollama's ConnectionError
    except (ConnectionError, httpx.HTTPError, ResponseError) as exc:
        raise HTTPException(status_code=502, detail="LLM unavailable") from exc
    return StreamingResponse(_prepend(first, stream), media_type="text/plain; charset=utf-8")
