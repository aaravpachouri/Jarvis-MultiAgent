from __future__ import annotations

import pytest

from agents.voice_agent.agent import VoiceAgent
from agents.voice_agent.model_client import OpenAICompatibleClient


@pytest.mark.asyncio
async def test_underspecified_mission_prompt_asks_for_goal_without_calling_model():
    class MustNotCallModel:
        configured = True

        def answer_chat(self, messages):
            raise AssertionError("an incomplete planning prompt should not call the model")

    agent = VoiceAgent(model_client=MustNotCallModel())
    task = type("Task", (), {"prompt": "plan a mission"})()

    response = await agent(task, object())

    assert "mission's objective" in response
    assert "deadline" in response
    assert "Nothing will be executed" in response


@pytest.mark.asyncio
async def test_native_sync_chat_client_receives_messages(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "test-key")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "test-model")
    client = OpenAICompatibleClient()
    client.configured = True
    seen = {}

    def fake_answer_chat(messages):
        seen["messages"] = messages
        return "Hello from the configured model."

    monkeypatch.setattr(client, "answer_chat", fake_answer_chat)
    agent = VoiceAgent(model_client=client)

    class Registry:
        def list(self, enabled_only=False):
            return []

    class Runtime:
        registry = Registry()

    class Context:
        runtime = Runtime()

    response = await agent._model_fallback("Hello JARVIS", Context())
    assert response == "Hello from the configured model."
    assert seen["messages"][-1] == {"role": "user", "content": "Hello JARVIS"}


@pytest.mark.asyncio
async def test_native_sync_source_synthesis_returns_answer(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "test-key")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "test-model")
    client = OpenAICompatibleClient()
    client.configured = True
    seen = {}

    def fake_answer_with_sources(messages):
        seen["messages"] = messages
        return {"answer": "FastAPI is a Python API framework [1].", "sources": []}

    monkeypatch.setattr(client, "answer_with_sources", fake_answer_with_sources)
    agent = VoiceAgent(model_client=client)
    source = {
        "title": "FastAPI docs",
        "url": "https://fastapi.tiangolo.com/",
        "snippet": "Framework documentation",
    }
    response = await agent._synthesize_search_response(
        "Search FastAPI",
        {"query": "FastAPI", "results": [source], "retrieved_at": "now"},
    )
    assert "FastAPI is a Python API framework" in response
    assert "https://fastapi.tiangolo.com/" in response
    assert "Snippet (unverified)" in seen["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_model_adapter_error_is_not_returned_as_successful_chat():
    class BrokenClient:
        configured = True

        async def answer_chat(self, **kwargs):
            raise TypeError("simulated adapter mismatch")

    agent = VoiceAgent(model_client=BrokenClient())

    class Registry:
        def list(self, enabled_only=False):
            return []

    class Runtime:
        registry = Registry()

    class Context:
        runtime = Runtime()

    with pytest.raises(RuntimeError, match="failed safely"):
        await agent._model_fallback("Build something", Context())
