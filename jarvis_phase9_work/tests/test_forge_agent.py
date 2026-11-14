from __future__ import annotations

import asyncio
import json

import pytest

from agents.coding_agent.agent import ForgeAgent, ForgeError, ForgeModel


class FakeModel:
    configured = True

    def __init__(self, response: str):
        self.response = response
        self.messages = None

    async def complete(self, messages):
        self.messages = messages
        return self.response


def test_forge_manifest_handler_is_executable():
    from agents.coding_agent.agent import create_handler
    assert isinstance(create_handler(), ForgeAgent)


def test_classifies_common_actions():
    assert ForgeAgent._classify("inspect the repository") == "inspect"
    assert ForgeAgent._classify("run the tests") == "test"
    assert ForgeAgent._classify("fix the broken parser") == "implement"
    assert ForgeAgent._classify("why does this traceback happen?") == "analyze"


def test_forge_model_uses_shared_provider_client(monkeypatch):
    from agents.voice_agent.model_client import OpenAICompatibleClient
    monkeypatch.setenv("JARVIS_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "test-key")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "test-model")
    monkeypatch.setenv("JARVIS_LLM_SECONDARY_API_KEY", "")
    monkeypatch.setenv("JARVIS_LLM_SECONDARY_MODEL", "")
    monkeypatch.setenv("JARVIS_LLM_FALLBACK_BASE_URL", "")
    client = OpenAICompatibleClient()
    model = ForgeModel(client=client)
    assert model.configured
    assert [p.name for p in client.providers] == ["gemini"]


def test_forge_model_not_configured_without_providers(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_BASE_URL", "")
    monkeypatch.setenv("JARVIS_LLM_API_KEY", "")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "")
    monkeypatch.setenv("JARVIS_LLM_SECONDARY_BASE_URL", "")
    monkeypatch.setenv("JARVIS_LLM_SECONDARY_API_KEY", "")
    monkeypatch.setenv("JARVIS_LLM_SECONDARY_MODEL", "")
    monkeypatch.setenv("JARVIS_LLM_FALLBACK_BASE_URL", "")
    monkeypatch.setenv("JARVIS_LLM_FALLBACK_MODEL", "")
    assert ForgeModel().configured is False


def test_parse_json_accepts_fenced_json():
    payload = ForgeAgent._parse_json('```json\n{"summary":"ok","files":[]}\n```')
    assert payload["summary"] == "ok"


def test_parse_json_rejects_invalid_content():
    with pytest.raises(ForgeError):
        ForgeAgent._parse_json("not json")


def test_workspace_report_never_lists_env(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=never-read", encoding="utf-8")
    report = ForgeAgent(workspace=tmp_path, model=FakeModel("{}"))._inspect_report("inspect")
    assert "main.py" in report
    assert ".env" not in report
    assert "never-read" not in report


def test_rejects_path_traversal_before_writing(tmp_path):
    model = FakeModel(json.dumps({"summary": "bad", "files": [{"path": "../outside.py", "content": "bad=True"}]}))
    with pytest.raises(ForgeError, match="Unsafe path"):
        asyncio.run(ForgeAgent(workspace=tmp_path, model=model)._implement("implement a file"))
    assert not (tmp_path.parent / "outside.py").exists()


def test_implementation_writes_file_and_keeps_backup(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("old = True\n", encoding="utf-8")
    model = FakeModel(json.dumps({"summary": "Updated app.", "files": [{"path": "app.py", "content": "new = True\n"}]}))
    agent = ForgeAgent(workspace=tmp_path, model=model)
    monkeypatch.setattr(agent, "_run_tests", lambda quiet=True: "Exit code: 0\n1 passed")
    result = asyncio.run(agent._implement("implement the new app"))
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "new = True\n"
    backups = list((tmp_path / ".jarvis_forge_backups").rglob("app.py"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "old = True\n"
    assert "Verification: PASSED" in result


def test_implementation_fails_when_tests_fail(tmp_path, monkeypatch):
    model = FakeModel(json.dumps({
        "summary": "Created app.",
        "files": [{"path": "app.py", "content": "x = 1\n"}],
    }))
    agent = ForgeAgent(workspace=tmp_path, model=model)
    monkeypatch.setattr(
        agent,
        "_run_tests",
        lambda quiet=True: "Verification: FAILED (exit code 1)\n1 failed",
    )

    with pytest.raises(ForgeError, match="verification did not pass"):
        asyncio.run(agent._implement("implement the app"))


def test_implementation_fails_when_verification_is_unknown(tmp_path, monkeypatch):
    model = FakeModel(json.dumps({
        "summary": "Created app.",
        "files": [{"path": "app.py", "content": "x = 1\n"}],
    }))
    agent = ForgeAgent(workspace=tmp_path, model=model)
    monkeypatch.setattr(
        agent,
        "_run_tests",
        lambda quiet=True: "Verification: RESULT UNKNOWN\nNo exit code",
    )

    with pytest.raises(ForgeError, match="verification did not pass"):
        asyncio.run(agent._implement("implement the app"))