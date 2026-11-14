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
    "tavily": (
        "Tavily Search API authentication and endpoint site:docs.tavily.com/documentation/api-reference/endpoint/search",
        "Tavily Search API request parameters and response schema site:docs.tavily.com/documentation/api-reference/endpoint/search",
        "Tavily Search API best practices and extraction behavior site:docs.tavily.com/documentation/best-practices/best-practices-search",
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

        model_targets = self._extract_model_targets(prompt)
        model_comparison_requested = len(model_targets) >= 2 and bool(
            re.search(r"\bcompare\b", prompt, re.IGNORECASE)
        )

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
        successful_queries: list[str] = []
        query_provenance: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for query, outcome in zip(queries, outcomes):
            if isinstance(outcome, BaseException):
                detail = f"{type(outcome).__name__}: {outcome}"
                failures.append(f"{query}: {detail}")
                query_provenance.append({"query": query, "status": "failed", "error": detail})
                continue
            if outcome.get("error"):
                failures.append(f"{query}: {outcome['error']}")
            query_results = 0
            for item in outcome.get("results", []) or []:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "")).strip()
                if not url.startswith(("https://", "http://")):
                    continue
                # Count each query's valid retrieval before cross-query
                # deduplication. Reusing a source URL does not make a later
                # successful query a failed retrieval.
                query_results += 1
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                parsed = urlparse(url)
                sources.append({
                    "title": str(item.get("title", "")).strip(),
                    "url": url,
                    "domain": str(item.get("domain", "")).strip() or parsed.netloc.removeprefix("www."),
                    "snippet": str(item.get("snippet", "")).strip(),
                    "page_content": str(item.get("page_content", "")).strip(),
                    "search_query": query,
                    "provider": str(item.get("provider") or outcome.get("provider", "")).strip() or "unspecified",
                    "retrieved_at": str(outcome.get("retrieved_at", "")).strip() or None,
                    "source_type": str(item.get("source_type", "")).strip() or "web_search_result",
                    "publication_date": str(item.get("publication_date", "")).strip(),
                })
            retrieval_status = str(outcome.get("retrieval_status") or ("partial" if outcome.get("partial") else "complete"))
            query_is_partial = bool(outcome.get("partial")) or retrieval_status == "partial" or bool(outcome.get("error"))
            if query_results and not query_is_partial:
                successful_queries.append(query)
            query_status = (
                "partial" if query_results and query_is_partial
                else "failed" if not query_results
                else "complete"
            )
            query_provenance.append({
                "query": query,
                "status": query_status,
                "provider": str(outcome.get("provider", "")).strip(),
                "providers_attempted": list(outcome.get("providers_attempted", []) or []),
                "provider_failures": list(outcome.get("provider_failures", []) or []),
                "result_count": query_results,
                "retrieval_status": retrieval_status,
                "delegated_agent_id": str(outcome.get("delegated_agent_id", "search.scout")),
                "delegation_task_id": outcome.get("delegation_task_id"),
                "requested_by": str(outcome.get("requested_by", "research.athena")),
            })

        sources = self._prioritize_primary_sources(prompt, sources)
        primary_count = sum(self._is_primary_source(prompt, item["url"]) for item in sources)
        has_partial_failure = bool(failures) or len(successful_queries) < len(queries)
        if not sources:
            detail = "; ".join(failures) if failures else "Scout returned no parseable results."
            return {
                "summary": "\n".join([
                    f"Athena research attempt: {prompt}",
                    "",
                    "Status: INSUFFICIENT EVIDENCE",
                    "No usable source URLs were returned by Scout.",
                    "No findings have been inferred or fabricated.",
                    "",
                    "Queries attempted:",
                    *[f"- {query}" for query in queries],
                    "",
                    "Search failures / limitations:",
                    f"- {detail}",
                    "",
                    "Next step: restore a relevant search provider or quota, then rerun this research request.",
                ]),
                "question": prompt,
                "queries": queries,
                "query_provenance": query_provenance,
                "sources": [],
                "source_count": 0,
                "primary_source_count": 0,
                "failures": failures or ["Scout returned no parseable results."],
                "evidence_level": "no_retrieved_evidence",
                "research_status": "insufficient_evidence",
                "requested_model_targets": model_targets,
                "model_target_coverage": {target: [] for target in model_targets},
                "model_target_coverage_complete": not model_comparison_requested,
                "complete": False,
            }
        api_comparison_requested = "tavily" in prompt.casefold() and "search api" in prompt.casefold()
        model_comparison_requested = model_comparison_requested or (
            len(model_targets) >= 2 and bool(re.search(r"\bcompare\b", prompt, re.IGNORECASE))
        )
        evidence_sources = [
            item for item in sources
            if self._is_relevant_api_source(item["url"])
        ] if api_comparison_requested else sources
        if api_comparison_requested:
            comparison_matrix = self._build_technical_evidence_matrix(evidence_sources)
            required_dimensions = tuple(comparison_matrix)
        elif model_comparison_requested:
            comparison_matrix = self._build_model_evidence_matrix(evidence_sources)
            required_dimensions = tuple(comparison_matrix)
        else:
            comparison_matrix = {}
            required_dimensions = ()

        model_target_coverage = {
            target: [
                item["url"] for item in sources
                if self._is_official_model_source(target, item["url"])
            ]
            for target in model_targets
        } if model_comparison_requested else {}
        model_target_coverage_complete = (
            all(model_target_coverage.values()) if model_comparison_requested else True
        )

        # Official-source gates count substantive extracted content from recognized
        # primary pages, not just URLs or search snippets.
        substantive_primary_urls = {
            item["url"] for item in sources
            if (
                self._is_relevant_api_source(item["url"])
                if api_comparison_requested
                else self._is_primary_source(prompt, item["url"])
            )
            and self._has_substantive_page_content(item.get("page_content", ""))
        }
        extracted_count = len(substantive_primary_urls)
        official_required = bool(re.search(r"\bofficial\b|\bprimary source\b", prompt, re.IGNORECASE))
        official_threshold_met = not official_required or primary_count >= 3
        extracted_threshold_met = not official_required or extracted_count >= 3
        covered_dimensions = {label for label, entries in comparison_matrix.items() if entries}
        technical_coverage_met = (
            not required_dimensions
            or set(required_dimensions).issubset(covered_dimensions)
        )
        verification_level = "provider_extracted_page_content" if extracted_count else "search_snippets_not_independently_verified"
        lines = [
            f"Athena research brief: {prompt}",
            "",
            "Scope and limitations:",
            "- Findings below are based on search-result metadata and any provider-extracted page content returned by Scout.",
            (f"- Tavily returned extracted page content for {extracted_count} source(s); this is provider-extracted content, not a browser-level independent verification."
             if extracted_count else "- Source pages were not independently opened or verified by Athena in this pass."),
            "- Treat compatibility or version claims as provisional unless supported by extracted primary-source content.",
            f"- Recognized official-source URLs: {primary_count} of {len(sources)} retrieved sources.",
            *([f"- API-relevant official documentation URLs: {len({item['url'] for item in evidence_sources if self._is_relevant_api_source(item['url'])})}."] if api_comparison_requested else []),
            f"- Official-source URL threshold (3): {'met' if official_threshold_met else 'not met'}.",
            f"- Substantive extracted primary-content threshold (3): {'met' if extracted_threshold_met else 'not met'}.",
            (f"- Required technical dimensions: {len(covered_dimensions)} of {len(required_dimensions)} covered."
             if required_dimensions else "- Required technical dimensions: not applicable for this research type."),
            *([f"- Official source coverage for requested models: {'complete' if model_target_coverage_complete else 'incomplete'}."]
              if model_comparison_requested else []),
            "",
            f"Queries run: {len(queries)}",
            f"Queries with usable, non-failed retrieval: {len(successful_queries)}",
            f"Distinct source URLs: {len(sources)}",
            "",
            "Scout delegation and retrieval provenance:",
            *[
                f"- {item.get('delegated_agent_id', 'search.scout')} via {item.get('provider') or 'unspecified provider'}: {item.get('status')} ({item.get('result_count', 0)} results); query: {item.get('query')}"
                for item in query_provenance
            ],
            "",
            "Evidence discovered:",
        ]
        for index, source in enumerate(sources, 1):
            lines.append(f"{index}. {source['title'] or source['domain'] or source['url']}")
            lines.append(f"   URL: {source['url']}")
            if source["snippet"]:
                lines.append(f"   Search snippet (discovery only): {source['snippet'][:1200]}")
            if source.get("page_content"):
                lines.append(f"   Extracted page content (provider-returned; review source directly before relying): {source['page_content'][:2400]}")
            lines.append(f"   Query: {source['search_query']}")
        if has_partial_failure:
            lines.extend(["", "Search coverage: PARTIAL — one or more queries/providers failed or returned partial results."])
        if failures:
            lines.extend(["", "Search failures / limitations:"])
            lines.extend(f"- {failure}" for failure in failures)
        if primary_count == 0 and re.search(r"\bofficial\b|\bprimary source\b", prompt, re.IGNORECASE):
            lines.extend(["", "Primary-source gap:", "- No recognized primary-source URL was returned. Findings remain unverified."])
        lines.extend([
            "",
            "Technical evidence matrix:",
            *[
                f"- {label}: " + (
                    " | ".join(f"{entry['excerpt']} [Source: {entry['url']}]" for entry in entries)
                    if entries else "Not verified from extracted primary-source content."
                )
                for label, entries in comparison_matrix.items()
            ],
            "",
            "Research coverage:",
            f"- Sources with substantive extracted primary content: {extracted_count} of {primary_count} recognized primary sources.",
            (f"- Technical dimensions supported by extracted primary content: {len(covered_dimensions)} of {len(required_dimensions)}."
             if required_dimensions else "- No fixed technical-dimension matrix applies to this request."),
            f"- Official-source requirement: {'satisfied' if official_threshold_met else 'not satisfied; do not treat this as a completed official-source review'}.",
            f"- Source-content verification requirement: {'satisfied' if extracted_threshold_met else 'not satisfied; snippets alone cannot complete the research task'}.",
            *([f"- Requested model target coverage: {'satisfied' if model_target_coverage_complete else 'not satisfied; one or more requested models lack a recognized official source'}."]
              if model_comparison_requested else []),
            "- Search snippets are discovery leads, not evidence for technical conclusions.",
            "",
            ("- Technical comparison is incomplete: one or more requested dimensions lack source-linked extracted evidence."
             if required_dimensions and not technical_coverage_met
             else "- All required technical dimensions have at least one source-linked extracted excerpt."
             if required_dimensions
             else "- No technical-dimension coverage requirement was defined for this request."),
            "",
            "Synthesis boundary:",
            "Athena preserves source content and provenance here. It does not claim a technical comparison is verified unless the relevant details are present in extracted primary-source content. The original publisher pages should still be checked before implementation.",
        ])

        return {
            "summary": "\n".join(lines),
            "question": prompt,
            "queries": queries,
            "query_provenance": query_provenance,
            "sources": sources,
            "source_count": len(sources),
            "primary_source_count": primary_count,
            "failures": failures,
            "evidence_level": verification_level,
            "extracted_content_count": extracted_count,
            "official_source_threshold_met": official_threshold_met,
            "technical_evidence_matrix": comparison_matrix,
            "requested_model_targets": model_targets,
            "model_target_coverage": model_target_coverage,
            "model_target_coverage_complete": model_target_coverage_complete,
            "technical_dimensions_required": list(required_dimensions),
            "research_status": (
                "partial" if has_partial_failure
                else "insufficient_primary_sources" if not official_threshold_met
                else "insufficient_extracted_content" if not extracted_threshold_met
                else "insufficient_model_target_coverage" if not model_target_coverage_complete
                else "insufficient_technical_coverage" if not technical_coverage_met
                else "retrieval_complete_with_extracted_content" if extracted_count
                else "retrieval_complete_unverified"
            ),
            "extracted_content_threshold_met": extracted_threshold_met,
            "technical_coverage_complete": technical_coverage_met,
            "technical_dimensions_covered": sorted(covered_dimensions),
            "complete": (
                not has_partial_failure and bool(sources) and official_threshold_met
                and extracted_threshold_met and model_target_coverage_complete
                and technical_coverage_met
            ),
        }

    @staticmethod
    def _is_relevant_api_source(url: str) -> bool:
        """Identify Tavily's official pages that directly document the Search API."""
        parsed = urlparse(str(url or ""))
        if parsed.netloc.casefold().removeprefix("www.") != "docs.tavily.com":
            return False
        path = parsed.path.rstrip("/").casefold()
        return path in {
            "/documentation/api-headers",
            "/documentation/api-reference/endpoint/search",
            "/documentation/best-practices/best-practices-search",
            "/sdk/python/reference",
            "/sdk/python/quick-start",
        }

    @staticmethod
    def _evidence_sentences(content: str) -> list[str]:
        """Return readable, non-navigation sentences from provider-extracted text."""
        text = str(content or "")
        text = re.sub(r"(?is)## Documentation Index.*?(?=API Reference|API keys|Authentication|Search API|Best practices|$)", " ", text)
        text = re.sub(r"!?\[[^\]]*\]\([^)]*\)", " ", text)
        text = re.sub(r"\s+", " ", text)
        candidates = [part.strip(" \t\r\n-*#>") for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
        boilerplate = re.compile(
            r"^(?:get started|api reference|integrations|changelog|examples|documentation index|"
            r"fetch the complete documentation|use this file to discover|tavily docs home page)$",
            re.IGNORECASE,
        )
        navigation_group = re.compile(
            r"^(?:(?:api reference|integrations|changelog|examples|get started|documentation index)\s*){2,}$",
            re.IGNORECASE,
        )
        return [
            " ".join(sentence.split())
            for sentence in candidates
            if len(sentence) >= 35 and not boilerplate.match(sentence) and not navigation_group.match(sentence)
        ]

    @classmethod
    def _has_substantive_page_content(cls, content: str) -> bool:
        """Accept substantive extracted prose while rejecting navigation-only pages."""
        if any(
            entries
            for entries in cls._build_technical_evidence_matrix([{"url": "", "page_content": content}]).values()
        ):
            return True
        sentences = cls._evidence_sentences(content)
        # Model cards need not mention Tavily-specific API terms. Require multiple
        # factual sentences or one substantial explanatory passage; navigation
        # labels and search snippets never satisfy this content gate.
        return len(sentences) >= 2 or any(
            len(sentence) >= 180
            and bool(re.search(r"\b(?:model|license|licence|context|parameter|memory|ollama|benchmark|tokens?)\b", sentence, re.IGNORECASE))
            for sentence in sentences
        )

    @classmethod
    def _build_technical_evidence_matrix(cls, sources: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
        """Build claim-specific evidence using conservative, source-linked sentence rules."""
        dimensions = {
            "Authentication": re.compile(
                r"(?:\bbearer\b.{0,100}\b(?:api key|token)\b|\b(?:api key|token)\b.{0,100}\bauthori[sz]ation\b|"
                r"\bauthori[sz]ation\b.{0,100}\bbearer\b)",
                re.IGNORECASE,
            ),
            "Endpoint and method": re.compile(
                r"\bPOST\b.{0,160}(?:https?://api\.tavily\.com/search|/search)|"
                r"(?:https?://api\.tavily\.com/search|/search).{0,160}\bPOST\b",
                re.IGNORECASE,
            ),
            "Request parameters": re.compile(
                r"\b(?:query|search_depth|max_results|include_raw_content|include_domains)\b.{0,140}"
                r"(?:\b(?:parameter|required|optional|default|controls?|specifies|request|body)\b|=)|"
                r"\b(?:parameter|required|optional|default|controls?|specifies|request|body)\b.{0,140}"
                r"\b(?:query|search_depth|max_results|include_raw_content|include_domains)\b",
                re.IGNORECASE,
            ),
            "Response structure": re.compile(
                r"\b(?:response|returns?|returned)\b.{0,180}\b(?:results|content|score|answer|raw_content)\b|"
                r"\b(?:results|content|score|answer|raw_content)\b.{0,180}\b(?:response|object|field|array)\b",
                re.IGNORECASE,
            ),
        }
        matrix: dict[str, list[dict[str, str]]] = {label: [] for label in dimensions}
        for source in sources:
            content = str(source.get("page_content") or "")
            if not content:
                continue
            for label, pattern in dimensions.items():
                for sentence in cls._evidence_sentences(content):
                    if pattern.search(sentence):
                        entry = {"excerpt": sentence[:420], "url": str(source.get("url", ""))}
                        if entry not in matrix[label]:
                            matrix[label].append(entry)
                        if len(matrix[label]) >= 3:
                            break
        return matrix

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
            return {"results": [], "error": str(error), "delegated_agent_id": getattr(result, "agent_id", "search.scout"), "delegation_task_id": getattr(result, "task_id", None), "requested_by": "research.athena"}
        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            return {"results": [], "error": "Scout returned an unexpected output format."}
        if not output.get("results"):
            detail = output.get("error") or output.get("summary") or "Scout returned zero results."
            return {**output, "results": [], "error": str(detail), "delegated_agent_id": getattr(result, "agent_id", "search.scout"), "delegation_task_id": getattr(result, "task_id", None), "requested_by": "research.athena"}
        return {**output, "delegated_agent_id": getattr(result, "agent_id", "search.scout"), "delegation_task_id": getattr(result, "task_id", None), "requested_by": "research.athena"}

    @staticmethod
    def _extract_model_targets(prompt: str) -> list[str]:
        """Read numbered or bulleted targets immediately beneath a Compare heading."""
        lines = str(prompt or "").splitlines()
        targets: list[str] = []
        inside_compare = False
        for raw_line in lines:
            line = raw_line.strip()
            if not inside_compare:
                if re.match(r"^compare\s*:\s*$", line, re.IGNORECASE):
                    inside_compare = True
                continue
            if re.match(r"^(?:REQUIREMENTS|COORDINATION|OUTPUT|IMPORTANT|CONSTRAINTS)\s*:", line, re.IGNORECASE):
                break
            item = re.match(r"^(?:\d+[.)]|[-*])\s*(.+?)\s*$", line)
            if not item:
                # Blank lines inside the list are harmless; prose or a new
                # subsection means the comparison list has ended.
                if not line:
                    continue
                if targets:
                    break
                continue
            target = " ".join(item.group(1).split()).strip(" .;:-")
            if target and len(target) <= 100 and target.casefold() not in {value.casefold() for value in targets}:
                targets.append(target)
            if len(targets) >= 5:
                break
        return targets

    @staticmethod
    def _build_queries(prompt: str) -> list[str]:
        """Extract focused topic queries without confusing orchestration with the objective."""
        raw_prompt = str(prompt or "")
        model_targets = ResearchAgent._extract_model_targets(raw_prompt)
        clean = " ".join(raw_prompt.split()).strip()
        if not clean:
            return []

        # Structured multi-option objectives must preserve every named target.
        if len(model_targets) >= 2:
            criteria = (
                "official model card reasoning coding benchmarks inference speed "
                "memory RAM VRAM context length license Ollama compatibility"
            )
            return [
                f"{target} {criteria}"[:179].rstrip()
                for target in model_targets[:DEFAULT_QUERIES]
            ]

        # Technical product research gets distinct, source-oriented query angles.
        if re.search(r"\btavily\b", clean, re.IGNORECASE) and re.search(r"\bsearch\s+api\b", clean, re.IGNORECASE):
            return list(_PRIMARY_SOURCE_HINTS["tavily"])

        # A standalone orchestration instruction is not a research topic.
        if re.fullmatch(
            r"(?:please\s+)?(?:preserve|keep|make sure|ensure)\s+.*(?:delegat|workstream|agent).*",
            clean,
            re.IGNORECASE,
        ):
            return []

        match = re.search(
            r"\b(?:research|look\s+up|find\s+information\s+about|investigate)\s+(?!and\b|analysis\s+task\b|task\b|workflow\b|mission\b)(.+)",
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
    def _is_official_model_source(target: str, url: str) -> bool:
        """Recognize publisher-owned model pages and the official Ollama model catalogue."""
        parsed = urlparse(str(url or ""))
        host = parsed.netloc.casefold().removeprefix("www.")
        path = parsed.path.casefold().rstrip("/")
        name = str(target or "").casefold()

        if "qwen" in name:
            return (
                host in {"qwen.ai", "qwenlm.github.io"}
                or (host == "github.com" and path.startswith("/qwenlm/"))
                or (host == "huggingface.co" and path.startswith("/qwen/"))
                or (host == "ollama.com" and path.startswith("/library/qwen3"))
            )
        if "llama" in name:
            return (
                host in {"llama.com", "ai.meta.com"}
                or (host == "github.com" and path.startswith("/meta-llama/"))
                or (host == "huggingface.co" and path.startswith("/meta-llama/"))
                or (host == "ollama.com" and path.startswith("/library/llama3.2"))
            )
        if "gemma" in name:
            return (
                (host == "ai.google.dev" and "gemma" in path)
                or (host == "deepmind.google" and "gemma" in path)
                or (host == "blog.google" and "gemma" in path)
                or (host == "github.com" and "gemma" in path)
                or (host == "huggingface.co" and "gemma" in path)
                or (host == "ollama.com" and path.startswith("/library/gemma3"))
            )
        return False

    @classmethod
    def _build_model_evidence_matrix(cls, sources: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
        """Extract source-linked evidence for the dimensions requested in model comparisons."""
        dimensions = {
            "Reasoning": re.compile(
                r"\b(?:reasoning|reasoning benchmark|mmlu|gpqa|arc[- ]?challenge|math benchmark|logical reasoning)\b",
                re.IGNORECASE,
            ),
            "Coding performance": re.compile(
                r"\b(?:coding|code generation|programming benchmark|humaneval|mbpp|livecodebench|swe[- ]?bench)\b",
                re.IGNORECASE,
            ),
            "Speed": re.compile(
                r"\b(?:tokens per second|tokens/s|throughput|latency|inference speed|generation speed|prefill speed)\b",
                re.IGNORECASE,
            ),
            "Memory requirements": re.compile(
                r"\b(?:memory requirements?|system memory|ram|vram|gpu memory|quantization|quantized|model size|disk space|gigabytes)\b",
                re.IGNORECASE,
            ),
            "Context length": re.compile(
                r"\b(?:context (?:window|length|size)|maximum context|context tokens|sequence length|token window|max(?:imum)? tokens)\b",
                re.IGNORECASE,
            ),
            "Licensing": re.compile(
                r"\b(?:licen[cs]e|terms of use|open weights|apache 2\.0|mit license|gemma terms|acceptable use policy)\b",
                re.IGNORECASE,
            ),
            "Ollama compatibility": re.compile(
                r"\bollama\b",
                re.IGNORECASE,
            ),
        }
        matrix: dict[str, list[dict[str, str]]] = {label: [] for label in dimensions}
        for source in sources:
            content = str(source.get("page_content") or "")
            if not content:
                continue
            for label, pattern in dimensions.items():
                for sentence in cls._evidence_sentences(content):
                    if pattern.search(sentence):
                        entry = {"excerpt": sentence[:420], "url": str(source.get("url", ""))}
                        if entry not in matrix[label]:
                            matrix[label].append(entry)
                        if len(matrix[label]) >= 5:
                            break
        return matrix

    @staticmethod
    def _is_primary_source(prompt: str, url: str) -> bool:
        lowered = prompt.casefold()
        parsed = urlparse(url)
        host = parsed.netloc.casefold().removeprefix("www.")
        path = parsed.path.casefold().rstrip("/")
        model_targets = ResearchAgent._extract_model_targets(prompt)
        if model_targets:
            return any(ResearchAgent._is_official_model_source(target, url) for target in model_targets)
        if "fastapi" in lowered:
            return (
                host == "fastapi.tiangolo.com"
                or (host == "pypi.org" and path.startswith("/project/fastapi"))
                or (host == "github.com" and path.startswith("/fastapi/fastapi"))
            )
        if "tavily" in lowered:
            return host == "docs.tavily.com"
        # Generic primary-source hints can be carried explicitly as site: URLs
        # in the original task; otherwise avoid guessing ownership.
        domains = re.findall(r"\bsite:([A-Za-z0-9.-]+)", prompt, re.IGNORECASE)
        return host in {domain.casefold() for domain in domains}

    @classmethod
    def _prioritize_primary_sources(cls, prompt: str, sources: list[dict[str, str]]) -> list[dict[str, str]]:
        return sorted(sources, key=lambda item: (not cls._is_primary_source(prompt, item["url"]), item["url"].casefold()))


def create_handler() -> ResearchAgent:
    return ResearchAgent()
