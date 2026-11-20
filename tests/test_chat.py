from collections.abc import AsyncIterator, Sequence

import pytest
from fastapi.testclient import TestClient
from ollama import Message

import app.chat
from app.chat import GREETING_REPLY, MAX_SOURCE_DISTANCE, NO_MATCH_REPLY, THANKS_REPLY, small_talk_reply
from app.db import get_conn
from app.knowledge import SearchResult
from app.llm import get_llm
from main import app as fastapi_app


class FakeLLM:
    def __init__(self) -> None:
        self.called = False
        self.system_prompt = ""

    async def stream_chat(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        self.called = True
        self.system_prompt = messages[0].content or ""
        yield "UKZN was formed in 2004."


def result(url: str, distance: float) -> SearchResult:
    return SearchResult(title="t", url=url, content="c", distance=distance)


def post_chat(monkeypatch: pytest.MonkeyPatch, results: list[SearchResult]) -> tuple[str, FakeLLM]:
    llm = FakeLLM()

    async def fake_retrieve(conn: object, llm: object, question: str) -> list[SearchResult]:
        return results

    async def fake_conn() -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(app.chat, "retrieve", fake_retrieve)
    fastapi_app.dependency_overrides = {get_llm: lambda: llm, get_conn: fake_conn}
    try:
        # No context manager: the lifespan (DB pool, Ollama) is not started.
        response = TestClient(fastapi_app).post("/chat", json={"messages": [{"role": "user", "content": "q"}]})
    finally:
        fastapi_app.dependency_overrides = {}
    assert response.status_code == 200
    return response.text, llm


def test_off_topic_question_gets_fixed_reply_without_model(monkeypatch: pytest.MonkeyPatch) -> None:
    text, llm = post_chat(monkeypatch, [result("https://ukzn.ac.za/a/", MAX_SOURCE_DISTANCE + 0.01)])
    assert text == NO_MATCH_REPLY
    assert not llm.called


def test_answer_is_model_text_only_and_prompt_has_only_relevant_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [
        result("https://ukzn.ac.za/history/", 0.1),
        result("https://ukzn.ac.za/far/", MAX_SOURCE_DISTANCE + 0.01),
    ]
    text, llm = post_chat(monkeypatch, results)
    assert text == "UKZN was formed in 2004."
    assert "https://ukzn.ac.za/history/" in llm.system_prompt
    assert "https://ukzn.ac.za/far/" not in llm.system_prompt


@pytest.mark.parametrize("message", ["Hi", "hi!", "Hello there", "hey", "Good morning", "Sawubona", "HELLO UKZN"])
def test_greeting_gets_welcome_without_retrieval_or_model(monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    async def fail_retrieve(conn: object, llm: object, question: str) -> list[SearchResult]:
        raise AssertionError("retrieval should not run for greetings")

    llm = FakeLLM()

    async def fake_conn() -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(app.chat, "retrieve", fail_retrieve)
    fastapi_app.dependency_overrides = {get_llm: lambda: llm, get_conn: fake_conn}
    try:
        response = TestClient(fastapi_app).post("/chat", json={"messages": [{"role": "user", "content": message}]})
    finally:
        fastapi_app.dependency_overrides = {}
    assert response.text == GREETING_REPLY
    assert not llm.called


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Thanks!", THANKS_REPLY),
        ("thank you very much", THANKS_REPLY),
        ("Ngiyabonga", THANKS_REPLY),
        ("hi, who is the vice-chancellor?", None),
        ("history of UKZN", None),
        ("thanks, and where is Westville campus?", None),
    ],
)
def test_small_talk_matches_whole_message_only(message: str, expected: str | None) -> None:
    assert small_talk_reply(message) == expected
