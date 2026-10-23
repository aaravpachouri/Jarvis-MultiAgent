"""Scout: executable web-search specialist using the standard library only.

Scout retrieves public search results and returns source URLs with short snippets.
It does not open pages, follow instructions found in results, or claim to verify
facts beyond the metadata returned by the search provider.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen
from typing import Any


DEFAULT_RESULT_COUNT = 5
MAX_RESULT_COUNT = 10
SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class _DuckDuckGoParser(HTMLParser):
    """Extract result anchors and snippets without third-party HTML libraries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self.snippets: list[str] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._href = ""

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        return set(dict(attrs).get("class", "").split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)
        attrs_map = dict(attrs)
        if tag == "a" and "result__a" in classes:
            self._capture = "title"
            self._buffer = []
            self._href = attrs_map.get("href") or ""
        elif tag in {"a", "div", "td"} and "result__snippet" in classes:
            self._capture = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        if self._capture == "title" and tag == "a":
            title = " ".join("".join(self._buffer).split())
            if title and self._href:
                self.results.append({"title": title, "url": _unwrap_result_url(self._href)})
            self._capture = None
            self._buffer = []
            self._href = ""
        elif self._capture == "snippet" and tag in {"a", "div", "td"}:
            snippet = " ".join("".join(self._buffer).split())
            if snippet:
                self.snippets.append(snippet)
            self._capture = None
            self._buffer = []


def _unwrap_result_url(href: str) -> str:
    """Convert DuckDuckGo redirect links to their destination URL when possible."""
    href = href.strip()
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        if target:
            return target
    return href


def _normalise_query(prompt: str) -> str:
    query = " ".join(str(prompt or "").strip().split())
    # Strip the wake word first, then strip one search command prefix. The
    # command prefix must be processed after the wake word for "Jarvis, search…".
    query = re.sub(r"^(?:hey\s+)?jarvis[,:.!?\s-]+", "", query, flags=re.IGNORECASE).strip()
    if query.casefold().strip(" .,!?") in {
        "search", "search for", "search the web", "search the web for",
        "search online", "search online for", "web search", "web search for",
        "find online", "find on the web", "look up", "google",
    }:
        return ""
    # Accept punctuation after command prefixes, including Scout's delegated
    # form: "Search the web for: <query>". Match longer prefixes first and
    # strip only the separator, not text belonging to the actual query.
    prefixes = (
        "search the web for", "search online for", "web search for",
        "find on the web", "search online", "search the web",
        "search for", "find online", "look up", "google", "search",
    )
    lowered = query.casefold()
    for prefix in prefixes:
        if lowered.startswith(prefix) and (
            len(query) == len(prefix)
            or query[len(prefix)].isspace()
            or query[len(prefix)] in ":,-"
        ):
            query = query[len(prefix):].lstrip(" \t\r\n:,-")
            break
    return query.strip(" \t\r\n.,!?;")


def _fetch_html(query: str, *, timeout: float) -> str:
    endpoint = os.getenv("JARVIS_SEARCH_ENDPOINT", SEARCH_ENDPOINT).strip() or SEARCH_ENDPOINT
    separator = "&" if "?" in endpoint else "?"
    url = f"{endpoint}{separator}q={quote_plus(query)}"
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _fetch_brave_results(query: str, *, timeout: float, limit: int) -> list[dict[str, str]]:
    """Fetch structured results from Brave Search API without third-party dependencies."""
    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("BRAVE_SEARCH_API_KEY is not configured.")
    from urllib.parse import urlencode

    url = f"{BRAVE_SEARCH_ENDPOINT}?{urlencode({'q': query, 'count': limit})}"
    request = Request(
        url,
        headers={
            "X-Subscription-Token": api_key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    web = payload.get("web", {}) if isinstance(payload, dict) else {}
    items = web.get("results", []) if isinstance(web, dict) else []
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url_value = str(item.get("url", "")).strip()
        if not url_value.startswith(("https://", "http://")) or url_value in seen:
            continue
        seen.add(url_value)
        domain = urlparse(url_value).netloc.removeprefix("www.")
        results.append({
            "title": str(item.get("title", "")).strip() or url_value,
            "url": url_value,
            "domain": domain,
            "snippet": " ".join(str(item.get("description", "")).split()),
        })
        if len(results) >= limit:
            break
    return results


def _fetch_tavily_results(
    query: str, *, timeout: float, limit: int, include_domains: list[str] | None = None
) -> list[dict[str, str]]:
    """Fetch structured results from Tavily Search API without third-party dependencies."""
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is not configured.")
    payload_data: dict[str, Any] = {
        "api_key": api_key,
        "query": query,
        "max_results": limit,
        "search_depth": "basic",
        "include_answer": False,
    }
    cleaned_domains = sorted({str(domain).strip().casefold() for domain in (include_domains or []) if str(domain).strip()})
    if cleaned_domains:
        payload_data["include_domains"] = cleaned_domains
    payload = json.dumps(payload_data).encode("utf-8")
    request = Request(
        TAVILY_SEARCH_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    items = data.get("results", []) if isinstance(data, dict) else []
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url_value = str(item.get("url", "")).strip()
        if not url_value.startswith(("https://", "http://")) or url_value in seen:
            continue
        seen.add(url_value)
        domain = urlparse(url_value).netloc.removeprefix("www.")
        results.append({
            "title": str(item.get("title", "")).strip() or url_value,
            "url": url_value,
            "domain": domain,
            "snippet": " ".join(str(item.get("content", "")).split()),
        })
        if len(results) >= limit:
            break
    return results


def _parse_results(html: str, limit: int) -> list[dict[str, str]]:
    parser = _DuckDuckGoParser()
    parser.feed(html)
    parsed: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(parser.results):
        url = item["url"]
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        parsed_url = urlparse(url)
        # DuckDuckGo's /y.js URLs are sponsored-ad click trackers, not useful
        # source pages. Never present them as normal research results.
        if parsed_url.netloc.casefold().endswith("duckduckgo.com") and parsed_url.path.startswith("/y.js"):
            continue
        seen.add(url)
        domain = parsed_url.netloc.removeprefix("www.")
        snippet = parser.snippets[index] if index < len(parser.snippets) else ""
        parsed.append({
            "title": item["title"],
            "url": url,
            "domain": domain,
            "snippet": snippet,
        })
        if len(parsed) >= limit:
            break
    return parsed


class SearchAgent:
    """Search the public web and return concise, source-linked results."""

    def __init__(self, *, timeout_seconds: float | None = None, result_count: int | None = None) -> None:
        self.timeout_seconds = timeout_seconds or float(os.getenv("JARVIS_SEARCH_TIMEOUT", "12"))
        self.result_count = result_count or int(os.getenv("JARVIS_SEARCH_RESULTS", str(DEFAULT_RESULT_COUNT)))
        if self.timeout_seconds <= 0:
            raise ValueError("Search timeout must be positive.")
        self.result_count = max(1, min(MAX_RESULT_COUNT, self.result_count))

    async def __call__(self, task: Any, context: Any) -> dict[str, Any]:
        query = _normalise_query(task.prompt)
        if not query:
            return {
                "summary": "Tell me what you want me to search for, and I'll look for source-linked results.",
                "query": "",
                "results": [],
                "retrieved_at": _utc_now(),
            }

        # An explicit provider setting always wins. When no provider is set,
        # prefer Brave if its key is configured, then Tavily, then DuckDuckGo.
        # This keeps provider choice deterministic when multiple API keys are
        # present (for example, in a developer environment running tests).
        provider = os.getenv("JARVIS_SEARCH_PROVIDER", "").strip().casefold()
        if provider:
            selected_provider = provider
        elif os.getenv("BRAVE_SEARCH_API_KEY", "").strip():
            selected_provider = "brave"
        elif os.getenv("TAVILY_API_KEY", "").strip():
            selected_provider = "tavily"
        else:
            selected_provider = "duckduckgo"
        try:
            if selected_provider == "tavily":
                requested_domains = re.findall(r"\bsite:([A-Za-z0-9.-]+)", query, re.IGNORECASE)
                provider_query = re.sub(r"\s+site:[A-Za-z0-9./_-]+", "", query, flags=re.IGNORECASE).strip()
                tavily_kwargs: dict[str, Any] = {"timeout": self.timeout_seconds, "limit": self.result_count}
                if requested_domains:
                    tavily_kwargs["include_domains"] = requested_domains
                results = await asyncio.to_thread(
                    _fetch_tavily_results, provider_query, **tavily_kwargs
                )
                provider_name = "Tavily Search API"
            elif selected_provider == "brave":
                results = await asyncio.to_thread(
                    _fetch_brave_results, query, timeout=self.timeout_seconds, limit=self.result_count
                )
                provider_name = "Brave Search API"
            elif selected_provider in {"duckduckgo", "ddg", ""}:
                html = await asyncio.to_thread(_fetch_html, query, timeout=self.timeout_seconds)
                results = _parse_results(html, self.result_count)
                provider_name = "DuckDuckGo HTML search"
            else:
                return self._failure(query, f"Unknown JARVIS_SEARCH_PROVIDER '{selected_provider}'. Choose tavily, brave, or duckduckgo.")
        except HTTPError as exc:
            return self._failure(query, f"The search provider returned HTTP {exc.code}. Check the provider configuration and API quota.")
        except (TimeoutError, URLError, OSError) as exc:
            detail = getattr(exc, "reason", None) or str(exc)
            return self._failure(query, f"The search request failed: {detail}")
        except Exception as exc:
            return self._failure(query, f"Search failed ({type(exc).__name__}): {exc}")

        retrieved_at = _utc_now()
        if not results:
            return {
                "summary": (
                    f"I searched for '{query}', but the provider returned no parseable results. "
                    "It may have returned a challenge page. Configure TAVILY_API_KEY and set "
                    "JARVIS_SEARCH_PROVIDER=tavily to use structured JSON search results."
                ),
                "query": query,
                "results": [],
                "retrieved_at": retrieved_at,
            }

        lines = [f"Scout search results for: {query}", f"Retrieved: {retrieved_at}"]
        for index, result in enumerate(results, start=1):
            lines.append(f"\n{index}. {result['title']} ({result['domain']})")
            lines.append(f"   {result['url']}")
            if result["snippet"]:
                lines.append(f"   {result['snippet']}")
        lines.append("\nThese are search results, not independently verified claims. Open primary sources before relying on details.")
        return {
            "summary": "\n".join(lines),
            "query": query,
            "results": results,
            "retrieved_at": retrieved_at,
            "provider": provider_name,
        }

    @staticmethod
    def _failure(query: str, reason: str) -> dict[str, Any]:
        return {
            "summary": f"Scout couldn't retrieve search results for '{query}'. {reason} No results have been fabricated.",
            "query": query,
            "results": [],
            "retrieved_at": _utc_now(),
            "error": reason,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_handler() -> SearchAgent:
    return SearchAgent()
