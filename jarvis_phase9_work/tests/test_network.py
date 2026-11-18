from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.bootstrap import discover_manifests, create_runtime
from core.contracts import AgentSpec, TaskRequest, TaskStatus, DuplicateAgentError
from core.registry import AgentRegistry
from core.router import CapabilityRouter
from core.runtime import AgentRuntime

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _spec(agent_id: str, name: str, capabilities: tuple[str, ...], keywords: tuple[str, ...] = ()) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        name=name,
        role=f"{name} test agent",
        description=f"Test handler for {name}",
        capabilities=capabilities,
        routing_keywords=keywords,
    )


def test_project_discovers_ten_separate_initial_agents() -> None:
    discovered = discover_manifests(PROJECT_ROOT / "agents")
    assert len(discovered) == 10
    assert len({spec.agent_id for _, spec in discovered}) == 10
    assert {folder.name for folder, _ in discovered} == {
        "voice_agent", "search_agent", "coding_agent", "desktop_agent",
        "trading_agent", "research_agent", "documentation_agent", "deployment_agent",
        "planner_agent", "memory_agent",
    }


def test_registry_persists_and_reloads(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "agents.json"
    registry = AgentRegistry(path)
    registry.register(_spec("analytics.agent", "Analyst", ("data.analyze",), ("analyse csv",)))
    reloaded = AgentRegistry(path)
    assert reloaded.get("analytics.agent").name == "Analyst"
    assert reloaded.supports("analytics.agent", "data.analyze")


def test_registry_rejects_duplicate_identity(tmp_path: Path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    spec = _spec("search.scout", "Scout", ("web.search",))
    registry.register(spec)
    with pytest.raises(DuplicateAgentError):
        registry.register(spec)


def test_router_finds_search_and_code_specialists() -> None:
    registry = AgentRegistry()
    for _, spec in discover_manifests(PROJECT_ROOT / "agents"):
        registry.register(spec)
    router = CapabilityRouter(registry)
    prompt = "Search the web for my GitHub profile and inspect the code in this repository"
    matches = router.route(prompt, limit=8, exclude=("voice.jarvis",))
    ids = {match.agent_id for match in matches}
    assert "search.scout" in ids
    assert "coding.forge" in ids


@pytest.mark.asyncio
async def test_runtime_dispatches_to_explicit_agent() -> None:
    runtime = AgentRuntime()

    async def handler(task, context):
        return {"agent": context.agent.agent_id, "prompt": task.prompt}

    runtime.register_agent(_spec("test.echo", "Echo", ("text.echo",)), handler)
    result = await runtime.run_task(TaskRequest(prompt="hello", target_agent_id="test.echo"))
    assert result.status is TaskStatus.SUCCEEDED
    assert result.output == {"agent": "test.echo", "prompt": "hello"}
    assert runtime.get_task_result(result.task_id) == result


@pytest.mark.asyncio
async def test_runtime_delegates_and_preserves_correlation() -> None:
    runtime = AgentRuntime()
    observed: dict[str, str] = {}

    async def specialist(task, context):
        observed["parent"] = task.parent_task_id or ""
        observed["correlation"] = task.correlation_id
        return f"done: {task.prompt}"

    async def coordinator(task, context):
        child = await context.delegate("inspect the repository", agent_id="coding.forge")
        return {"child_output": child.output, "child_task_id": child.task_id}

    runtime.register_agent(_spec("coding.forge", "Forge", ("code.inspect",)), specialist)
    runtime.register_agent(_spec("voice.jarvis", "JARVIS", ("task.coordinate",)), coordinator)
    delegation_events = runtime.bus.subscribe(event_types={"agent.task.delegated"})
    parent = await runtime.run_task(TaskRequest(prompt="please inspect", target_agent_id="voice.jarvis"))
    assert parent.succeeded
    assert parent.output["child_output"] == "done: inspect the repository"
    assert observed["parent"] == parent.task_id
    assert observed["correlation"]
    # Delegation is directly executed, so its event must be observable without
    # accumulating a second, unconsumed copy in the specialist's message inbox.
    event = delegation_events.get_nowait()
    assert event.message_type == "agent.task.delegated"
    assert event.correlation_id == observed["correlation"]
    assert runtime._inboxes["coding.forge"].empty()


@pytest.mark.asyncio
async def test_delegate_many_preserves_input_order() -> None:
    runtime = AgentRuntime()

    async def echo(task, context):
        await asyncio.sleep(float(task.context.get("delay", 0)))
        return task.prompt

    async def coordinator(task, context):
        results = await context.delegate_many([
            {"prompt": "first", "agent_id": "test.echo", "context": {"delay": 0.03}},
            {"prompt": "second", "agent_id": "test.echo", "context": {"delay": 0.0}},
        ])
        return [result.output for result in results]

    runtime.register_agent(_spec("test.echo", "Echo", ("text.echo",),), echo)
    runtime.register_agent(_spec("voice.jarvis", "JARVIS", ("task.coordinate",)), coordinator)
    result = await runtime.run_task(TaskRequest(prompt="run both", target_agent_id="voice.jarvis"))
    assert result.succeeded
    assert result.output == ["first", "second"]


@pytest.mark.asyncio
async def test_exceptions_are_reported_as_failed_task_results() -> None:
    runtime = AgentRuntime()

    async def broken(task, context):
        raise RuntimeError("reproducible failure")

    runtime.register_agent(_spec("test.broken", "Broken", ("test.fail",)), broken)
    result = await runtime.run_task(TaskRequest(prompt="fail safely", target_agent_id="test.broken"))
    assert result.status is TaskStatus.FAILED
    assert "reproducible failure" in (result.error or "")


@pytest.mark.asyncio
async def test_jarvis_delegates_search_to_executable_scout(tmp_path: Path, monkeypatch) -> None:
    import agents.search_agent.agent as search_module
    
    monkeypatch.setenv("JARVIS_SEARCH_PROVIDER", "duckduckgo")
    

    sample_html = '''<html><body><a class=\"result__a\" href=\"https://docs.python.org/3/\">Python Docs</a><a class=\"result__snippet\">Official docs</a></body></html>'''
    monkeypatch.setattr(search_module, "_fetch_html", lambda query, *, timeout: sample_html)
    runtime = create_runtime(project_root=PROJECT_ROOT, registry_path=tmp_path / "registry.json")
    result = await runtime.run_task(TaskRequest(
        prompt="Search the web for Git", target_agent_id="voice.jarvis"
    ))
    assert result.status is TaskStatus.SUCCEEDED
    assert "https://docs.python.org/3/" in str(result.output)
    assert runtime.has_handler("voice.jarvis")
    assert runtime.has_handler("search.scout")


def test_new_agent_manifest_can_be_added_without_router_changes(tmp_path: Path) -> None:
    folder = tmp_path / "sentiment_agent"
    folder.mkdir()
    manifest = {
        "agent_id": "sentiment.iris",
        "name": "Iris",
        "role": "Sentiment analysis specialist",
        "description": "Analyses sentiment in text",
        "capabilities": ["text.sentiment"],
        "routing_keywords": ["sentiment analysis", "classify emotion"],
        "version": "0.1.0",
        "enabled": True,
        "max_concurrency": 1,
        "timeout_seconds": 30,
        "tags": ["nlp"],
        "requires_approval_for": [],
        "metadata": {},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    discovered = discover_manifests(tmp_path)
    assert discovered[0][1].agent_id == "sentiment.iris"


@pytest.mark.asyncio
async def test_targeted_agent_message_is_delivered_to_inbox() -> None:
    from core.contracts import AgentMessage

    runtime = AgentRuntime()

    async def handler(task, context):
        return "ready"

    runtime.register_agent(_spec("test.receiver", "Receiver", ("message.receive",)), handler)
    message = AgentMessage(
        message_type="task.handoff",
        from_agent_id="test.sender",
        to_agent_id="test.receiver",
        payload={"artifact_ref": "reports/analysis.md"},
    )
    assert await runtime.send_message(message) is True
    received = await runtime.receive_message("test.receiver", timeout=0.1)
    assert received.message_id == message.message_id
    assert received.payload["artifact_ref"] == "reports/analysis.md"


@pytest.mark.asyncio
async def test_async_handler_timeout_is_explicit() -> None:
    runtime = AgentRuntime()

    async def slow(task, context):
        await asyncio.sleep(0.2)
        return "too late"

    runtime.register_agent(_spec("test.slow", "Slow", ("test.slow",)), slow)
    result = await runtime.run_task(TaskRequest(
        prompt="run slowly", target_agent_id="test.slow", timeout_seconds=0.01
    ))
    assert result.status is TaskStatus.TIMED_OUT
    assert "timeout" in (result.error or "").lower()


def test_each_live_agent_folder_has_identity_and_specialist_instructions() -> None:
    for folder, spec in discover_manifests(PROJECT_ROOT / "agents"):
        assert (folder / "manifest.json").is_file()
        assert (folder / "agent.py").is_file()
        assert (folder / "instructions.md").is_file()
        assert spec.name.strip()
        assert spec.agent_id.strip()
        assert spec.capabilities


def test_voice_wake_word_extraction_is_unambiguous() -> None:
    from agents.voice_agent.session import extract_wake_word, is_voice_exit_command

    assert extract_wake_word("Jarvis, search the web for Git") == (True, "search the web for Git")
    assert extract_wake_word("someone says hello") == (False, "someone says hello")
    assert extract_wake_word("Hey JARVIS") == (True, "")
    assert extract_wake_word("Jarvis, hello") == (True, "hello")
    assert is_voice_exit_command(" stop listening ")


@pytest.mark.asyncio
async def test_voice_session_routes_woken_phrase_and_can_exit_without_wake_word(tmp_path: Path) -> None:
    from agents.voice_agent.session import run_voice_session

    class FakeVoiceIO:
        language = "en-IN"

        def __init__(self) -> None:
            self.transcripts = iter(["someone talking", "Jarvis, hello", "stop listening"])
            self.spoken: list[str] = []

        def listen_once(self) -> str:
            return next(self.transcripts)

        def speak(self, text: str) -> None:
            self.spoken.append(text)

    runtime = create_runtime(project_root=PROJECT_ROOT, registry_path=tmp_path / "registry.json")
    audio = FakeVoiceIO()
    result = await run_voice_session(runtime, voice_io=audio, use_wake_word=True)
    assert result == 0
    assert any("JARVIS online" in item for item in audio.spoken)
    assert any("Voice session ending" in item for item in audio.spoken)


@pytest.mark.asyncio
async def test_voice_agent_fans_out_compound_request_to_search_and_code(tmp_path: Path) -> None:
    runtime = create_runtime(project_root=PROJECT_ROOT, registry_path=tmp_path / "registry.json")
    called: list[str] = []

    async def search_handler(task, context):
        called.append("search")
        return "Search result: official API docs found."

    async def coding_handler(task, context):
        called.append("code")
        return "Code result: repository structure inspected."

    runtime.register_agent(runtime.registry.get("search.scout"), search_handler, replace=True)
    runtime.register_agent(runtime.registry.get("coding.forge"), coding_handler, replace=True)
    result = await runtime.run_task(TaskRequest(
        prompt="Search the web for my GitHub profile and inspect the code in this repository",
        target_agent_id="voice.jarvis",
    ))
    assert result.succeeded
    assert set(called) == {"search", "code"}
    assert "Search result" in result.output
    assert "Code result" in result.output


@pytest.mark.asyncio
async def test_voice_agent_can_use_an_injected_model_for_general_chat(tmp_path: Path) -> None:
    from agents.voice_agent.agent import VoiceAgent

    class FakeModel:
        configured = True

        async def decide(self, *, prompt, history, agents):
            return {"action": "chat", "response": "Quantum entanglement correlates measurement outcomes across particles."}

    runtime = create_runtime(project_root=PROJECT_ROOT, registry_path=tmp_path / "registry.json")
    voice_spec = runtime.registry.get("voice.jarvis")
    runtime.register_agent(voice_spec, VoiceAgent(model_client=FakeModel()), replace=True)
    result = await runtime.run_task(TaskRequest(
        prompt="Explain quantum entanglement in one sentence",
        target_agent_id="voice.jarvis",
    ))
    assert result.succeeded
    assert "correlates measurement outcomes" in result.output


@pytest.mark.asyncio
async def test_jarvis_uses_plain_chat_for_open_ended_ideas_without_json_planning(tmp_path: Path) -> None:
    from agents.voice_agent.agent import VoiceAgent

    class FakeModel:
        configured = True

        async def answer_chat(self, *, prompt, history):
            assert prompt == "Give me five creative ideas for a new AI agent project."
            assert history == []
            return "Here are five ideas: a local-first research agent, a codebase explainer, a study coach, a hardware diagnostics agent, and a personal knowledge librarian."

        async def decide(self, *, prompt, history, agents):
            raise AssertionError("Open-ended chat should not require JSON task planning.")

    runtime = create_runtime(project_root=PROJECT_ROOT, registry_path=tmp_path / "registry.json")
    runtime.register_agent(
        runtime.registry.get("voice.jarvis"),
        VoiceAgent(model_client=FakeModel()),
        replace=True,
    )
    result = await runtime.run_task(TaskRequest(
        prompt="Give me five creative ideas for a new AI agent project.",
        target_agent_id="voice.jarvis",
    ))

    assert result.succeeded
    assert "five ideas" in result.output
    assert "not valid JSON" not in result.output


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("how r u", "doing well"),
        ("how are you", "doing well"),
        ("are you doing alright", "doing alright"),
        ("Jarvis, are you doing alright", "doing alright"),
        ("how's it going", "All good"),
        ("thanks Jarvis", "You're welcome"),
    ],
)
@pytest.mark.asyncio
async def test_jarvis_handles_common_small_talk_without_a_configured_model(
    tmp_path: Path, prompt: str, expected: str
) -> None:
    from agents.voice_agent.agent import VoiceAgent

    class UnconfiguredModel:
        configured = False

    runtime = create_runtime(project_root=PROJECT_ROOT, registry_path=tmp_path / "registry.json")
    runtime.register_agent(
        runtime.registry.get("voice.jarvis"),
        VoiceAgent(model_client=UnconfiguredModel()),
        replace=True,
    )
    result = await runtime.run_task(TaskRequest(
        prompt=prompt,
        target_agent_id="voice.jarvis",
    ))
    assert result.succeeded
    assert expected.casefold() in str(result.output).casefold()
    assert "configure a chat model" not in str(result.output).casefold()


@pytest.mark.asyncio
async def test_jarvis_handles_wellbeing_followup_without_configured_model(tmp_path: Path) -> None:
    from agents.voice_agent.agent import VoiceAgent

    class UnconfiguredModel:
        configured = False

    runtime = create_runtime(project_root=PROJECT_ROOT, registry_path=tmp_path / "registry.json")
    voice = VoiceAgent(model_client=UnconfiguredModel())
    runtime.register_agent(runtime.registry.get("voice.jarvis"), voice, replace=True)

    first = await runtime.run_task(TaskRequest(prompt="Jarvis, how are you?", target_agent_id="voice.jarvis"))
    second = await runtime.run_task(TaskRequest(prompt="I am good Jarvis", target_agent_id="voice.jarvis"))

    assert first.succeeded and "How are you?" in first.output
    assert second.succeeded
    assert "Glad to hear that" in second.output
    assert "configure a chat model" not in second.output.casefold()


@pytest.mark.asyncio
async def test_jarvis_handles_contextual_what_about_you_without_model(tmp_path: Path) -> None:
    from agents.voice_agent.agent import VoiceAgent

    class UnconfiguredModel:
        configured = False

    runtime = create_runtime(project_root=PROJECT_ROOT, registry_path=tmp_path / "registry.json")
    voice = VoiceAgent(model_client=UnconfiguredModel())
    runtime.register_agent(runtime.registry.get("voice.jarvis"), voice, replace=True)

    await runtime.run_task(TaskRequest(prompt="how are you", target_agent_id="voice.jarvis"))
    result = await runtime.run_task(TaskRequest(prompt="what about you", target_agent_id="voice.jarvis"))

    assert result.succeeded
    assert "doing well" in result.output.casefold()
    assert "configure a chat model" not in result.output.casefold()


def test_router_normalizes_hyphenated_phrases_and_routes_common_specialist_requests() -> None:
    registry = AgentRegistry()
    for _, spec in discover_manifests(PROJECT_ROOT / "agents"):
        registry.register(spec)
    router = CapabilityRouter(registry)
    executable_ids = {"documentation.scribe", "planning.planner", "research.athena"}

    cases = {
        "Draft a professional email to the project team": "documentation.scribe",
        "Create a step-by-step plan to test the JARVIS agent network": "planning.planner",
        "Analyze the benefits and risks of adding Scribe, Athena, and Planner": "research.athena",
    }
    for prompt, expected_id in cases.items():
        matches = router.route(
            prompt, limit=8, exclude=("voice.jarvis",), executable_ids=executable_ids
        )
        assert matches, f"No specialist route for: {prompt}"
        assert matches[0].agent_id == expected_id
        assert matches[0].executable


def test_voice_output_prefers_scribe_content_over_summary() -> None:
    from agents.voice_agent.agent import VoiceAgent

    rendered = VoiceAgent._render_output({
        "summary": "Scribe generated an email draft; no files were written.",
        "content": "To: Project team\nSubject: Update\n\nHello team,\n[Draft body]",
        "draft_only": True,
    })
    assert "Hello team" in rendered
    assert "Scribe generated an email draft" not in rendered


def test_voice_output_renders_planner_steps_not_only_summary() -> None:
    from agents.voice_agent.agent import VoiceAgent

    rendered = VoiceAgent._render_output({
        "goal": "Test the agent network",
        "steps": [
            {"title": "Run the unit tests", "depends_on": [], "verification": "Record pass/fail."},
            {"title": "Check specialist routing", "depends_on": [1], "verification": "Confirm expected agent IDs."},
        ],
        "risks": ["External search may be unavailable."],
        "summary": "Planner created a 2-step plan.",
        "execution_status": "plan_only_not_executed",
    })
    assert "Run the unit tests" in rendered
    assert "Check specialist routing" in rendered
    assert "External search may be unavailable" in rendered
    assert "plan_only_not_executed" in rendered
