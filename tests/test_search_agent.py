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



def test_result_url_validation_rejects_non_http_and_userinfo_urls():
    from agents.search_agent.agent import _is_public_http_url

    assert _is_public_http_url("https://docs.python.org/3/")
    assert _is_public_http_url("http://example.com/path")
    assert not _is_public_http_url("javascript:alert(1)")
    assert not _is_public_http_url("file:///etc/passwd")
    assert not _is_public_http_url("https://user:password@example.com/")
    assert not _is_public_http_url("https://")


@pytest.mark.asyncio
async def test_empty_results_preserve_provider_and_structured_status(monkeypatch):
    import agents.search_agent.agent as module

    monkeypatch.setenv("JARVIS_SEARCH_PROVIDER", "duckduckgo")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.setattr(module, "_fetch_html", lambda query, *, timeout: "<html>challenge page</html>")
    output = await SearchAgent()(SimpleNamespace(prompt="search for obscure topic"), None)
    assert output["provider"] == "DuckDuckGo HTML search"
    assert output["error_code"] == "empty_results"
    assert output["retryable"] is False
    assert output["results"] == []


@pytest.mark.asyncio
async def test_network_failure_preserves_provider_and_retryability(monkeypatch):
    import agents.search_agent.agent as module

    monkeypatch.setenv("JARVIS_SEARCH_PROVIDER", "duckduckgo")
    monkeypatch.setattr(module, "_fetch_html", lambda query, *, timeout: (_ for _ in ()).throw(OSError("offline")))
    output = await SearchAgent()(SimpleNamespace(prompt="search for Python"), None)
    assert output["provider"] == "DuckDuckGo HTML search"
    assert output["error_code"] == "network_error"
    assert output["retryable"] is True


@pytest.mark.asyncio
async def test_rate_limit_error_is_explicit_and_preserves_retry_after(monkeypatch):
    import email.message
    import urllib.error
    import agents.search_agent.agent as module

    monkeypatch.setenv("JARVIS_SEARCH_PROVIDER", "duckduckgo")
    headers = email.message.Message()
    headers["Retry-After"] = "60"

    def rate_limited(query, *, timeout):
        raise urllib.error.HTTPError("https://search.example", 429, "Too Many Requests", headers, None)

    monkeypatch.setattr(module, "_fetch_html", rate_limited)
    output = await SearchAgent()(SimpleNamespace(prompt="search for Python"), None)
    assert output["error_code"] == "rate_limited"
    assert output["retryable"] is True
    assert output["retry_after"] == "60"
    assert output["provider"] == "DuckDuckGo HTML search"


def test_invalid_search_environment_values_use_safe_defaults(monkeypatch):
    monkeypatch.setenv("JARVIS_SEARCH_TIMEOUT", "not-a-number")
    monkeypatch.setenv("JARVIS_SEARCH_RESULTS", "many")
    agent = SearchAgent()
    assert agent.timeout_seconds == 12.0
    assert agent.result_count == 5


def test_search_timeout_is_capped():
    assert SearchAgent(timeout_seconds=999).timeout_seconds == 60.0


@pytest.mark.asyncio
async def test_agent_falls_back_to_duckduckgo_after_brave_timeout(monkeypatch):
    import agents.search_agent.agent as module

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key-not-real")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_SEARCH_PROVIDER", raising=False)

    def fail_brave(query, *, timeout, limit):
        raise TimeoutError("Brave timed out")

    monkeypatch.setattr(module, "_fetch_brave_results", fail_brave)
    monkeypatch.setattr(module, "_fetch_html", lambda query, *, timeout: SAMPLE_HTML)

    output = await SearchAgent()(SimpleNamespace(prompt="search for Python docs"), None)

    assert len(output["results"]) == 2
    assert output["provider"] == "DuckDuckGo HTML search"
    assert output["provider_failures"][0]["error_code"] == "network_error"
    assert output["providers_attempted"] == ["Brave Search API", "DuckDuckGo HTML search"]


@pytest.mark.asyncio
async def test_agent_combines_partial_results_from_multiple_providers(monkeypatch):
    import agents.search_agent.agent as module

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key-not-real")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key-not-real")
    monkeypatch.delenv("JARVIS_SEARCH_PROVIDER", raising=False)

    monkeypatch.setattr(module, "_fetch_brave_results", lambda query, *, timeout, limit: [])
    monkeypatch.setattr(module, "_fetch_tavily_results", lambda query, *, timeout, limit, include_domains=None: [{
        "title": "Tavily result", "url": "https://example.org/b", "domain": "example.org", "snippet": "B"
    }])

    output = await SearchAgent(result_count=5)(SimpleNamespace(prompt="research topic"), None)

    assert [item["url"] for item in output["results"]] == ["https://example.org/b"]
    assert output["provider"] == "Tavily Search API"
    assert output["provider_failures"][0]["error_code"] == "empty_results"
    assert output["partial"] is True


@pytest.mark.asyncio
async def test_agent_reports_all_provider_failures_without_fabricating(monkeypatch):
    import agents.search_agent.agent as module

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key-not-real")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key-not-real")
    monkeypatch.delenv("JARVIS_SEARCH_PROVIDER", raising=False)

    def fail(*args, **kwargs):
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(module, "_fetch_brave_results", fail)
    monkeypatch.setattr(module, "_fetch_tavily_results", fail)
    monkeypatch.setattr(module, "_fetch_html", fail)

    output = await SearchAgent()(SimpleNamespace(prompt="research topic"), None)

    assert output["results"] == []
    assert output["error_code"] == "network_error"
    assert len(output["provider_failures"]) == 3
    assert output["providers_attempted"] == [
        "Brave Search API", "Tavily Search API", "DuckDuckGo HTML search"
    ]
    assert "No results have been fabricated" in output["summary"]


@pytest.mark.asyncio
async def test_explicit_tavily_provider_still_falls_back_to_brave(monkeypatch):
    import agents.search_agent.agent as module

    monkeypatch.setenv("JARVIS_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key-not-real")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key-not-real")

    monkeypatch.setattr(module, "_fetch_tavily_results", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("Tavily timeout")))
    monkeypatch.setattr(module, "_fetch_brave_results", lambda query, *, timeout, limit: [{
        "title": "Brave result", "url": "https://example.com/", "domain": "example.com", "snippet": "Recovered"
    }])

    output = await SearchAgent()(SimpleNamespace(prompt="research topic"), None)

    assert output["provider"] == "Brave Search API"
    assert output["results"][0]["url"] == "https://example.com/"
    assert output["provider_failures"][0]["provider"] == "Tavily Search API"


@pytest.mark.asyncio
async def test_scientific_research_uses_crossref_without_api_keys(monkeypatch):
    import agents.search_agent.agent as module

    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_SEARCH_PROVIDER", raising=False)
    calls = []

    def fake_crossref(query, *, timeout, limit):
        calls.append((query, timeout, limit))
        return [{
            "title": "A peer-reviewed scientific paper",
            "url": "https://doi.org/10.1234/example",
            "domain": "doi.org",
            "snippet": "Published: 2025-03-01. Example journal record.",
            "publication_date": "2025-03-01",
            "source_type": "scholarly_record",
        }]

    def fail_ddg(query, *, timeout):
        raise AssertionError("DuckDuckGo should not be needed after Crossref returns usable links")

    monkeypatch.setattr(module, "_fetch_crossref_results", fake_crossref)
    monkeypatch.setattr(module, "_fetch_html", fail_ddg)
    output = await SearchAgent()(SimpleNamespace(prompt="Find recent scientific papers on room-temperature superconductivity"), None)

    assert len(calls) == 1
    assert output["provider"] == "Crossref scholarly works API"
    assert output["results"][0]["url"] == "https://doi.org/10.1234/example"
    assert output["results"][0]["publication_date"] == "2025-03-01"


def test_crossref_parser_extracts_doi_title_and_publication_date(monkeypatch):
    import json
    import agents.search_agent.agent as module

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({"message": {"items": [{
                "title": ["A scholarly paper"],
                "URL": "https://doi.org/10.1234/example",
                "published": {"date-parts": [[2024, 7, 9]]},
                "container-title": ["Journal of Examples"],
                "abstract": "<jats:p>Study abstract</jats:p>",
            }]}}).encode()

    monkeypatch.setattr(module, "urlopen", lambda request, timeout: FakeResponse())
    results = module._fetch_crossref_results("scholarly paper", timeout=2, limit=5)
    assert results[0]["title"] == "A scholarly paper"
    assert results[0]["url"] == "https://doi.org/10.1234/example"
    assert results[0]["publication_date"] == "2024-07-09"
    assert "Study abstract" in results[0]["snippet"]


@pytest.mark.asyncio
async def test_crossref_recovers_general_query_after_duckduckgo_empty(monkeypatch):
    import agents.search_agent.agent as module

    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_SEARCH_PROVIDER", raising=False)
    monkeypatch.setattr(module, "_fetch_html", lambda query, *, timeout: "<html>challenge page</html>")
    monkeypatch.setattr(module, "_fetch_crossref_results", lambda query, *, timeout, limit: [{
        "title": "Research related to air quality",
        "url": "https://doi.org/10.1234/aqi",
        "domain": "doi.org",
        "snippet": "Scholarly record",
        "publication_date": "2024-01-01",
        "source_type": "scholarly_record",
    }])

    output = await SearchAgent()(SimpleNamespace(prompt="current AQI situation in India"), None)
    assert output["provider"] == "Crossref scholarly works API"
    assert output["results"][0]["url"] == "https://doi.org/10.1234/aqi"
    assert output["provider_failures"][0]["provider"] == "DuckDuckGo HTML search"
    assert output["provider_failures"][0]["error_code"] == "empty_results"
