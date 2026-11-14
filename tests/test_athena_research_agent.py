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
                "page_content": ("Authentication uses a Bearer API key. " "POST https://api.tavily.com/search executes a search query. " "The query and search_depth parameters control the request body. " "The response contains results with content and score fields."),
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


def test_navigation_only_raw_content_does_not_count_as_substantive_evidence():
    content = (
        '## Documentation Index Fetch the complete documentation index at llms.txt '
        'Use this file to discover all available pages before exploring further. '
        'Get started API Reference Integrations Examples Changelog'
    )
    assert ResearchAgent._has_substantive_page_content(content) is False


def test_technical_evidence_matrix_requires_dimension_specific_support():
    sources = [{
        'url': 'https://docs.tavily.com/documentation/api-reference/endpoint/search',
        'page_content': (
            'Authentication uses a Bearer API key. '
            'The page discusses API usage and search results. '
            'The query parameter is required in the request body. '
            'The response contains results with content and score fields.'
        ),
    }]
    matrix = ResearchAgent._build_technical_evidence_matrix(sources)
    assert matrix['Authentication']
    assert matrix['Request parameters']
    assert matrix['Response structure']
    assert matrix['Endpoint and method'] == []


def test_official_tavily_research_requires_all_four_technical_dimensions():
    agent = ResearchAgent()
    queries = agent._build_queries('Research the Tavily Search API official documentation')
    docs = [
        ('Headers', 'https://docs.tavily.com/documentation/api-headers',
         'Authentication uses a Bearer API key.'),
        ('Search API', 'https://docs.tavily.com/documentation/api-reference/endpoint/search',
         'The query parameter is required in the request body. The response contains results with content and score fields.'),
        ('Best practices', 'https://docs.tavily.com/documentation/best-practices/best-practices-search',
         'Use include_raw_content=true to retrieve extracted content.'),
    ]
    outcomes = {
        query: {
            'provider': 'Tavily Search API',
            'retrieved_at': '2026-11-12T14:00:00+00:00',
            'results': [{
                'title': title, 'url': url, 'domain': 'docs.tavily.com',
                'snippet': 'Official documentation', 'page_content': content,
            }],
        }
        for query, (title, url, content) in zip(queries, docs)
    }
    result = asyncio.run(agent(SimpleNamespace(
        prompt='Research the Tavily Search API official documentation'
    ), FakeContext(outcomes)))
    assert result['official_source_threshold_met'] is True
    assert result['extracted_content_count'] == 3
    assert result['technical_coverage_complete'] is False
    assert result['research_status'] == 'insufficient_technical_coverage'
    assert result['complete'] is False
    assert 'Endpoint and method' not in result['technical_dimensions_covered']



def test_tavily_integration_and_changelog_pages_do_not_count_as_api_evidence():
    assert ResearchAgent._is_relevant_api_source(
        "https://docs.tavily.com/documentation/api-reference/endpoint/search"
    )
    assert ResearchAgent._is_relevant_api_source(
        "https://docs.tavily.com/documentation/api-headers"
    )
    assert not ResearchAgent._is_relevant_api_source(
        "https://docs.tavily.com/documentation/integrations/anthropic"
    )
    assert not ResearchAgent._is_relevant_api_source(
        "https://docs.tavily.com/changelog"
    )


def test_athena_report_exposes_scout_query_and_provider_provenance():
    agent = ResearchAgent()
    queries = agent._build_queries("Research the Tavily Search API official documentation")
    docs = [
        ("Headers", "https://docs.tavily.com/documentation/api-headers"),
        ("Search API", "https://docs.tavily.com/documentation/api-reference/endpoint/search"),
        ("Best practices", "https://docs.tavily.com/documentation/best-practices/best-practices-search"),
    ]
    outcomes = {
        query: {
            "provider": "Tavily Search API",
            "retrieved_at": "2026-11-12T14:00:00+00:00",
            "results": [{
                "title": title, "url": url, "domain": "docs.tavily.com",
                "snippet": "Official documentation",
                "page_content": (
                    "Authentication uses a Bearer API key. "
                    "POST https://api.tavily.com/search executes a search query. "
                    "The query parameter is required in the request body. "
                    "The response contains results with content and score fields."
                ),
            }],
        }
        for query, (title, url) in zip(queries, docs)
    }
    result = asyncio.run(agent(SimpleNamespace(
        prompt="Research the Tavily Search API official documentation"
    ), FakeContext(outcomes)))
    report = result["summary"]
    assert "Scout delegation and retrieval provenance:" in report
    assert "search.scout via Tavily Search API: complete" in report
    assert all(query in report for query in queries)
    assert result["extracted_content_count"] == 3


def test_non_tavily_official_research_keeps_topic_specific_primary_source_counting():
    agent = ResearchAgent()
    queries = agent._build_queries("Research the FastAPI Search API official documentation")
    docs = [
        ("FastAPI reference", "https://fastapi.tiangolo.com/reference/"),
        ("FastAPI package", "https://pypi.org/project/fastapi/"),
        ("FastAPI repository", "https://github.com/fastapi/fastapi"),
    ]
    page_content = (
        "Authentication uses a Bearer API token. "
        "POST /search executes a search query. "
        "The query parameter is required in the request body. "
        "The response contains results with content and score fields."
    )
    outcomes = {
        query: {
            "provider": "Tavily Search API",
            "retrieved_at": "2026-11-12T17:30:00+00:00",
            "results": [{
                "title": title,
                "url": url,
                "domain": url.split("/")[2],
                "snippet": "Official primary documentation",
                "page_content": page_content,
            }],
        }
        for query, (title, url) in zip(queries, docs)
    }

    result = asyncio.run(agent(SimpleNamespace(
        prompt="Research the FastAPI Search API official documentation"
    ), FakeContext(outcomes)))

    assert result["primary_source_count"] == 3
    assert result["extracted_content_count"] == 3
    assert result["extracted_content_threshold_met"] is True
    assert result["research_status"] == "retrieval_complete_with_extracted_content"
    assert result["complete"] is True


MODEL_COMPARISON_PROMPT = """
Run a complete multi-agent research and analysis task.

OBJECTIVE:
Recommend the best local AI model setup for a student developer using Windows 11, Python and Ollama.

Compare:
1. Qwen 3 4B
2. Llama 3.2 3B
3. Gemma 3 4B

REQUIREMENTS:
- Research current model information using reliable official sources.
- Compare reasoning, coding, speed, memory requirements, context length, licensing and Ollama compatibility.
"""


def _model_card_content(model_name: str) -> str:
    return (
        f"The {model_name} model publishes reasoning benchmark results, including MMLU and GPQA. "
        "Its coding performance includes code generation and HumanEval benchmark results. "
        "Published inference speed is measured in tokens per second on stated hardware. "
        "Memory requirements describe RAM, VRAM and quantization options for local inference. "
        "The model card documents the context window, context length and supported token limits. "
        "The model licensing terms identify the applicable license and usage conditions. "
        "Ollama compatibility is documented for local model execution."
    )


def test_model_comparison_queries_are_targeted_to_all_three_named_models():
    queries = ResearchAgent._build_queries(MODEL_COMPARISON_PROMPT)
    assert len(queries) == 3
    assert any("Qwen 3 4B" in query for query in queries)
    assert any("Llama 3.2 3B" in query for query in queries)
    assert any("Gemma 3 4B" in query for query in queries)


def test_model_comparison_recognizes_only_publisher_owned_or_ollama_model_pages():
    assert ResearchAgent._is_primary_source(
        MODEL_COMPARISON_PROMPT, "https://qwen.ai/models/qwen3"
    )
    assert ResearchAgent._is_primary_source(
        MODEL_COMPARISON_PROMPT, "https://llama.com/docs/model-cards/llama3_2"
    )
    assert ResearchAgent._is_primary_source(
        MODEL_COMPARISON_PROMPT, "https://ai.google.dev/gemma/docs/core"
    )
    assert ResearchAgent._is_primary_source(
        MODEL_COMPARISON_PROMPT, "https://ollama.com/library/gemma3"
    )
    assert not ResearchAgent._is_primary_source(
        MODEL_COMPARISON_PROMPT, "https://random-model-review.example/models/gemma3"
    )


def test_model_comparison_evidence_matrix_covers_all_requested_dimensions_with_source_links():
    sources = [{
        "url": "https://qwen.ai/models/qwen3",
        "page_content": _model_card_content("Qwen 3 4B"),
    }]
    matrix = ResearchAgent._build_model_evidence_matrix(sources)
    assert set(matrix) == {
        "Reasoning", "Coding performance", "Speed", "Memory requirements",
        "Context length", "Licensing", "Ollama compatibility",
    }
    assert all(matrix.values())
    assert all(
        item["url"] == sources[0]["url"]
        for entries in matrix.values()
        for item in entries
    )


def test_generic_model_card_content_counts_as_substantive_but_navigation_does_not():
    assert ResearchAgent._has_substantive_page_content(_model_card_content("Qwen 3 4B"))
    assert not ResearchAgent._has_substantive_page_content(
        "API Reference Integrations Changelog Examples Get Started Documentation Index"
    )


def test_model_comparison_is_complete_only_with_three_official_model_sources_and_all_dimensions():
    agent = ResearchAgent()
    queries = agent._build_queries(MODEL_COMPARISON_PROMPT)
    docs = [
        ("Qwen 3 4B", "https://qwen.ai/models/qwen3"),
        ("Llama 3.2 3B", "https://llama.com/docs/model-cards/llama3_2"),
        ("Gemma 3 4B", "https://ai.google.dev/gemma/docs/core"),
    ]
    outcomes = {
        query: {
            "provider": "Tavily Search API",
            "retrieved_at": "2026-11-14T12:00:00+00:00",
            "retrieval_status": "complete",
            "partial": False,
            "results": [{
                "title": f"{model_name} official model card",
                "url": url,
                "domain": url.split("/")[2],
                "snippet": f"Official {model_name} model information.",
                "page_content": _model_card_content(model_name),
            }],
        }
        for query, (model_name, url) in zip(queries, docs)
    }
    result = asyncio.run(
        agent(SimpleNamespace(prompt=MODEL_COMPARISON_PROMPT), FakeContext(outcomes))
    )
    assert result["requested_model_targets"] == ["Qwen 3 4B", "Llama 3.2 3B", "Gemma 3 4B"]
    assert result["primary_source_count"] == 3
    assert result["extracted_content_count"] == 3
    assert result["model_target_coverage_complete"] is True
    assert result["technical_coverage_complete"] is True
    assert set(result["technical_dimensions_covered"]) == set(result["technical_dimensions_required"])
    assert result["research_status"] == "retrieval_complete_with_extracted_content"
    assert result["complete"] is True


def test_model_comparison_does_not_pass_when_one_named_model_lacks_its_official_source():
    agent = ResearchAgent()
    queries = agent._build_queries(MODEL_COMPARISON_PROMPT)
    urls = [
        "https://qwen.ai/models/qwen3",
        "https://qwen.ai/models/qwen3-4b-instruct",
        "https://llama.com/docs/model-cards/llama3_2",
    ]
    outcomes = {
        query: {
            "provider": "Tavily Search API",
            "retrieval_status": "complete",
            "partial": False,
            "results": [{
                "title": "Official model card",
                "url": url,
                "domain": url.split("/")[2],
                "snippet": "Official model information.",
                "page_content": _model_card_content("Model"),
            }],
        }
        for query, url in zip(queries, urls)
    }
    result = asyncio.run(
        agent(SimpleNamespace(prompt=MODEL_COMPARISON_PROMPT), FakeContext(outcomes))
    )
    assert result["primary_source_count"] == 3
    assert result["model_target_coverage_complete"] is False
    assert result["model_target_coverage"]["Gemma 3 4B"] == []
    assert result["research_status"] == "insufficient_model_target_coverage"
    assert result["complete"] is False
