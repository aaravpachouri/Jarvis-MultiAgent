"""Offline output-quality regression tests for Forge, Athena and Scribe.

These cover the no-API-key behaviour: agents must stay useful and honest
when no model provider is configured and no web material is available.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agents.coding_agent.agent import ForgeAgent
from agents.research_agent.agent import ResearchAgent
from agents.documentation_agent.agent import create_handler as scribe_handler
from core.contracts import TaskRequest


class UnconfiguredModel:
    configured = False

    async def complete(self, messages):  # pragma: no cover - must never be called
        raise AssertionError("Offline Forge analysis must not call a model provider")


def _run(coro):
    return asyncio.run(coro)


def test_forge_offline_analyze_returns_deterministic_report(tmp_path):
    (tmp_path / "router.py").write_text(
        "class Router:\n    def route(self, task):\n        return task\n",
        encoding="utf-8",
    )
    (tmp_path / "notes.txt").write_text("unrelated notes\n", encoding="utf-8")
    agent = ForgeAgent(workspace=tmp_path, model=UnconfiguredModel())
    report = _run(agent(SimpleNamespace(prompt="Explain how the router works", context={}), None))
    assert "OFFLINE WORKSPACE ANALYSIS" in report
    assert "no model provider is configured" in report
    assert "Router" in report and "line 1" in report
    assert "router.py" in report
    assert "Explain how the router works" in report


def test_forge_offline_report_never_reads_env_or_secret_files(tmp_path):
    (tmp_path / "app.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=never-read\n", encoding="utf-8")
    (tmp_path / "service_token.py").write_text("TOKEN = 'hidden'\n", encoding="utf-8")
    agent = ForgeAgent(workspace=tmp_path, model=UnconfiguredModel())
    report = _run(agent(SimpleNamespace(prompt="Inspect the repository", context={}), None))
    assert "app.py" in report
    assert "never-read" not in report
    assert "hidden" not in report
    assert "service_token.py" not in report


def test_forge_offline_report_is_bounded_and_honest(tmp_path):
    agent = ForgeAgent(workspace=tmp_path, model=UnconfiguredModel())
    report = _run(agent(SimpleNamespace(prompt="Review the workspace", context={}), None))
    assert len(report) <= ForgeAgent.MAX_REPORT_CHARS
    assert "extractive" in report
    assert "JARVIS_LLM_" in report


def test_athena_offline_analysis_ranks_relevant_statements():
    class NoSearchContext:
        async def delegate(self, **kwargs):  # pragma: no cover - must never be called
            raise AssertionError("Offline context analysis must not call Scout")

    task = SimpleNamespace(
        prompt="Summarize the deployment status",
        context={
            "source_material": [
                "The lunch menu changes weekly. Nothing about infrastructure here.",
                "The deployment is unverified because the pipeline lacks credentials.",
            ]
        },
    )
    result = _run(ResearchAgent()(task, NoSearchContext()))
    assert result["analysis_mode"] == "offline_context_analysis"
    assert result["key_statements"], "expected at least one ranked key statement"
    top = result["key_statements"][0]
    assert top["item"] == 2
    assert "deployment is unverified" in top["statement"]
    assert "Most relevant supplied statements" in result["summary"]


def test_athena_offline_analysis_without_keyword_overlap_has_no_key_statements():
    class NoSearchContext:
        async def delegate(self, **kwargs):  # pragma: no cover - must never be called
            raise AssertionError("Offline context analysis must not call Scout")

    task = SimpleNamespace(
        prompt="Summarize the zebra population",
        context={"source_material": ["Short note."]},
    )
    result = _run(ResearchAgent()(task, NoSearchContext()))
    assert result["analysis_mode"] == "offline_context_analysis"
    assert result["key_statements"] == []


def test_scribe_email_uses_topic_stated_in_prompt():
    output = _run(scribe_handler()(
        TaskRequest(prompt="Draft an email to the facilities team about the server-room cooling outage"),
        None,
    ))
    assert output["document_type"] == "email"
    assert "I'm reaching out regarding the server-room cooling outage." in output["content"]
    assert "[topic/purpose]" not in output["content"]
    assert "key_message_details" in output["missing_context"]
    assert output["draft_only"] is True


def test_scribe_email_without_topic_keeps_clear_placeholders():
    output = _run(scribe_handler()(
        TaskRequest(prompt="Draft a professional email to the project team"),
        None,
    ))
    assert "I'm reaching out regarding [topic/purpose]." in output["content"]
    assert "purpose_or_body_or_key_points" in output["missing_context"]
