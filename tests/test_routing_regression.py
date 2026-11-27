"""Routing regression: everyday requests must reach the right specialist."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.bootstrap import create_runtime
from core.router import CapabilityRouter, _stem

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CASES = [

("Plan a 4-week SAT study schedule","planning.planner"),
("Make me a weekly study timetable and milestones","planning.planner"),
("Help me prioritize my tasks for exams","planning.planner"),
("Break down the steps to launch my app","planning.planner"),
("What is the roadmap to learn machine learning","planning.planner"),
("Search for the best free SAT practice sites","search.scout"),
("Find online resources about Python asyncio","search.scout"),
("Look up the latest React documentation","search.scout"),
("Research the pros and cons of studying in the US vs UK","research.athena"),
("Compare options for cloud databases with evidence","research.athena"),
("Summarize the evidence on spaced repetition","research.athena"),
("Fix the bug in my python function","coding.forge"),
("Write a function that parses CSV files","coding.forge"),
("Add unit tests to my repository","coding.forge"),
("Write a README for my project","documentation.scribe"),
("Draft an email to my teacher asking for a recommendation letter","documentation.scribe"),
("Write a personal statement outline","documentation.scribe"),
("Remember that my SAT is on Dec 5","memory.notes"),
("What did I tell you to remember about SAT","memory.notes"),
("Deploy my site to Vercel","deployment.launch"),
("Is my app ready for production release","deployment.launch"),
("How do I integrate the Stripe API with my backend","systems.atlas"),
("Design the integration between two services via webhooks","systems.atlas"),
]


@pytest.fixture(scope="module")
def router() -> CapabilityRouter:
    return CapabilityRouter(create_runtime(project_root=PROJECT_ROOT).registry)


@pytest.mark.parametrize(("prompt", "expected"), CASES)
def test_everyday_requests_route_to_expected_agent(router, prompt, expected):
    matches = router.route(prompt, limit=1)
    assert matches, f"no route for: {prompt}"
    assert matches[0].agent_id == expected


@pytest.mark.parametrize(
    ("word", "stem"),
    [("plans", "plan"), ("planning", "plan"), ("integrations", "integration"), ("is", "is")],
)
def test_stemmer_normalizes_simple_suffixes(word, stem):
    assert _stem(word) == stem


def test_unrelated_chatter_matches_nothing(router):
    assert router.route("hello there how are you", limit=1) == []
