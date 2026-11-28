"""Round 3: wider routing battery and natural-language note recall."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.memory_agent.agent import MemoryNotesAgent
from core.bootstrap import create_runtime
from core.router import CapabilityRouter

ROOT = Path(__file__).resolve().parents[1]

ROUTES = [
    ("What did I note about my dentist appointment?", "memory.notes"),
    ("Do you remember my SAT date?", "memory.notes"),
    ("How many notes do I have", "memory.notes"),
    ("Why is my code throwing a KeyError?", "coding.forge"),
    ("Review my pull request for bugs", "coding.forge"),
    ("Proofread my essay", "documentation.scribe"),
    ("Draft a LinkedIn post about my project", "documentation.scribe"),
    ("Who is the founder of OpenAI?", "search.scout"),
    ("Latest news on NASA", "search.scout"),
    ("Find a good laptop under 60000", "search.scout"),
    ("Explain the causes of World War 1 in depth", "research.athena"),
    ("Connect my app to the Twilio API", "systems.atlas"),
]
SMALL_TALK = ["hello", "thanks!", "What can you do?", "What's the weather like?"]


@pytest.fixture(scope="module")
def router():
    return CapabilityRouter(create_runtime(project_root=ROOT).registry)


@pytest.mark.parametrize(("prompt", "expected"), ROUTES)
def test_extended_prompts_route(router, prompt, expected):
    matches = router.route(prompt, limit=1)
    assert matches and matches[0].agent_id == expected


@pytest.mark.parametrize("prompt", SMALL_TALK)
def test_small_talk_does_not_route_to_a_specialist(router, prompt):
    assert router.route(prompt, limit=1) == []


async def _ask(agent, prompt):
    return await agent(SimpleNamespace(prompt=prompt, context={}), None)


async def test_natural_recall_phrasings_find_notes(tmp_path):
    agent = MemoryNotesAgent(tmp_path / "n.json")
    await _ask(agent, "Remember that the project deadline is Friday")
    await _ask(agent, "Remember that dentist appointment is Monday 4pm")
    for prompt in (
        "What did I note about the project deadline?",
        "check deadline in my notes",
        "Do you remember the deadline for the project?",
    ):
        result = await _ask(agent, prompt)
        assert result["operation"] == "search", prompt
        assert result["count"] == 1 and "deadline" in result["notes"][0]["content"]
    assert (await _ask(agent, "what did I tell you about groceries?"))["count"] == 0


async def test_recall_ranks_exact_phrase_first_then_newest(tmp_path):
    agent = MemoryNotesAgent(tmp_path / "n.json")
    await _ask(agent, "Remember that physics test chapter 3 is on Monday")
    await _ask(agent, "Remember that physics test chapter 5 is on Friday")
    await _ask(agent, "Remember that chemistry test is on Tuesday")
    found = await _ask(agent, "Find my note about physics test")
    assert [n["content"][:22] for n in found["notes"]] == ["physics test chapter 3", "physics test chapter 5"]
    fuzzy = await _ask(agent, "what did I note about the test in physics")
    assert fuzzy["notes"][0]["content"].startswith("physics test chapter 5")  # newest first
    assert all("chemistry" not in n["content"] for n in fuzzy["notes"])


async def test_count_notes(tmp_path):
    agent = MemoryNotesAgent(tmp_path / "n.json")
    assert (await _ask(agent, "how many notes do I have"))["count"] == 0
    await _ask(agent, "Remember that a")
    assert (await _ask(agent, "How many notes do I have?"))["count"] == 1
