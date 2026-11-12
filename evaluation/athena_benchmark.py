"""Deterministic evaluation harness for Athena's source-evidence gates.

This benchmark uses synthetic, explicitly labelled fixtures. It never calls a
search provider and must not be interpreted as a live-provider quality result.
Run with: python -m evaluation.athena_benchmark
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agents.research_agent.agent import ResearchAgent


FIXTURE_MARKER = "SYNTHETIC BENCHMARK FIXTURE —"
FIXTURE_PROVIDER = "Athena Benchmark Fixture"
FIXTURE_RETRIEVED_AT = "2026-11-12T00:00:00+00:00"
BENCHMARK_VERSION = "1.0.0"


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    prompt: str
    pages: tuple[dict[str, str], ...]
    expected_status: str
    expected_complete: bool
    failed_query_indices: tuple[int, ...] = ()
    duplicate_first_page: bool = False
    expected_technical_coverage: bool | None = None


def _page(title: str, url: str, extracted_text: str) -> dict[str, str]:
    """Create a clearly labelled synthetic source record for a parser fixture."""
    return {
        "title": f"[SYNTHETIC FIXTURE] {title}",
        "url": url,
        "domain": url.split("/")[2],
        "snippet": "Synthetic discovery snippet; not retrieved from this URL.",
        "page_content": f"{FIXTURE_MARKER} {extracted_text}",
        "source_type": "synthetic_benchmark_fixture",
    }


def _tavily_complete_pages() -> tuple[dict[str, str], ...]:
    return (
        _page(
            "Tavily headers fixture",
            "https://docs.tavily.com/documentation/api-headers",
            "Authentication uses a Bearer API key.",
        ),
        _page(
            "Tavily Search API fixture",
            "https://docs.tavily.com/documentation/api-reference/endpoint/search",
            "POST https://api.tavily.com/search processes a search request. "
            "The query parameter is required in the request body. "
            "The response contains a results array with content and score fields.",
        ),
        _page(
            "Tavily search best-practices fixture",
            "https://docs.tavily.com/documentation/best-practices/best-practices-search",
            "The include_raw_content=true option requests extracted page content.",
        ),
    )


def _cases() -> tuple[BenchmarkCase, ...]:
    tavily_prompt = "Research the Tavily Search API official documentation"
    tavily_pages = _tavily_complete_pages()
    nav_text = (
        "## Documentation Index Fetch the complete documentation index at llms.txt "
        "Use this file to discover all available pages before exploring further. "
        "Get started API Reference Integrations Examples Changelog"
    )
    fastapi_prompt = (
        "Research FastAPI current release and official PyPI package metadata"
    )
    fastapi_pages = (
        _page(
            "FastAPI docs fixture",
            "https://fastapi.tiangolo.com/reference/",
            "Authentication uses a Bearer API token.",
        ),
        _page(
            "FastAPI package fixture",
            "https://pypi.org/project/fastapi/",
            "POST /search executes the documented search request.",
        ),
        _page(
            "FastAPI repository fixture",
            "https://github.com/fastapi/fastapi",
            "The response contains a results array with content and score fields.",
        ),
    )

    return (
        BenchmarkCase(
            case_id="tavily_complete_four_dimensions",
            prompt=tavily_prompt,
            pages=tavily_pages,
            expected_status="retrieval_complete_with_extracted_content",
            expected_complete=True,
            expected_technical_coverage=True,
        ),
        BenchmarkCase(
            case_id="tavily_missing_response_structure",
            prompt=tavily_prompt,
            pages=(
                tavily_pages[0],
                _page(
                    "Tavily endpoint fixture",
                    "https://docs.tavily.com/documentation/api-reference/endpoint/search",
                    "POST https://api.tavily.com/search processes a search request. "
                    "The query parameter is required in the request body.",
                ),
                _page(
                    "Tavily parameters fixture",
                    "https://docs.tavily.com/documentation/best-practices/best-practices-search",
                    "The include_raw_content=true option requests extracted page content.",
                ),
            ),
            expected_status="insufficient_technical_coverage",
            expected_complete=False,
            expected_technical_coverage=False,
        ),
        BenchmarkCase(
            case_id="tavily_navigation_only",
            prompt=tavily_prompt,
            pages=tuple(
                _page(f"Navigation fixture {index}", source["url"], nav_text)
                for index, source in enumerate(tavily_pages, start=1)
            ),
            expected_status="insufficient_extracted_content",
            expected_complete=False,
            expected_technical_coverage=False,
        ),
        BenchmarkCase(
            case_id="tavily_irrelevant_official_integrations",
            prompt=tavily_prompt,
            pages=(
                _page(
                    "Anthropic integration fixture",
                    "https://docs.tavily.com/documentation/integrations/anthropic",
                    "SYNTHETIC unrelated integration text that is not Search API reference evidence.",
                ),
                _page(
                    "Changelog fixture",
                    "https://docs.tavily.com/changelog",
                    "SYNTHETIC changelog text that is not Search API reference evidence.",
                ),
                _page(
                    "CrewAI integration fixture",
                    "https://docs.tavily.com/documentation/integrations/crewai",
                    "SYNTHETIC unrelated integration text that is not Search API reference evidence.",
                ),
            ),
            expected_status="insufficient_extracted_content",
            expected_complete=False,
            expected_technical_coverage=False,
        ),
        BenchmarkCase(
            case_id="tavily_duplicate_source_cannot_meet_source_threshold",
            prompt=tavily_prompt,
            pages=(tavily_pages[1],),
            expected_status="insufficient_primary_sources",
            expected_complete=False,
            duplicate_first_page=True,
            expected_technical_coverage=False,
        ),
        BenchmarkCase(
            case_id="tavily_partial_provider_retrieval",
            prompt=tavily_prompt,
            pages=tavily_pages[:2],
            expected_status="partial",
            expected_complete=False,
            failed_query_indices=(2,),
            expected_technical_coverage=False,
        ),
        BenchmarkCase(
            case_id="all_queries_fail_closed",
            prompt=tavily_prompt,
            pages=(),
            expected_status="insufficient_evidence",
            expected_complete=False,
            failed_query_indices=(0, 1, 2),
            expected_technical_coverage=False,
        ),
        BenchmarkCase(
            case_id="fastapi_official_sources_remain_topic_scoped",
            prompt=fastapi_prompt,
            pages=fastapi_pages,
            expected_status="retrieval_complete_with_extracted_content",
            expected_complete=True,
        ),
        BenchmarkCase(
            case_id="fastapi_official_navigation_only",
            prompt=fastapi_prompt,
            pages=tuple(
                _page(f"FastAPI navigation fixture {index}", source["url"], nav_text)
                for index, source in enumerate(fastapi_pages, start=1)
            ),
            expected_status="insufficient_extracted_content",
            expected_complete=False,
        ),
        BenchmarkCase(
            case_id="tavily_lookalike_domains_are_not_primary",
            prompt=tavily_prompt,
            pages=(
                _page(
                    "Spoofed docs host fixture",
                    "https://docs.tavily.com.attacker.example/documentation/api-headers",
                    "Authentication uses a Bearer API key.",
                ),
                _page(
                    "Third-party guide fixture",
                    "https://example.org/tavily/search-api",
                    "POST https://api.tavily.com/search processes a search request.",
                ),
                _page(
                    "Third-party schema fixture",
                    "https://example.net/tavily/response",
                    "The response contains a results array with content and score fields.",
                ),
            ),
            expected_status="insufficient_primary_sources",
            expected_complete=False,
            expected_technical_coverage=False,
        ),
    )


class _FixtureContext:
    """Minimal Scout delegation substitute; network access is intentionally absent."""

    def __init__(self, outcomes: dict[str, dict[str, Any]]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    async def delegate(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        query = kwargs["context"]["research_query"]
        outcome = self.outcomes.get(query, {})
        if isinstance(outcome, Exception):
            raise outcome
        failed = bool(outcome.get("error"))
        return SimpleNamespace(
            succeeded=not failed,
            output=outcome,
            error=outcome.get("error"),
            summary=outcome.get("error", ""),
            agent_id="search.scout",
            task_id=f"fixture-{len(self.calls)}",
        )


def _fixture_outcomes(
    case: BenchmarkCase, queries: list[str]
) -> dict[str, dict[str, Any]]:
    outcomes: dict[str, dict[str, Any]] = {}
    page_index = 0
    for query_index, query in enumerate(queries):
        if query_index in case.failed_query_indices:
            outcomes[query] = {
                "provider": FIXTURE_PROVIDER,
                "results": [],
                "error": "Synthetic provider failure for benchmark coverage.",
                "retrieval_status": "failed",
                "partial": True,
            }
            continue

        if not case.pages:
            outcomes[query] = {
                "provider": FIXTURE_PROVIDER,
                "results": [],
                "error": "Synthetic empty result set.",
                "retrieval_status": "failed",
                "partial": True,
            }
            continue

        if case.duplicate_first_page:
            page = case.pages[0]
        else:
            page = case.pages[min(page_index, len(case.pages) - 1)]
            page_index += 1

        outcomes[query] = {
            "provider": FIXTURE_PROVIDER,
            "retrieved_at": FIXTURE_RETRIEVED_AT,
            "results": [dict(page)],
            "result_count": 1,
            "requested_result_count": 1,
            "partial": False,
            "retrieval_status": "complete",
        }
    return outcomes


async def _evaluate_case(case: BenchmarkCase) -> dict[str, Any]:
    agent = ResearchAgent()
    queries = agent._build_queries(case.prompt)
    context = _FixtureContext(_fixture_outcomes(case, queries))
    result = await agent(
        SimpleNamespace(prompt=case.prompt, context={}),
        context,
    )
    actual_status = str(result.get("research_status", "missing_status"))
    actual_complete = bool(result.get("complete", False))
    actual_coverage = result.get("technical_coverage_complete")
    provenance = result.get("query_provenance", [])

    return {
        "case_id": case.case_id,
        "prompt": case.prompt,
        "expected": {
            "research_status": case.expected_status,
            "complete": case.expected_complete,
            "technical_coverage_complete": case.expected_technical_coverage,
        },
        "actual": {
            "research_status": actual_status,
            "complete": actual_complete,
            "technical_coverage_complete": actual_coverage,
            "source_count": result.get("source_count", 0),
            "primary_source_count": result.get("primary_source_count", 0),
            "extracted_content_count": result.get("extracted_content_count", 0),
            "technical_dimensions_covered": result.get("technical_dimensions_covered", []),
            "source_urls": [item.get("url") for item in result.get("sources", [])],
            "query_count": len(result.get("queries", [])),
            "provenance_count": len(provenance),
            "delegated_agents": sorted(
                {str(item.get("delegated_agent_id", "")) for item in provenance}
            ),
            "providers": sorted(
                {str(item.get("provider", "")) for item in provenance if item.get("provider")}
            ),
        },
        "checks": {
            "status_correct": actual_status == case.expected_status,
            "completion_correct": actual_complete == case.expected_complete,
            "technical_coverage_correct": (
                None
                if case.expected_technical_coverage is None
                else actual_coverage is case.expected_technical_coverage
            ),
        },
    }


def _safe_rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


async def _run_all_cases() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in _cases():
        results.append(await _evaluate_case(case))
    return results


def run_benchmark() -> dict[str, Any]:
    """Run all deterministic scenarios and return a machine-readable report."""
    results = asyncio.run(_run_all_cases())
    total = len(results)
    status_correct = sum(item["checks"]["status_correct"] for item in results)
    completion_correct = sum(item["checks"]["completion_correct"] for item in results)
    expected_complete = sum(item["expected"]["complete"] for item in results)
    expected_incomplete = total - expected_complete
    true_positive = sum(
        item["expected"]["complete"] and item["actual"]["complete"]
        for item in results
    )
    false_positive = sum(
        (not item["expected"]["complete"]) and item["actual"]["complete"]
        for item in results
    )
    false_negative = sum(
        item["expected"]["complete"] and (not item["actual"]["complete"])
        for item in results
    )
    coverage_cases = [
        item for item in results
        if item["checks"]["technical_coverage_correct"] is not None
    ]
    coverage_correct = sum(
        item["checks"]["technical_coverage_correct"] for item in coverage_cases
    )

    return {
        "benchmark": {
            "name": "Athena Evidence Lab",
            "version": BENCHMARK_VERSION,
            "execution_mode": "deterministic_synthetic_fixtures",
            "provider_calls": 0,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_revision": os.getenv("GITHUB_SHA") or None,
            "python_version": platform.python_version(),
            "fixture_warning": (
                "All source text and snippets are synthetic. URLs exercise source-classification "
                "logic only; they were not opened, fetched, or independently verified."
            ),
        },
        "metrics": {
            "scenario_count": total,
            "status_accuracy": _safe_rate(status_correct, total),
            "completion_accuracy": _safe_rate(completion_correct, total),
            "completion_precision": _safe_rate(true_positive, true_positive + false_positive),
            "completion_recall": _safe_rate(true_positive, true_positive + false_negative),
            "false_completion_count": false_positive,
            "false_completion_rate": _safe_rate(false_positive, expected_incomplete),
            "technical_coverage_accuracy": _safe_rate(coverage_correct, len(coverage_cases)),
            "technical_coverage_case_count": len(coverage_cases),
            "expected_complete_cases": expected_complete,
            "expected_incomplete_cases": expected_incomplete,
            "critical_gate": (
                "PASS" if status_correct == total and false_positive == 0 else "FAIL"
            ),
        },
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Athena's deterministic synthetic evidence benchmark."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for a JSON report. The report is also printed to stdout.",
    )
    args = parser.parse_args()
    report = run_benchmark()
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["metrics"]["critical_gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
