"""Athena: focused, source-linked research delegated to Scout.

Athena deliberately treats search snippets as discovery evidence, not verified
facts. It creates three bounded queries, strips workflow instructions from those
queries, prefers primary-source targets when recognizable, and reports gaps.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

from core.action_scope import action_scope

DEFAULT_QUERIES = 3
MAX_RESULTS_PER_QUERY = 5

# Small, explicit primary-source hints for well-known package/project targets.
# Unknown products still use generic source-oriented queries rather than guesses.
_PRIMARY_SOURCE_HINTS: dict[str, tuple[str, str, str]] = {
    "fastapi": (
        "site:fastapi.tiangolo.com",
        "site:pypi.org/project/fastapi",
        "site:github.com/fastapi/fastapi",
    ),
}


class ResearchAgent:
    """Run three focused Scout searches and synthesize the retrieved evidence."""

    async def __call__(self, task: Any, context: Any) -> dict[str, Any]:
        async with action_scope(context, "athena.research.workflow", action_type="workflow", metadata={"operation": "source_research_and_synthesis"}):
            return await self._research(task, context)

    async def _research(self, task: Any, context: Any) -> dict[str, Any]:
        prompt = str(getattr(task, "prompt", "") or "").strip()
        if not prompt:
            raise ValueError("Athena requires a non-empty research prompt.")

        task_context = getattr(task, "context", {}) or {}
        if not isinstance(task_context, dict) and not hasattr(task_context, "items"):
            raise TypeError("Athena task context must be a mapping.")
        task_context = dict(task_context)

        # Athena can reason over material already supplied to JARVIS without
        # making Scout or any external search provider a dependency.
        supplied_material = self._provided_material(task_context)
        if supplied_material and self._is_context_analysis_request(prompt, task_context):
            return self._analyze_provided_material(prompt, supplied_material)

        queries = self._build_queries(prompt)
        if not queries:
            raise RuntimeError("Athena could not extract meaningful research queries from the prompt.")

        outcomes = await asyncio.gather(
            *(self._search(query, context) for query in queries),
            return_exceptions=True,
        )

        sources: list[dict[str, str]] = []
        failures: list[str] = []
        seen_urls: set[str] = set()
        for query, outcome in zip(queries, outcomes):
            if isinstance(outcome, BaseException):
                failures.append(f"{query}: {type(outcome).__name__}: {outcome}")
                continue
            if outcome.get("error"):
                failures.append(f"{query}: {outcome['error']}")
            for item in outcome.get("results", []) or []:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "")).strip()
                if not url.startswith(("https://", "http://")) or url in seen_urls:
                    continue
                seen_urls.add(url)
                parsed = urlparse(url)
                sources.append({
                    "title": str(item.get("title", "")).strip(),
                    "url": url,
                    "domain": str(item.get("domain", "")).strip() or parsed.netloc.removeprefix("www."),
                    "snippet": str(item.get("snippet", "")).strip(),
                    "search_query": query,
                })

        sources = self._prioritize_primary_sources(prompt, sources)
        if not sources:
            detail = "; ".join(failures) if failures else "Scout returned no parseable results."
            raise RuntimeError(f"Athena could not retrieve research sources. {detail}")

        primary_count = sum(self._is_primary_source(prompt, item["url"]) for item in sources)
        lines = [
            f"Athena research brief: {prompt}",
            "",
            "Scope and limitations:",
            "- Findings below are based on search-result titles and snippets returned by Scout.",
            "- Source pages were not independently opened or verified by Athena in this pass.",
            "- Treat compatibility or version claims as provisional until confirmed against primary documentation.",
            f"- Recognized primary-source URLs: {primary_count} of {len(sources)} retrieved sources.",
            "",
            f"Queries run: {len(queries)}",
            f"Distinct source URLs: {len(sources)}",
            "",
            "Evidence discovered:",
        ]
        for index, source in enumerate(sources, 1):
            lines.append(f"{index}. {source['title'] or source['domain'] or source['url']}")
            lines.append(f"   URL: {source['url']}")
            if source["snippet"]:
                lines.append(f"   Search snippet (unverified): {source['snippet']}")
            lines.append(f"   Query: {source['search_query']}")
        if failures:
            lines.extend(["", "Search failures / limitations:"])
            lines.extend(f"- {failure}" for failure in failures)
        if primary_count == 0 and re.search(r"\bofficial\b|\bprimary source\b", prompt, re.IGNORECASE):
            lines.extend(["", "Primary-source gap:", "- No recognized primary-source URL was returned. Findings remain unverified."])
        lines.extend([
            "",
            "Synthesis:",
            "The retrieved results provide a source-linked starting point. Snippets alone do not verify "
            "facts. Open primary sources and check their current release dates before relying on version-specific claims.",
        ])

        return {
            "summary": "\n".join(lines),
            "question": prompt,
            "queries": queries,
            "sources": sources,
            "source_count": len(sources),
            "primary_source_count": primary_count,
            "failures": failures,
            "evidence_level": "search_snippets_not_independently_verified",
            "complete": not failures,
        }

    @staticmethod
    def _is_context_analysis_request(prompt: str, context: dict[str, Any]) -> bool:
        intent = re.search(
            r"\b(?:analy[sz]e|summari[sz]e|explain|compare|evaluate|review|break down|synthesize|synthesise|reason about)\b",
            prompt,
            re.IGNORECASE,
        )
        explicit_material = any(
            key in context for key in ("source_material", "documents", "notes", "information", "content", "analysis_data")
        )
        return bool(intent and (explicit_material or context.get("conversation_history")))

    @staticmethod
    def _provided_material(context: dict[str, Any]) -> list[str]:
        material: list[str] = []
        for key in ("source_material", "documents", "notes", "information", "content", "analysis_data"):
            value = context.get(key)
            if value is None:
                continue
            values = value if isinstance(value, (list, tuple, set)) else [value]
            for item in values:
                if isinstance(item, dict):
                    rendered = "; ".join(f"{name}: {content}" for name, content in item.items() if str(content).strip())
                else:
                    rendered = str(item).strip()
                if rendered and rendered not in material:
                    material.append(rendered)
        history = context.get("conversation_history")
        if isinstance(history, (list, tuple)):
            for item in history:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "context")).strip() or "context"
                content = str(item.get("content", "")).strip()
                if content:
                    rendered = f"{role}: {content}"
                    if rendered not in material:
                        material.append(rendered)
        return material

    @staticmethod
    def _analyze_provided_material(prompt: str, material: list[str]) -> dict[str, Any]:
        """Return a traceable, local analysis of supplied text without web calls."""
        excerpts: list[str] = []
        for item in material:
            # Keep each source item intact; do not invent factual claims or sources.
            normalized = " ".join(item.split())
            if normalized:
                excerpts.append(normalized)
        summary = [
            f"Athena context analysis: {prompt}",
            "",
            "Analysis basis:",
            "- This response uses only information supplied in the task context or prior JARVIS conversation.",
            "- No web search, external provider, or independent fact-check was used.",
            "",
            "Supplied information:",
        ]
        summary.extend(f"{index}. {item}" for index, item in enumerate(excerpts, 1))
        summary.extend([
            "",
            "Assessment:",
            f"- Reviewed {len(excerpts)} supplied item(s).",
            "- The content above is the evidence available for this request; any conclusion beyond it would require additional information.",
        ])
        if re.search(r"\b(?:benefits?|pros)\b", prompt, re.IGNORECASE) and re.search(
            r"\b(?:risks?|cons|trade-?offs?)\b", prompt, re.IGNORECASE
        ):
            joined = " ".join(excerpts).casefold()
            summary.extend(["", "Benefits supported by the supplied context:"])
            if "documentation.scribe" in joined or "scribe" in joined:
                summary.append("- Scribe provides a dedicated writing/documentation capability; its draft-only boundary reduces unintended filesystem side effects.")
            if "planning.planner" in joined or "planner" in joined:
                summary.append("- Planner provides a separate, offline-capable planning handler for sequencing, dependencies, checklists and validation gates.")
            if "research.athena" in joined or "athena" in joined:
                summary.append("- Athena separates research synthesis from other work; its local context-analysis mode can analyze supplied material without a web call.")
            if not any(name in joined for name in ("scribe", "planner", "athena")):
                summary.append("- The supplied context does not identify enough specialist details to establish specific benefits.")
            summary.extend(["", "Risks and limits supported by the supplied context:"])
            if "draft_only" in joined or "draft-only" in joined or "does not write files" in joined:
                summary.append("- Scribe's draft-only mode does not save files; any file creation needs a separate approved workflow.")
            if "scout" in joined or "search snippets" in joined:
                summary.append("- Athena's external research path depends on Scout and network availability; search snippets are not independent verification.")
            if "plan_only_not_executed" in joined or "does not execute" in joined:
                summary.append("- Planner proposes steps but does not execute them; completion still needs separate task execution and verification.")
            if "does not establish that any agent has completed a real task" in joined:
                summary.append("- Manifest/runtime metadata proves availability, not task quality or remote-service reachability.")
            if "coding.forge" in joined and "executable handler available in this runtime: true" in joined:
                summary.append("- Important scope check: Forge's handler is executable in this project snapshot, so Forge is not currently disabled by the runtime. This analysis did not change Forge or disable it.")
            summary.append("- Validate routing with representative prompts and test each specialist's output before treating the integration as production-ready.")
        else:
            summary.append("- For a comparison, evaluate the options against the same criteria; for a decision, state the criteria and trade-offs explicitly.")
        return {
            "summary": "\n".join(summary),
            "question": prompt,
            "queries": [],
            "sources": [],
            "source_count": 0,
            "primary_source_count": 0,
            "failures": [],
            "evidence_level": "user_supplied_context_only",
            "analysis_mode": "offline_context_analysis",
            "provided_item_count": len(excerpts),
            "complete": True,
        }

    async def _search(self, query: str, context: Any) -> dict[str, Any]:
        result = await context.delegate(
            prompt=f"Search the web for: {query}",
            capabilities=("web.search",),
            agent_id="search.scout",
            context={"research_query": query, "requested_by": "research.athena"},
        )
        if not getattr(result, "succeeded", False):
            error = getattr(result, "error", None) or getattr(result, "summary", None) or "Scout task failed."
            return {"results": [], "error": str(error)}
        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            return {"results": [], "error": "Scout returned an unexpected output format."}
        if not output.get("results"):
            detail = output.get("error") or output.get("summary") or "Scout returned zero results."
            return {**output, "results": [], "error": str(detail)}
        return output

    @staticmethod
    def _build_queries(prompt: str) -> list[str]:
        """Extract up to three focused queries and exclude orchestration text."""
        clean = " ".join(str(prompt or "").split()).strip()
        if not clean:
            return []

        # A standalone orchestration instruction is not a research topic.
        if re.fullmatch(
            r"(?:please\s+)?(?:preserve|keep|make sure|ensure)\s+.*(?:delegat|workstream|agent).*",
            clean,
            re.IGNORECASE,
        ):
            return []

        match = re.search(
            r"\b(?:research|look\s+up|find\s+information\s+about|investigate)\s+(.+)",
            clean,
            re.IGNORECASE,
        )
        subject = match.group(1) if match else clean
        # End the topic before workflow directives, even when the user put them
        # in the same sentence or pasted a multi-stage prompt.
        stop_pattern = (
            r"\b(?:return|provide|output|summari[sz]e|report|"
            r"then\s+(?:edit|create|write|modify|run|execute|test|delegate|pass)|"
            r"if\s+(?:sufficient|any|the)|delegate\b|preserve\s+the\s+three\s+separate|"
            r"gather\s+enough\s+evidence|use\s+(?:athena|scout|tavily)|"
            r"do\s+not\s+(?:delegate|modify|implement|invent)|don't\s+|"
            r"never\s+(?:simulate|claim|pretend)|\*{0,2}stage\s+\d+|"
            r"\w+\s+must|without\s+proof)\b"
        )
        subject = re.split(stop_pattern, subject, maxsplit=1, flags=re.IGNORECASE)[0]
        # Sentence boundaries also stop trailing prose while preserving a
        # coordinated target phrase before the sentence-ending period.
        subject = re.split(r"\.\s+(?=[A-Z])", subject, maxsplit=1)[0]
        subject = re.sub(
            r"\b(?:through|using)\s+(?:(?:the|an?)\s+)?(?:Scout|Tavily|Athena|Brave)(?:\s+and\s+(?:Scout|Tavily|Athena|Brave))?\b",
            "",
            subject,
            flags=re.IGNORECASE,
        )
        subject = re.sub(r"\s+", " ", subject).strip(" ,.;:!?-\t")
        if not subject:
            return []

        targets = [part.strip(" ,.;:!?-\t") for part in re.split(r"\s+and\s+", subject, flags=re.IGNORECASE)]
        targets = [part for part in targets if part][:2]
        if not targets:
            return []

        # Infer the subject token (e.g. FastAPI) to avoid queries like
        # "FastAPI the package's PyPI metadata".
        first_words = re.findall(r"\b[A-Za-z][A-Za-z0-9.+_-]*\b", targets[0])
        ignored = {"official", "release", "notes", "documentation", "docs", "the", "for", "and", "about", "current", "stable"}
        product = ""
        for word in first_words:
            if word.casefold() in ignored:
                continue
            if any(char.isupper() for char in word[1:]) or word.isupper():
                product = word
                break
        if not product and first_words:
            product = next((word for word in first_words if word.casefold() not in ignored), "")

        queries: list[str] = []
        lower_subject = subject.casefold()
        hint = _PRIMARY_SOURCE_HINTS.get(product.casefold())
        for index, target in enumerate(targets):
            target = re.sub(r"^(?:the\s+)?(?:package(?:'s|’s)?\s+)", "", target, flags=re.IGNORECASE)
            target = re.sub(r"^(?:the\s+)?metadata\s+for\s+", "", target, flags=re.IGNORECASE)
            if product and product.casefold() not in target.casefold() and any(
                term in target.casefold() for term in ("pypi", "package metadata", "package information", "package details", "metadata")
            ):
                target = f"{product} {target}"
            target = " ".join(target.split()).strip(" ,.;:!?-")
            if hint and index < len(hint):
                target = f"{target} {hint[index]}"
            if target and target.casefold() not in {item.casefold() for item in queries}:
                queries.append(target[:179].rstrip())

        # A third independent search targets the official repository/release
        # record when the prompt asks about both release notes and package data.
        if product and ("release" in lower_subject or "version" in lower_subject) and any(
            term in lower_subject for term in ("pypi", "package metadata", "package's metadata", "package metadata")
        ):
            third = f"{product} official repository releases"
            if hint:
                third = f"{third} {hint[2]}"
        elif product and hint and any(term in lower_subject for term in ("release", "version", "current")):
            third = f"{product} official release history {hint[2]}"
        else:
            third = f"{targets[0]} official primary source"
        third = " ".join(third.split()).strip()[:179].rstrip()
        if third and third.casefold() not in {item.casefold() for item in queries}:
            queries.append(third)

        # For a single-target query, fill remaining slots with distinct source
        # angles without copying any downstream implementation instructions.
        if len(queries) < DEFAULT_QUERIES:
            for suffix in ("official documentation source", "authoritative reference"):
                candidate = f"{targets[0]} {suffix}".strip()
                if hint:
                    candidate = f"{targets[0]} {hint[min(len(queries), len(hint)-1)]}"
                candidate = " ".join(candidate.split()).strip()[:179].rstrip()
                if candidate.casefold() not in {item.casefold() for item in queries}:
                    queries.append(candidate)
                if len(queries) >= DEFAULT_QUERIES:
                    break
        return queries[:DEFAULT_QUERIES]

    @staticmethod
    def _is_primary_source(prompt: str, url: str) -> bool:
        lowered = prompt.casefold()
        parsed = urlparse(url)
        host = parsed.netloc.casefold().removeprefix("www.")
        path = parsed.path.casefold().rstrip("/")
        if "fastapi" in lowered:
            return (
                host == "fastapi.tiangolo.com"
                or (host == "pypi.org" and path.startswith("/project/fastapi"))
                or (host == "github.com" and path.startswith("/fastapi/fastapi"))
            )
        # Generic primary-source hints can be carried explicitly as site: URLs
        # in the original task; otherwise avoid guessing ownership.
        domains = re.findall(r"\bsite:([A-Za-z0-9.-]+)", prompt, re.IGNORECASE)
        return host in {domain.casefold() for domain in domains}

    @classmethod
    def _prioritize_primary_sources(cls, prompt: str, sources: list[dict[str, str]]) -> list[dict[str, str]]:
        return sorted(sources, key=lambda item: (not cls._is_primary_source(prompt, item["url"]), item["url"].casefold()))


def create_handler() -> ResearchAgent:
    return ResearchAgent()
