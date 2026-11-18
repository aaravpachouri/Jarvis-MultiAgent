import asyncio
from types import SimpleNamespace

import pytest

from agents.research_agent.agent import ResearchAgent, create_handler


class FakeContext:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    async def delegate(self, **kwargs):
        self.calls.append(kwargs)
        query = kwargs["context"]["research_query"]
        outcome = self.outcomes.get(query, {})
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            succeeded=not bool(outcome.get("error")),
            output=outcome,
            error=outcome.get("error"),
            summary=outcome.get("error", ""),
        )


def test_create_handler_is_executable():
    assert isinstance(create_handler(), ResearchAgent)


def test_research_returns_deduplicated_source_linked_evidence():
    agent = ResearchAgent()
    queries = agent._build_queries("FastAPI stable release")
    shared = {"title": "Official docs", "url": "https://fastapi.tiangolo.com/", "domain": "fastapi.tiangolo.com", "snippet": "Official documentation"}
    context = FakeContext({query: {"results": [shared]} for query in queries})
    result = asyncio.run(agent(SimpleNamespace(prompt="FastAPI stable release"), context))
    assert result["source_count"] == 1
    assert result["sources"][0]["url"] == "https://fastapi.tiangolo.com/"
    assert result["evidence_level"] == "search_snippets_not_independently_verified"
    assert len(context.calls) == 3
    assert "not independently opened or verified" in result["summary"]


def test_research_fails_when_no_search_produces_sources():
    agent = ResearchAgent()
    queries = agent._build_queries("test topic")
    context = FakeContext({query: {"results": [], "error": "provider unavailable"} for query in queries})
    with pytest.raises(RuntimeError, match="could not retrieve research sources"):
        asyncio.run(agent(SimpleNamespace(prompt="test topic"), context))


def test_empty_prompt_is_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        asyncio.run(ResearchAgent()(SimpleNamespace(prompt="  "), FakeContext({})))


def test_athena_analyzes_supplied_context_without_scout():
    class NoSearchContext:
        async def delegate(self, **kwargs):
            raise AssertionError("Offline context analysis must not call Scout")

    task = SimpleNamespace(
        prompt="Analyze the supplied project notes",
        context={"source_material": ["The app starts locally.", "Automated tests pass, but deployment is unverified."]},
    )
    result = asyncio.run(ResearchAgent()(task, NoSearchContext()))
    assert result["analysis_mode"] == "offline_context_analysis"
    assert result["evidence_level"] == "user_supplied_context_only"
    assert result["source_count"] == 0
    assert result["provided_item_count"] == 2
    assert "deployment is unverified" in result["summary"]
    assert result["queries"] == []


def test_athena_can_use_existing_conversation_context_without_scout():
    class NoSearchContext:
        async def delegate(self, **kwargs):
            raise AssertionError("Conversation-context analysis must not call Scout")

    task = SimpleNamespace(
        prompt="Summarize what we have discussed",
        context={"conversation_history": [{"role": "user", "content": "We need a local planner."}]},
    )
    result = asyncio.run(ResearchAgent()(task, NoSearchContext()))
    assert result["analysis_mode"] == "offline_context_analysis"
    assert "We need a local planner" in result["summary"]
