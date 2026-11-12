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
    context = FakeContext({query: {"results": [shared], "provider": "Tavily Search API", "retrieved_at": "2026-11-10T14:00:00+00:00", "partial": False, "retrieval_status": "complete", "requested_result_count": 5, "result_count": 5} for query in queries})
    result = asyncio.run(agent(SimpleNamespace(prompt="FastAPI stable release"), context))
    assert result["source_count"] == 1
    assert result["sources"][0]["url"] == "https://fastapi.tiangolo.com/"
    assert result["evidence_level"] == "search_snippets_not_independently_verified"
    assert result["research_status"] == "retrieval_complete_unverified"
    assert result["complete"] is True
    assert result["sources"][0]["provider"] == "Tavily Search API"
    assert result["sources"][0]["retrieved_at"] == "2026-11-10T14:00:00+00:00"
    assert len(context.calls) == 3
    assert "not independently opened or verified" in result["summary"]


def test_research_reports_insufficient_evidence_when_all_searches_fail():
    agent = ResearchAgent()
    queries = agent._build_queries("test topic")
    context = FakeContext({query: {"results": [], "error": "provider unavailable"} for query in queries})
    result = asyncio.run(agent(SimpleNamespace(prompt="test topic"), context))
    assert result["research_status"] == "insufficient_evidence"
    assert result["evidence_level"] == "no_retrieved_evidence"
    assert result["source_count"] == 0
    assert result["complete"] is False
    assert len(result["failures"]) == 3
    assert "No findings have been inferred or fabricated" in result["summary"]


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


def test_repeated_source_across_queries_does_not_mark_athena_partial():
    agent = ResearchAgent()
    queries = agent._build_queries("FastAPI stable release")
    shared = {
        "title": "Official docs",
        "url": "https://fastapi.tiangolo.com/",
        "domain": "fastapi.tiangolo.com",
        "snippet": "Official documentation",
    }
    context = FakeContext({
        query: {
            "results": [shared],
            "provider": "Tavily Search API",
            "retrieved_at": "2026-11-10T14:00:00+00:00",
            "partial": False,
            "retrieval_status": "complete",
        }
        for query in queries
    })

    result = asyncio.run(agent(SimpleNamespace(prompt="FastAPI stable release"), context))

    assert result["source_count"] == 1  # Global source list remains deduplicated.
    assert result["research_status"] == "retrieval_complete_unverified"
    assert result["complete"] is True
    assert [item["status"] for item in result["query_provenance"]] == ["complete"] * 3



def test_tavily_query_plan_uses_three_distinct_official_documentation_angles():
    queries = ResearchAgent._build_queries(
        "Research the Tavily Search API using Scout's web-retrieval capability. "
        "Compare authentication, endpoint, request parameters and response structure."
    )
    assert len(queries) == 3
    assert len(set(queries)) == 3
    assert all("site:docs.tavily.com" in query for query in queries)
    assert any("authentication" in query.casefold() for query in queries)
    assert any("request parameters" in query.casefold() for query in queries)
    assert any("best practices" in query.casefold() for query in queries)
    assert all("web-retrieval capability" not in query.casefold() for query in queries)


def test_athena_records_scout_delegation_provenance_and_requires_three_official_sources():
    agent = ResearchAgent()
    queries = agent._build_queries("Research the Tavily Search API official documentation")
    docs = [
        ("Search API", "https://docs.tavily.com/documentation/api-reference/endpoint/search"),
        ("Search best practices", "https://docs.tavily.com/documentation/best-practices/best-practices-search"),
        ("Python quickstart", "https://docs.tavily.com/sdk/python/quick-start"),
    ]
    outcomes = {
        query: {
            "provider": "Tavily Search API",
            "retrieved_at": "2026-11-12T14:00:00+00:00",
            "results": [{
                "title": title,
                "url": url,
                "domain": "docs.tavily.com",
                "snippet": "Documentation discovery snippet",
                "page_content": "Extracted documentation content",
            }],
        }
        for query, (title, url) in zip(queries, docs)
    }
    context = FakeContext(outcomes)
    result = asyncio.run(agent(SimpleNamespace(
        prompt="Research the Tavily Search API official documentation"
    ), context))
    assert len(context.calls) == 3
    assert result["primary_source_count"] == 3
    assert result["official_source_threshold_met"] is True
    assert result["extracted_content_count"] == 3
    assert result["evidence_level"] == "provider_extracted_page_content"
    assert result["complete"] is True
    assert all(item["delegated_agent_id"] == "search.scout" for item in result["query_provenance"])
    assert all(item["requested_by"] == "research.athena" for item in result["query_provenance"])
    assert "provider-extracted content" in result["summary"]


def test_athena_does_not_mark_official_research_complete_without_three_primary_sources():
    agent = ResearchAgent()
    queries = agent._build_queries("Research the Tavily Search API official documentation")
    context = FakeContext({
        query: {
            "provider": "Tavily Search API",
            "results": [{
                "title": "Unofficial guide",
                "url": f"https://example{index}.org/tavily",
                "domain": f"example{index}.org",
                "snippet": "Unverified",
            }],
        }
        for index, query in enumerate(queries)
    })
    result = asyncio.run(agent(SimpleNamespace(
        prompt="Research the Tavily Search API official documentation"
    ), context))
    assert result["primary_source_count"] == 0
    assert result["official_source_threshold_met"] is False
    assert result["research_status"] == "insufficient_primary_sources"
    assert result["complete"] is False



def test_official_research_requires_extracted_content_not_search_snippets_alone():
    agent = ResearchAgent()
    queries = agent._build_queries("Research the Tavily Search API official documentation")
    docs = [
        "https://docs.tavily.com/documentation/api-reference/endpoint/search",
        "https://docs.tavily.com/documentation/best-practices/best-practices-search",
        "https://docs.tavily.com/sdk/python/quick-start",
    ]
    context = FakeContext({
        query: {
            "provider": "Tavily Search API",
            "results": [{
                "title": "Official Tavily documentation",
                "url": url,
                "domain": "docs.tavily.com",
                "snippet": "Search result snippet only",
            }],
        }
        for query, url in zip(queries, docs)
    })
    result = asyncio.run(agent(SimpleNamespace(
        prompt="Research the Tavily Search API official documentation"
    ), context))
    assert result["primary_source_count"] == 3
    assert result["official_source_threshold_met"] is True
    assert result["extracted_content_threshold_met"] is False
    assert result["research_status"] == "insufficient_extracted_content"
    assert result["complete"] is False



def test_athena_builds_source_linked_technical_evidence_matrix():
    sources = [{
        "url": "https://docs.tavily.com/documentation/api-reference/endpoint/search",
        "page_content": (
            "Authentication uses a Bearer API key. "
            "POST https://api.tavily.com/search executes a search query. "
            "The query and search_depth parameters control the request. "
            "The response contains results with content and score."
        ),
    }]
    matrix = ResearchAgent._build_technical_evidence_matrix(sources)
    assert matrix["Authentication"]
    assert matrix["Endpoint and method"]
    assert matrix["Request parameters"]
    assert matrix["Response structure"]
    assert all(
        item["url"] == sources[0]["url"]
        for entries in matrix.values()
        for item in entries
    )
