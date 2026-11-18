from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.search_agent.agent import SearchAgent, _normalise_query, _parse_results


SAMPLE_HTML = '''
<html><body>
<div class="result results_links results_links_deep">
  <a class="result__a" href="https://docs.python.org/3/">Python 3 Documentation</a>
  <a class="result__snippet">Official documentation for the Python programming language.</a>
</div>
<div class="result results_links results_links_deep">
  <a class="result__a" href="https://github.com/python/cpython">CPython repository</a>
  <a class="result__snippet">The Python programming language implementation.</a>
</div>
</body></html>
'''


def test_normalise_query_removes_command_prefix_only() -> None:
    assert _normalise_query("Jarvis, search the web for Python 3.12 docs") == "Python 3.12 docs"
    assert _normalise_query("latest Python packages") == "latest Python packages"
    assert _normalise_query("search for") == ""


def test_parse_results_returns_linked_structured_results() -> None:
    results = _parse_results(SAMPLE_HTML, 5)
    assert len(results) == 2
    assert results[0]["title"] == "Python 3 Documentation"
    assert results[0]["url"] == "https://docs.python.org/3/"
    assert results[0]["domain"] == "docs.python.org"
    assert "Official documentation" in results[0]["snippet"]


def test_parse_results_deduplicates_and_respects_limit() -> None:
    html = SAMPLE_HTML.replace(
        '</body>',
        '<a class="result__a" href="https://docs.python.org/3/">Duplicate</a></body>',
    )
    results = _parse_results(html, 1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_agent_returns_structured_results_and_summary(monkeypatch) -> None:
    import agents.search_agent.agent as module

    monkeypatch.setattr(module, "_fetch_html", lambda query, *, timeout: SAMPLE_HTML)
    agent = SearchAgent(result_count=3)
    output = await agent(SimpleNamespace(prompt="search the web for Python docs"), None)
    assert output["query"] == "Python docs"
    assert output["provider"] == "DuckDuckGo HTML search"
    assert len(output["results"]) == 2
    assert "https://docs.python.org/3/" in output["summary"]
    assert output["retrieved_at"]


@pytest.mark.asyncio
async def test_agent_reports_empty_provider_results_without_fabricating(monkeypatch) -> None:
    import agents.search_agent.agent as module

    monkeypatch.setattr(module, "_fetch_html", lambda query, *, timeout: "<html>challenge page</html>")
    output = await SearchAgent()(SimpleNamespace(prompt="search for an obscure query"), None)
    assert output["results"] == []
    assert "no parseable results" in output["summary"]


@pytest.mark.asyncio
async def test_agent_reports_network_errors(monkeypatch) -> None:
    import agents.search_agent.agent as module

    def fail(query, *, timeout):
        raise OSError("offline")

    monkeypatch.setattr(module, "_fetch_html", fail)
    output = await SearchAgent()(SimpleNamespace(prompt="search for Python"), None)
    assert output["results"] == []
    assert "No results have been fabricated" in output["summary"]


@pytest.mark.asyncio
async def test_agent_asks_for_query_when_prompt_is_empty() -> None:
    output = await SearchAgent()(SimpleNamespace(prompt="search for"), None)
    assert output["results"] == []
    assert "what you want me to search for" in output["summary"]


def test_parse_results_skips_sponsored_duckduckgo_trackers():
    html = SAMPLE_HTML.replace(
        '</body>',
        '<a class="result__a" href="https://duckduckgo.com/y.js?ad_domain=example.com">Sponsored result</a></body>',
    )
    results = _parse_results(html, 10)
    assert all("duckduckgo.com/y.js" not in item["url"] for item in results)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_agent_uses_brave_json_api_when_configured(monkeypatch) -> None:
    import agents.search_agent.agent as module

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key-not-real")
    monkeypatch.delenv("JARVIS_SEARCH_PROVIDER", raising=False)

    def fake_brave(query, *, timeout, limit):
        assert query == "FastAPI official docs"
        assert limit == 5
        return [{
            "title": "FastAPI",
            "url": "https://fastapi.tiangolo.com/",
            "domain": "fastapi.tiangolo.com",
            "snippet": "Official FastAPI documentation.",
        }]

    monkeypatch.setattr(module, "_fetch_brave_results", fake_brave)
    output = await SearchAgent()(SimpleNamespace(prompt="search the web for FastAPI official docs"), None)
    assert output["provider"] == "Brave Search API"
    assert output["results"][0]["url"] == "https://fastapi.tiangolo.com/"


def test_fetch_brave_results_parses_json_without_network(monkeypatch) -> None:
    import io
    import json
    import agents.search_agent.agent as module

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key-not-real")

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({"web": {"results": [{
                "title": "FastAPI Docs",
                "url": "https://fastapi.tiangolo.com/",
                "description": "Official docs",
            }]}}).encode()

    monkeypatch.setattr(module, "urlopen", lambda request, timeout: FakeResponse())
    results = module._fetch_brave_results("FastAPI", timeout=1, limit=3)
    assert results == [{
        "title": "FastAPI Docs",
        "url": "https://fastapi.tiangolo.com/",
        "domain": "fastapi.tiangolo.com",
        "snippet": "Official docs",
    }]


@pytest.mark.asyncio
async def test_agent_uses_tavily_json_api_when_configured(monkeypatch) -> None:
    import agents.search_agent.agent as module

    monkeypatch.setenv("JARVIS_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key-not-real")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    def fake_tavily(query, *, timeout, limit):
        assert query == "FastAPI official docs"
        assert limit == 5
        return [{
            "title": "FastAPI",
            "url": "https://fastapi.tiangolo.com/",
            "domain": "fastapi.tiangolo.com",
            "snippet": "Official FastAPI documentation.",
        }]

    monkeypatch.setattr(module, "_fetch_tavily_results", fake_tavily)
    output = await SearchAgent()(SimpleNamespace(prompt="search the web for FastAPI official docs"), None)
    assert output["provider"] == "Tavily Search API"
    assert output["results"][0]["url"] == "https://fastapi.tiangolo.com/"


def test_fetch_tavily_results_parses_json_without_network(monkeypatch) -> None:
    import json
    import agents.search_agent.agent as module

    monkeypatch.setenv("TAVILY_API_KEY", "test-key-not-real")

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({"results": [{
                "title": "FastAPI Docs",
                "url": "https://fastapi.tiangolo.com/",
                "content": "Official docs",
            }]}).encode()

    def fake_urlopen(request, timeout):
        assert request.full_url == module.TAVILY_SEARCH_ENDPOINT
        assert request.get_method() == "POST"
        body = json.loads(request.data.decode())
        assert body["api_key"] == "test-key-not-real"
        assert body["query"] == "FastAPI"
        return FakeResponse()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    results = module._fetch_tavily_results("FastAPI", timeout=1, limit=3)
    assert results == [{
        "title": "FastAPI Docs",
        "url": "https://fastapi.tiangolo.com/",
        "domain": "fastapi.tiangolo.com",
        "snippet": "Official docs",
    }]


def test_fetch_tavily_results_passes_domain_restrictions(monkeypatch) -> None:
    import json
    import agents.search_agent.agent as module

    monkeypatch.setenv("TAVILY_API_KEY", "test-key-not-real")

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({"results": []}).encode()

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode())
        assert body["include_domains"] == ["fastapi.tiangolo.com"]
        assert body["query"] == "FastAPI release notes"
        return FakeResponse()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    assert module._fetch_tavily_results(
        "FastAPI release notes", timeout=1, limit=3,
        include_domains=["fastapi.tiangolo.com"],
    ) == []


@pytest.mark.asyncio
async def test_agent_passes_site_filter_to_tavily(monkeypatch) -> None:
    import agents.search_agent.agent as module

    monkeypatch.setenv("JARVIS_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key-not-real")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    seen = {}

    def fake_tavily(query, *, timeout, limit, include_domains=None):
        seen.update(query=query, include_domains=include_domains, limit=limit)
        return [{
            "title": "FastAPI Release Notes",
            "url": "https://fastapi.tiangolo.com/release-notes/",
            "domain": "fastapi.tiangolo.com",
            "snippet": "Official release notes.",
        }]

    monkeypatch.setattr(module, "_fetch_tavily_results", fake_tavily)
    output = await SearchAgent()(SimpleNamespace(
        prompt="Search the web for: FastAPI release notes site:fastapi.tiangolo.com"
    ), None)
    assert seen["query"] == "FastAPI release notes"
    assert seen["include_domains"] == ["fastapi.tiangolo.com"]
    assert output["results"][0]["url"] == "https://fastapi.tiangolo.com/release-notes/"
