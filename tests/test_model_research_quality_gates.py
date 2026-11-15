from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agents.research_agent.agent import ResearchAgent
from agents.voice_agent.agent import VoiceAgent


MODEL_PROMPT = """
Run a complete multi-agent research and analysis task.
Compare:
1. Qwen 3 4B
2. Llama 3.2 3B
3. Gemma 3 4B
Research current model information using reliable official sources.
Compare reasoning, coding performance, inference speed, RAM and VRAM requirements,
context length, licensing and Ollama compatibility.
"""


def test_model_source_matcher_rejects_nearby_but_different_variants():
    assert not ResearchAgent._is_official_model_source(
        "Qwen 3 4B", "https://ollama.com/library/qwen3-coder"
    )
    assert not ResearchAgent._is_official_model_source(
        "Llama 3.2 3B", "https://ollama.com/library/llama3.2-vision"
    )
    assert not ResearchAgent._is_official_model_source(
        "Gemma 3 4B", "https://ai.google.dev/gemma/docs/core/model_card_4"
    )


def test_model_source_matcher_accepts_exact_ollama_variant_pages():
    assert ResearchAgent._is_official_model_source(
        "Qwen 3 4B", "https://ollama.com/library/qwen3:4b"
    )
    assert ResearchAgent._is_official_model_source(
        "Llama 3.2 3B", "https://ollama.com/library/llama3.2:3b"
    )
    assert ResearchAgent._is_official_model_source(
        "Gemma 3 4B", "https://ollama.com/library/gemma3:4b"
    )


def test_model_identity_requires_both_family_and_parameter_variant():
    assert ResearchAgent._has_exact_model_identity("Qwen 3 4B", "Qwen3-4B model card")
    assert ResearchAgent._has_exact_model_identity("Llama 3.2 3B", "Llama 3.2 3B Instruct")
    assert ResearchAgent._has_exact_model_identity("Gemma 3 4B", "Gemma 3 4B IT")
    assert not ResearchAgent._has_exact_model_identity("Gemma 3 4B", "Gemma 4 E4B")
    assert not ResearchAgent._has_exact_model_identity("Qwen 3 4B", "Qwen3-Coder 30B")
    assert not ResearchAgent._has_exact_model_identity("Llama 3.2 3B", "Llama 3.2 Vision 11B")


def test_model_comparison_does_not_pass_with_wrong_model_family_pages():
    agent = ResearchAgent()
    queries = agent._build_queries(MODEL_PROMPT)
    wrong_sources = [
        {
            "title": "Qwen3 Coder",
            "url": "https://ollama.com/library/qwen3-coder",
            "snippet": "Qwen3-Coder 30B",
            "page_content": "Qwen3-Coder 30B is a coding model.",
        },
        {
            "title": "Llama 3.2 Vision",
            "url": "https://ollama.com/library/llama3.2-vision",
            "snippet": "Llama 3.2 Vision 11B",
            "page_content": "Llama 3.2 Vision 11B is a multimodal model.",
        },
        {
            "title": "Gemma 4 model card",
            "url": "https://ai.google.dev/gemma/docs/core/model_card_4",
            "snippet": "Gemma 4 E4B model information",
            "page_content": "Gemma 4 E4B supports long context windows.",
        },
    ]
    outcomes = {
        query: {
            "provider": "Tavily Search API",
            "retrieved_at": "2026-11-15T12:00:00+00:00",
            "retrieval_status": "complete",
            "partial": False,
            "results": [source],
        }
        for query, source in zip(queries, wrong_sources)
    }

    class FakeContext:
        def __init__(self):
            self.calls = 0

        async def delegate(self, **kwargs):
            query = kwargs["context"]["research_query"]
            output = outcomes[query]
            self.calls += 1
            return SimpleNamespace(
                succeeded=True,
                output=output,
                task_id=f"00000000-0000-4000-8000-{self.calls:012d}",
                agent_id="search.scout",
                error=None,
                summary="",
            )

    result = asyncio.run(agent(SimpleNamespace(prompt=MODEL_PROMPT), FakeContext()))

    assert result["requested_model_targets"] == ["Qwen 3 4B", "Llama 3.2 3B", "Gemma 3 4B"]
    assert result["model_target_coverage_complete"] is False
    assert result["model_target_content_coverage_complete"] is False
    assert result["model_target_dimension_coverage_complete"] is False
    assert result["technical_coverage_complete"] is False
    assert result["complete"] is False
    assert all(not urls for urls in result["model_target_coverage"].values())


def _complete_report() -> str:
    return """
# Local model comparison

## Executive recommendation

The evidence-backed recommendation is conditional; do not treat unpublished measurements as verified.

## Three-model comparison table

| Model | Reasoning and instruction following | Coding performance | Inference speed | RAM and VRAM | Context length | Licensing | Ollama compatibility |
|---|---|---|---|---|---|---|---|
| Qwen 3 4B | sourced | sourced | missing | estimate | sourced | sourced | sourced |
| Llama 3.2 3B | sourced | sourced | missing | estimate | sourced | sourced | sourced |
| Gemma 3 4B | sourced | sourced | missing | estimate | sourced | sourced | sourced |

## Best model by use case

Coding: evidence-based comparison.
Research: evidence-based comparison.
General desktop assistant: evidence-based comparison.

## Recommended setup

Windows 11 + Python + Ollama setup, with measurement-dependent hardware guidance.

## Official sources and evidence provenance

- Official source: https://ollama.com/library/qwen3:4b — retrieved 2026-11-15.
- Official source: https://ollama.com/library/llama3.2:3b — retrieved 2026-11-15.
- Official source: https://ollama.com/library/gemma3:4b — retrieved 2026-11-15.

## Limitations and missing evidence

Inference-speed and hardware measurements are not directly comparable without a fixed device and quantization.

## Actual agent execution trace

- planning.planner task=00000000-0000-4000-8000-000000000001
- research.athena task=00000000-0000-4000-8000-000000000002
- research.athena → search.scout task=00000000-0000-4000-8000-000000000003
- research.athena → search.scout task=00000000-0000-4000-8000-000000000004
- research.athena → search.scout task=00000000-0000-4000-8000-000000000005
- documentation.scribe task=00000000-0000-4000-8000-000000000006
"""


def test_model_report_gate_rejects_generic_scribe_report():
    result = VoiceAgent._validate_model_comparison_report(
        MODEL_PROMPT,
        "Report draft: actual Athena results are included below.",
    )
    assert result["required"] is True
    assert result["complete"] is False
    assert "three-model comparison table" in result["missing"]
    assert "actual execution trace with task IDs" in result["missing"]


def test_model_report_gate_accepts_all_required_report_deliverables():
    result = VoiceAgent._validate_model_comparison_report(MODEL_PROMPT, _complete_report())
    assert result["required"] is True
    assert result["complete"] is True
    assert result["missing"] == []


def test_model_report_gate_does_not_change_non_model_report_tasks():
    result = VoiceAgent._validate_model_comparison_report(
        "Write a project status report",
        "## Status\n\nA concise status report.",
    )
    assert result == {"required": False, "complete": True, "missing": []}
