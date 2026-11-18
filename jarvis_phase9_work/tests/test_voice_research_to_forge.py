import asyncio
from types import SimpleNamespace

import pytest

from agents.voice_agent.agent import VoiceAgent


class FakeContext:
    def __init__(self, research_result=None, forge_result=None, executable=None,
                 research_agent_id="research.athena", forge_agent_id="coding.forge"):
        self.research_agent_id = research_agent_id
        self.forge_agent_id = forge_agent_id
        available = executable or {research_agent_id, forge_agent_id}
        specs = {
            research_agent_id: SimpleNamespace(agent_id=research_agent_id,
                capabilities=("research.deep", "research.synthesize")),
            forge_agent_id: SimpleNamespace(agent_id=forge_agent_id,
                capabilities=("code.write", "code.test")),
        }
        self.runtime = SimpleNamespace(
            executable_agent_ids=lambda: available,
            discover_executable=lambda capabilities, match_all=True: [
                spec for agent_id, spec in specs.items()
                if agent_id in available and (set(capabilities).issubset(set(spec.capabilities)) if match_all else bool(set(capabilities) & set(spec.capabilities)))
            ],
        )
        self.agent = SimpleNamespace(agent_id="voice.jarvis")
        self.research_result = research_result or SimpleNamespace(
            succeeded=True,
            output={
                "summary": "Search snippets found official FastAPI documentation.",
                "sources": [{
                    "title": "FastAPI docs",
                    "url": "https://fastapi.tiangolo.com/",
                    "snippet": "Official tutorial",
                }],
                "evidence_level": "search_snippets_not_independently_verified",
            },
            error=None,
            summary="",
            status=SimpleNamespace(value="succeeded"),
        )
        self.forge_result = forge_result or SimpleNamespace(
            succeeded=True,
            output=(
                "Files written and verified on disk: athena_forge_smoke_test/app.py, "
                "athena_forge_smoke_test/test_app.py. pytest: 2 passed. Verification: PASSED. Exit code: 0."
            ),
            error=None,
            summary="",
            status=SimpleNamespace(value="succeeded"),
        )
        self.calls = []

    async def delegate(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["agent_id"] == self.research_agent_id:
            return self.research_result
        if kwargs["agent_id"] == self.forge_agent_id:
            return self.forge_result
        raise AssertionError(f"Unexpected agent: {kwargs['agent_id']}")


def test_detects_research_to_forge_workflow():
    prompt = (
        "Research the official FastAPI documentation and release information. "
        "Then pass the actual research results to Forge and create athena_forge_smoke_test/app.py "
        "and athena_forge_smoke_test/test_app.py and run pytest."
    )
    assert VoiceAgent._is_research_to_forge_request(prompt)
    assert "FastAPI" in VoiceAgent._research_query_from_request(prompt)


def test_research_runs_before_forge_and_passes_sources():
    prompt = (
        "Research the official FastAPI documentation and release information for the current stable "
        "version and its Python compatibility requirements. Use Scout to find sources and label snippets "
        "as unverified. Then pass the actual research results to Forge and create "
        "athena_forge_smoke_test/app.py and athena_forge_smoke_test/test_app.py."
    )
    context = FakeContext()
    result = asyncio.run(VoiceAgent()._run_research_to_forge(prompt, context))
    assert [call["agent_id"] for call in context.calls] == ["research.athena", "coding.forge"]
    forge_call = context.calls[1]
    assert forge_call["context"]["research_sources"][0]["url"] == "https://fastapi.tiangolo.com/"
    assert "Athena research summary" in forge_call["prompt"]
    assert "athena_forge_smoke_test/app.py" in result
    assert "2 passed" in result


def test_research_failure_prevents_forge():
    failed = SimpleNamespace(
        succeeded=False, output=None, error="provider unavailable",
        summary="", status=SimpleNamespace(value="failed")
    )
    context = FakeContext(research_result=failed)
    with pytest.raises(RuntimeError, match="Athena failed"):
        asyncio.run(VoiceAgent()._run_research_to_forge(
            "Research FastAPI then create app.py and test_app.py with Forge.", context
        ))
    assert [call["agent_id"] for call in context.calls] == ["research.athena"]


def test_forge_without_verification_evidence_is_not_reported_as_complete():
    forge = SimpleNamespace(
        succeeded=True,
        output="Done, created some files.",
        error=None, summary="", status=SimpleNamespace(value="succeeded")
    )
    context = FakeContext(forge_result=forge)
    with pytest.raises(RuntimeError, match="not contain enough evidence"):
        asyncio.run(VoiceAgent()._run_research_to_forge(
            "Research FastAPI then create athena_forge_smoke_test/app.py and "
            "athena_forge_smoke_test/test_app.py with Forge.", context
        ))


def test_research_to_forge_is_topic_agnostic_and_uses_requested_paths():
    """The coordinator must not silently replace the user's stack with FastAPI."""
    research = SimpleNamespace(
        succeeded=True,
        output={
            "question": "Research Flask documentation and deployment requirements",
            "summary": "Scout returned Flask documentation search snippets.",
            "sources": [{
                "title": "Flask Documentation",
                "url": "https://flask.palletsprojects.com/",
                "snippet": "Flask application and deployment documentation",
            }],
            "evidence_level": "search_snippets_not_independently_verified",
        },
        error=None, summary="", status=SimpleNamespace(value="succeeded"),
    )
    forge = SimpleNamespace(
        succeeded=True,
        output=(
            "Files written and verified: flask_service/app.py, "
            "flask_service/test_app.py. Tests: 4 passed. Exit code: 0. Verification: PASSED."
        ),
        error=None, summary="", status=SimpleNamespace(value="succeeded"),
    )
    context = FakeContext(research_result=research, forge_result=forge)
    prompt = (
        "Research Flask documentation and deployment requirements, then use Forge to create "
        "flask_service/app.py and flask_service/test_app.py and run the tests."
    )
    result = asyncio.run(VoiceAgent()._run_research_to_forge(prompt, context))
    forge_call = context.calls[1]
    assert forge_call["context"]["expected_files"] == [
        "flask_service/app.py", "flask_service/test_app.py"
    ]
    assert "flask_service/app.py" in forge_call["prompt"]
    assert "minimal FastAPI application" not in forge_call["prompt"]
    assert "https://flask.palletsprojects.com/" in result


def test_research_to_forge_uses_capability_discovery_not_fixed_agent_ids():
    context = FakeContext(
        research_agent_id="research.specialist_alpha",
        forge_agent_id="engineering.specialist_beta",
        forge_result=SimpleNamespace(
            succeeded=True,
            output=("Files written and verified: flask_service/app.py, "
                    "flask_service/test_app.py. Tests: 4 passed. Exit code: 0. Verification: PASSED."),
            error=None, summary="", status=SimpleNamespace(value="succeeded"),
        ),
    )
    prompt = (
        "Research Flask documentation and deployment requirements, then use Forge to create "
        "flask_service/app.py and flask_service/test_app.py and run the tests."
    )
    result = asyncio.run(VoiceAgent()._run_research_to_forge(prompt, context))
    assert [call["agent_id"] for call in context.calls] == [
        "research.specialist_alpha", "engineering.specialist_beta"
    ]
    assert "workflow completed" in result.casefold()
