import json

import pytest

from agents.voice_agent.model_client import ModelClientError, OpenAICompatibleClient


def configure_all(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.setenv("JARVIS_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "gemini-test")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "gemini-test-model")
    monkeypatch.setenv("JARVIS_LLM_SECONDARY_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("JARVIS_LLM_SECONDARY_API_KEY", "groq-test")
    monkeypatch.setenv("JARVIS_LLM_SECONDARY_MODEL", "groq-test-model")
    monkeypatch.setenv("JARVIS_LLM_FALLBACK_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("JARVIS_LLM_FALLBACK_API_KEY", "")
    monkeypatch.setenv("JARVIS_LLM_FALLBACK_MODEL", "llama3.2:latest")


def test_provider_order_is_gemini_groq_ollama(monkeypatch):
    configure_all(monkeypatch)
    client = OpenAICompatibleClient()
    assert [p.name for p in client.providers] == ["gemini", "groq", "ollama"]


def test_groq_skipped_when_key_or_model_missing(monkeypatch):
    configure_all(monkeypatch)
    monkeypatch.setenv("JARVIS_LLM_SECONDARY_API_KEY", "")
    client = OpenAICompatibleClient()
    assert [p.name for p in client.providers] == ["gemini", "ollama"]


def test_fails_over_in_order(monkeypatch):
    configure_all(monkeypatch)
    client = OpenAICompatibleClient()
    calls = []

    def fake(provider, messages, *, json_mode):
        calls.append(provider.name)
        if provider.name != "ollama":
            raise ModelClientError("simulated provider failure")
        return '{"ok": true}' if json_mode else "local response"

    monkeypatch.setattr(client, "_request_provider", fake)
    assert client.answer_chat([{"role": "user", "content": "hi"}]) == "local response"
    assert calls == ["gemini", "groq", "ollama"]
    assert client.active_provider == "ollama"


def test_decision_invalid_json_fails_over(monkeypatch):
    configure_all(monkeypatch)
    client = OpenAICompatibleClient()
    calls = []

    def fake(provider, messages, *, json_mode):
        calls.append(provider.name)
        if provider.name == "gemini":
            return "not json"
        return json.dumps({"route": "chat"})

    monkeypatch.setattr(client, "_request_provider", fake)
    assert client.decide([{"role": "user", "content": "hi"}]) == {"route": "chat"}
    assert calls == ["gemini", "groq"]
    assert client.active_provider == "groq"


def test_render_skips_localhost_ollama_when_cloud_credentials_are_missing(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "")
    monkeypatch.setenv("JARVIS_LLM_SECONDARY_API_KEY", "")
    monkeypatch.setenv("JARVIS_LLM_SECONDARY_MODEL", "")
    monkeypatch.setenv("JARVIS_LLM_FALLBACK_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("JARVIS_LLM_FALLBACK_MODEL", "llama3.2:latest")

    client = OpenAICompatibleClient()

    assert client.providers == []
    with pytest.raises(ModelClientError, match="No cloud model provider is configured for Render"):
        client.answer_chat([{"role": "user", "content": "plan a mission"}])


def test_raises_when_all_providers_fail(monkeypatch):
    configure_all(monkeypatch)
    client = OpenAICompatibleClient()

    def fake(provider, messages, *, json_mode):
        raise ModelClientError("simulated outage")

    monkeypatch.setattr(client, "_request_provider", fake)
    with pytest.raises(ModelClientError, match="All configured model providers failed"):
        client.answer_chat([{"role": "user", "content": "hi"}])
