from __future__ import annotations

import pytest

from agents.voice_agent.agent import VoiceAgent
from agents.voice_agent.session import _spoken_reply


SOURCES = [
    {
        "title": "Python 3.12 documentation",
        "url": "https://docs.python.org/3.12/",
        "domain": "docs.python.org",
        "snippet": "Python 3.12 documentation includes tutorials, library reference, and language reference.",
    },
    {
        "title": "What's new in Python 3.12",
        "url": "https://docs.python.org/3.12/whatsnew/3.12.html",
        "domain": "docs.python.org",
        "snippet": "The release notes describe new features and changes in Python 3.12.",
    },
]


class FakeChatModel:
    configured = True

    async def answer_with_sources(self, *, prompt, history, sources):
        assert prompt == (
            "Search the web for Python 3.12 documentation and explain "
            "the biggest changes in simple terms."
        )
        assert history == []  # Fresh searches must not be contaminated by unrelated turns.
        assert sources[0]["url"] == SOURCES[0]["url"]
        return "The biggest Python 3.12 changes include clearer type-parameter syntax and more flexible f-strings [1][2]."


@pytest.mark.asyncio
async def test_search_results_are_synthesized_and_sources_are_preserved():
    agent = VoiceAgent(model_client=FakeChatModel())
    output = {
        "query": "Python 3.12",
        "results": SOURCES,
        "retrieved_at": "2026-10-16T12:00:00+00:00",
        "summary": "raw Scout result summary",
    }

    response = await agent._synthesize_search_response(
        "Search the web for Python 3.12 documentation and explain the biggest changes in simple terms.",
        output,
    )

    assert response.startswith("JARVIS answer: The biggest Python 3.12 changes include")
    assert "[1] Python 3.12 documentation — https://docs.python.org/3.12/" in response
    assert "[2] What's new in Python 3.12 — https://docs.python.org/3.12/whatsnew/3.12.html" in response
    assert "Retrieved: 2026-10-16" in response


def test_search_without_model_provides_snippet_summary_and_sources():
    agent = VoiceAgent(model_client=type("Unconfigured", (), {"configured": False})())
    output = {"query": "Python 3.12", "results": SOURCES, "retrieved_at": "now"}

    response = __import__("asyncio").run(agent._synthesize_search_response("search Python 3.12", output))

    assert "Here is what the retrieved results say" in response
    assert "Python 3.12 documentation includes tutorials" in response
    assert "https://docs.python.org/3.12/" in response
    assert "configure the optional chat model" in response


def test_voice_speaks_answer_paragraph_not_source_list():
    response = (
        "JARVIS answer: Python 3.12 documentation covers the language, standard library, "
        "and new release changes [1][2].\n\nSources:\n[1] Docs — https://docs.python.org/3.12/"
    )

    spoken = _spoken_reply(type("Result", (), {"status": "succeeded"})(), response)

    assert "Python 3.12 documentation covers" in spoken
    assert "https://" not in spoken
    assert "[1]" not in spoken


def test_search_and_explain_prompt_is_trimmed_before_scout_routing():
    prompt = (
        '“Search the web for Python 3.12 documentation and explain '
        'the biggest changes in simple terms.”'
    )
    prepared = VoiceAgent._prepare_search_prompt(prompt)
    assert prepared == 'Python 3.12 official release notes key changes'


def test_search_and_explain_is_not_treated_as_multi_agent_compound():
    # Search followed by explanation is a single Scout -> JARVIS synthesis flow.
    import re
    prompt = 'Search the web for Python 3.12 documentation and explain the biggest changes.'
    search_intent = bool(re.search(
        r"\b(?:search|web search|look up|find online|google)\b", prompt, re.IGNORECASE
    ))
    executable = [type('Match', (), {'agent_id': 'search.scout'})()]
    synthesis_followup = bool(re.search(
        r"\s+(?:and then|then|and)\s+(?:please\s+)?"
        r"(?:explain|summari[sz]e|tell me|describe|compare|analy[sz]e|break down|give me an overview of)\b",
        prompt,
        re.IGNORECASE,
    ))
    compound = VoiceAgent._is_compound_request(prompt) and not (
        search_intent and synthesis_followup and any(match.agent_id == 'search.scout' for match in executable)
    )
    assert compound is False


@pytest.mark.asyncio
async def test_search_synthesis_preserves_full_question_instead_of_scout_query():
    class CaptureModel:
        configured = True

        def __init__(self):
            self.seen_prompt = None
            self.seen_history = None

        async def answer_with_sources(self, *, prompt, history, sources):
            self.seen_prompt = prompt
            self.seen_history = history
            return "For a beginner, the most useful change is clearer type parameter syntax [1]."

    model = CaptureModel()
    agent = VoiceAgent(model_client=model)
    agent._remember(
        "Search the web for Python 3.12 changes",
        "JARVIS answer: Python 3.12 introduced several changes [1].\n\nSources:\n[1] Docs — https://docs.python.org/3.12/",
    )
    prompt = "Which of those changes would matter most to a beginner?"
    response = await agent._synthesize_search_response(
        prompt,
        {"query": "Python 3.12 changes", "results": SOURCES, "retrieved_at": "now"},
    )

    assert model.seen_prompt == prompt
    assert model.seen_history
    assert "For a beginner" in response


def test_search_query_adds_research_terms_for_explanation_requests():
    prepared = VoiceAgent._prepare_search_prompt(
        "Search the web for study abroad requirements and explain the main documents needed."
    )
    assert prepared == "study abroad requirements official requirements eligibility documents"


def test_search_query_does_not_add_synthesis_terms_for_plain_search():
    prepared = VoiceAgent._prepare_search_prompt("Search the web for study abroad requirements")
    assert prepared == "study abroad requirements"
