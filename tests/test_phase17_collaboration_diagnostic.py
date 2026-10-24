from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from agents.documentation_agent.agent import DocumentationAgent
from agents.planner_agent.agent import PlannerAgent
from agents.research_agent.agent import ResearchAgent
from agents.voice_agent.agent import VoiceAgent
from core.contracts import AgentSpec, TaskRequest, TaskStatus
from core.event_bus import EventBus
from core.registry import AgentRegistry
from core.runtime import AgentRuntime


def _spec(agent_id: str, name: str, capabilities: tuple[str, ...]) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        name=name,
        role=f"{name} test specialist",
        description=f"Test fixture for {name}",
        capabilities=capabilities,
        routing_keywords=(name.casefold(),),
        max_concurrency=2,
        timeout_seconds=30,
    )


def test_local_agent_audit_intent_is_routed_before_keyword_router_without_magic_title() -> None:
    # This is the user's actual natural-language objective, not the canned title.
    assert VoiceAgent._is_local_agent_collaboration_audit(
        "Objective: Investigate the actual JARVIS codebase, verify its collaboration infrastructure, "
        "and produce an evidence-backed report showing which agents genuinely worked, exchanged information, and returned results."
    )
    assert VoiceAgent._is_local_agent_collaboration_audit(
        "Analyze our current repository to inspect the agent runtime, verify handoffs and inbox delivery, and produce a report."
    )
    assert VoiceAgent._is_agent_collaboration_diagnostic_request(
        "Run a multi-agent collaboration diagnostic for our actual codebase and verify the infrastructure."
    )
    assert not VoiceAgent._is_local_agent_collaboration_audit(
        "Research JARVIS collaboration frameworks on the public web."
    )
    assert not VoiceAgent._is_local_agent_collaboration_audit(
        "Review this codebase for security problems."
    )


def test_diagnostic_stages_select_agents_from_declared_capabilities_not_fixed_ids() -> None:
    specs = [
        _spec("custom.planner", "Custom Planner", ("planning.breakdown",)),
        _spec("custom.repo_inspector", "Repo Inspector", ("code.inspect", "code.test")),
        _spec("custom.reviewer", "Evidence Reviewer", ("research.verify",)),
        _spec("custom.reporter", "Report Writer", ("writing.report",)),
    ]

    class Registry:
        def list(self, enabled_only=True): return specs

    class Runtime:
        registry = Registry()
        def has_handler(self, agent_id): return agent_id in {spec.agent_id for spec in specs}

    runtime = Runtime()
    assert VoiceAgent._choose_executable_capability_handler(runtime, ("planning.breakdown",)) == ("custom.planner", "planning.breakdown")
    assert VoiceAgent._choose_executable_capability_handler(runtime, ("code.inspect",)) == ("custom.repo_inspector", "code.inspect")
    assert VoiceAgent._choose_executable_capability_handler(runtime, ("research.synthesize", "research.verify")) == ("custom.reviewer", "research.verify")
    assert VoiceAgent._choose_executable_capability_handler(runtime, ("writing.report",)) == ("custom.reporter", "writing.report")
    assert VoiceAgent._choose_executable_capability_handler(runtime, ("deployment.release",)) == (None, None)


def test_test_outcome_parser_accepts_normal_pytest_summaries_and_fails_closed() -> None:
    assert VoiceAgent._parse_explicit_test_outcome("228 passed in 0.53s") == "passed"
    assert VoiceAgent._parse_explicit_test_outcome("2 failed, 229 passed in 11.98s") == "failed"
    assert VoiceAgent._parse_explicit_test_outcome("Verification: PASSED\nExit code: 0") == "passed"
    assert VoiceAgent._parse_explicit_test_outcome("Tests started but no summary was returned") == "unknown"


def test_diagnostic_delegates_to_real_handlers_passes_results_and_persists_scribe_report(tmp_path: Path) -> None:
    audit_path = tmp_path / "data" / "task_history.jsonl"
    # Give the read-only evidence collector a tiny real repository for citations.
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "runtime.py").write_text(
        "class AgentContext:\n    async def delegate(self): pass\n    async def send_message(self): pass\n    async def receive_message(self): pass\n\nclass AgentRuntime:\n    async def run_task(self): pass\n    async def send_message(self): pass\n",
        encoding="utf-8",
    )
    (tmp_path / "core" / "contracts.py").write_text(
        "class AgentMessage: pass\nclass TaskRequest: pass\nclass TaskResult: pass\n",
        encoding="utf-8",
    )
    runtime = AgentRuntime(registry=AgentRegistry(), bus=EventBus(), max_delegation_depth=6, audit_log_path=audit_path)
    events = runtime.bus.subscribe(queue_size=1000)
    observed_athena_contexts: list[dict] = []
    observed_forge_prompts: list[str] = []
    voice = VoiceAgent()
    planner = PlannerAgent()
    athena = ResearchAgent()
    scribe = DocumentationAgent()

    async def forge_handler(task, context):
        prompt = task.prompt
        observed_forge_prompts.append(prompt)
        if task.context.get("stage") == "existing_test_suite":
            return "Verification: PASSED\nExit code: 0\n228 passed in 0.53s"
        return (
            "Local source inspection evidence: core/runtime.py defines AgentRuntime and AgentContext; "
            "AgentRuntime.run_task executes a child request, AgentContext.delegate carries parent_task_id and "
            "correlation_id, AgentContext.send_message and AgentContext.receive_message provide inbox APIs. "
            "core/contracts.py defines AgentMessage, TaskRequest and TaskResult. "
            "Delegation publishes agent.task.delegated and agent.task.result. "
            "Inbox delivery is only confirmed by AgentRuntime.send_message success and an actual receive."
        )

    async def athena_handler(task, context):
        observed_athena_contexts.append(dict(task.context))
        return await athena(task, context)

    runtime.register_agent(_spec("voice.jarvis", "JARVIS", ("conversation.handle", "task.coordinate")), voice)
    runtime.register_agent(_spec("planning.planner", "Planner", ("planning.breakdown", "planning.checklist")), planner)
    runtime.register_agent(_spec("coding.forge", "Forge", ("code.inspect", "code.test")), forge_handler)
    runtime.register_agent(_spec("research.athena", "Athena", ("research.deep", "research.synthesize", "research.verify")), athena_handler)
    runtime.register_agent(_spec("documentation.scribe", "Scribe", ("documentation.write", "writing.report")), scribe)
    # Deliberately register Scout with web-only capabilities. The workflow should
    # report this limitation rather than sending a local-filesystem request to web search.
    async def scout_must_not_run(task, context):
        raise AssertionError("Scout must not be invoked for local code inspection")
    runtime.register_agent(_spec("search.scout", "Scout", ("web.search", "documentation.lookup")), scout_must_not_run)

    prompt = (
        "Objective: Investigate the actual JARVIS codebase, verify its collaboration infrastructure, "
        "and produce an evidence-backed report showing which agents genuinely worked, exchanged information, "
        "and returned results. Inspect local source files and run the tests."
    )
    root = TaskRequest(prompt=prompt, capabilities=("conversation.handle",), target_agent_id="voice.jarvis")
    result = asyncio.run(runtime.run_task(root))

    assert result.status is TaskStatus.SUCCEEDED, result.error or result.output
    output = str(result.output)
    assert "LOCAL AGENT-SYSTEM DIAGNOSTIC — SUCCESS" in output
    assert "coding.forge" in output and "planning.planner" in output
    assert "research.athena" in output and "documentation.scribe" in output
    assert "Test outcome: passed" in output
    assert "data/reports/agent_collaboration_test" in output
    assert len(observed_forge_prompts) == 2
    assert len(observed_athena_contexts) == 1
    assert observed_athena_contexts[0]["analysis_scope"] == "offline_context_analysis_only"
    supplied = " ".join(str(x) for x in observed_athena_contexts[0]["source_material"])
    assert "core/runtime.py" in supplied
    assert "Verification: PASSED" in supplied

    child_results = [item for item in runtime._task_results.values() if item.task_id != root.task_id]
    assert len(child_results) == 5
    assert {item.agent_id for item in child_results} == {
        "planning.planner", "coding.forge", "research.athena", "documentation.scribe"
    }
    assert all(item.metadata.get("parent_task_id") == root.task_id for item in child_results)

    written = next(line.split("Report: ", 1)[1] for line in output.splitlines() if line.startswith("Report: "))
    report = tmp_path / written
    assert report.is_file()
    report_text = report.read_text(encoding="utf-8")
    assert "Observed facts" in report_text or "source inspection evidence" in report_text
    assert "does not claim direct agent-to-agent inbox delivery" in report_text.lower() or "direct agent-to-agent inbox delivery" in report_text.lower()

    # Verify actual task handoff/result events were emitted by the runtime.
    bus_items = []
    while not events.queue.empty():
        bus_items.append(events.get_nowait())
    kinds = {
        getattr(item, "message_type", None) or getattr(item, "event_type", None)
        for item in bus_items
    }
    assert "agent.task.delegated" in kinds
    assert "agent.task.result" in kinds
    assert "agent.workflow.context_handoff" in kinds
    assert "agent.action.started" in kinds
    assert "agent.action.succeeded" in kinds
    runtime.bus.unsubscribe(events)


def test_scribe_authorized_report_write_cannot_escape_data_reports(tmp_path: Path) -> None:
    from agents.documentation_agent.agent import DocumentationAgent

    context = SimpleNamespace(runtime=SimpleNamespace(audit_log_path=tmp_path / "data" / "task_history.jsonl"))
    try:
        DocumentationAgent._persist_authorized_report(
            {"persist_report_filename": "..\\outside.md"},
            {"content": "must not be written"},
            context,
        )
    except ValueError as exc:
        assert "specifically named" in str(exc)
    else:
        raise AssertionError("Unsafe filename was accepted")
    assert not (tmp_path / "outside.md").exists()


def test_failed_pytest_command_makes_root_mission_fail_even_when_forge_handler_returns_normally(tmp_path: Path) -> None:
    from core.contracts import TaskResult

    specs = {
        "planning.planner": _spec("planning.planner", "Planner", ("planning.breakdown",)),
        "coding.forge": _spec("coding.forge", "Forge", ("code.inspect", "code.test")),
        "research.athena": _spec("research.athena", "Athena", ("research.synthesize",)),
        "documentation.scribe": _spec("documentation.scribe", "Scribe", ("writing.report",)),
        "search.scout": _spec("search.scout", "Scout", ("web.search",)),
    }

    class Registry:
        def get(self, agent_id): return specs[agent_id]
        def list(self, enabled_only=True): return list(specs.values())

    class Runtime:
        registry = Registry()
        audit_log_path = tmp_path / "data" / "task_history.jsonl"
        def has_handler(self, agent_id): return agent_id in specs
        def executable_agent_ids(self): return set(specs)

    class Context:
        runtime = Runtime()
        agent = SimpleNamespace(agent_id="voice.jarvis")
        task = TaskRequest(
            prompt="Investigate our current codebase, verify agent-runtime delegation and message delivery, run tests, and produce a report.",
            task_id="12345678-aaaa-bbbb-cccc-123456789abc",
        )
        events = []
        def publish(self, event_type, payload): self.events.append((event_type, dict(payload)))
        async def delegate(self, *, prompt, agent_id, context, **kwargs):
            stage_name = context["stage"]
            if stage_name == "plan":
                output = {"summary": "Planner produced the diagnostic plan."}
            elif stage_name == "local_source_inspection":
                output = "Source evidence: core/runtime.py implements delegation."
            elif stage_name == "existing_test_suite":
                output = "Verification: FAILED (exit code 1)\n2 failed, 229 passed"
            elif stage_name == "independent_evidence_review":
                output = {"summary": "Athena reviewed supplied local evidence only."}
            else:
                output = {"summary": "Scribe wrote report.", "written_files": ["data/reports/agent_collaboration_test.md"]}
            return TaskResult(
                task_id=f"child-{len([e for e in self.events if e[0] == 'agent.workflow.stage.started'])}",
                agent_id=agent_id,
                status=TaskStatus.SUCCEEDED,
                output=output,
                summary="Handler returned normally.",
            )

    context = Context()
    result = asyncio.run(VoiceAgent()._run_agent_collaboration_diagnostic(context.task.prompt, context))
    assert result.status is TaskStatus.FAILED
    assert result.metadata["diagnostic_complete"] is False
    assert result.metadata["test_outcome"] == "failed"
    assert "LOCAL AGENT-SYSTEM DIAGNOSTIC — PARTIAL" in str(result.output)
    assert "Test outcome: failed" in str(result.output)


def test_forge_uses_typed_task_request_prompt_for_read_only_test_execution(tmp_path: Path, monkeypatch) -> None:
    from agents.coding_agent.agent import ForgeAgent

    forge = ForgeAgent(workspace=tmp_path, model=SimpleNamespace())
    observed: list[bool] = []
    def fake_run_tests(quiet: bool = True) -> str:
        observed.append(quiet)
        return "Verification: PASSED\nExit code: 0\n234 passed in 1.12s"
    monkeypatch.setattr(forge, "_run_tests", fake_run_tests)
    task = TaskRequest(
        prompt="Run the existing Python test suite for this local project. Do not modify files.",
        context={
            "workflow": "local_agent_system_audit",
            "stage": "existing_test_suite",
            "original_request": "Inspect source files and produce a report; do not edit application code.",
        },
    )
    result = asyncio.run(forge(task, context=None))
    assert observed == [False]
    assert "234 passed" in result


def test_local_source_inspection_requires_two_real_paths_and_symbols(tmp_path: Path) -> None:
    source = tmp_path / "core"
    source.mkdir()
    (source / "runtime.py").write_text(
        "class AgentRuntime:\n    async def run_task(self): pass\n", encoding="utf-8"
    )
    (source / "contracts.py").write_text(
        "class TaskResult: pass\nclass AgentMessage: pass\n", encoding="utf-8"
    )
    runtime = SimpleNamespace(audit_log_path=tmp_path / "data" / "task_history.jsonl")
    evidence = VoiceAgent._collect_local_source_evidence(runtime)
    valid = VoiceAgent._validate_local_source_inspection(
        "Evidence from core/runtime.py: AgentRuntime and run_task; core/contracts.py: TaskResult and AgentMessage.",
        evidence,
    )
    invalid = VoiceAgent._validate_local_source_inspection(
        "The runtime has a task handler and messages.", evidence
    )
    deterministic_without_pass = VoiceAgent._validate_local_source_inspection(
        "INSPECTION MODE: deterministic read-only source analysis\n"
        "FILE: core/runtime.py AgentRuntime run_task\n"
        "FILE: core/contracts.py TaskResult AgentMessage",
        evidence,
    )
    assert valid["valid"] is True
    assert set(valid["cited_paths"]) == {"core/runtime.py", "core/contracts.py"}
    assert invalid["valid"] is False
    assert deterministic_without_pass["valid"] is False
    assert "Verification: PASSED" in deterministic_without_pass["reason"] or "verification" in deterministic_without_pass["reason"]


def test_diagnostic_result_text_prefers_actual_test_output_over_generic_wrapper_summary() -> None:
    wrapped = SimpleNamespace(
        output={
            "summary": "Handler returned normally.",
            "test_output": "Verification: PASSED\nExit code: 0\n236 passed in 0.69s",
        },
        metadata={},
        summary="Completed",
    )
    text = VoiceAgent._diagnostic_result_text(wrapped)
    assert "236 passed" in text
    assert VoiceAgent._parse_explicit_test_outcome(text) == "passed"


def test_deterministic_source_inspection_truncation_preserves_real_terminal_verification() -> None:
    # Real repositories can produce an inspection longer than the coordinator's
    # 12k result bound. Prefix-only truncation used to remove the scanner's final
    # Verification marker and falsely fail otherwise valid offline evidence.
    raw = (
        "FORGE LOCAL SOURCE INSPECTION\n"
        "INSPECTION MODE: deterministic read-only source analysis; no model provider required.\n"
        "FILE: core/runtime.py\nDEFINED SYMBOLS: AgentRuntime (line 100, ClassDef), delegate (line 137, FunctionDef)\n"
        + ("SOURCE FACTS OBSERVED: runtime delegates task results.\n" * 500)
        + "Verification: PASSED"
    )
    result = SimpleNamespace(output=raw, metadata={}, summary="completed")
    bounded = VoiceAgent._diagnostic_result_text(result, limit=12000)
    assert len(bounded) <= 12000
    assert "FILE: core/runtime.py" in bounded
    assert "AgentRuntime" in bounded
    assert "[source evidence truncated; terminal verification retained]" in bounded
    assert bounded.endswith("Verification: PASSED")


def test_deterministic_source_inspection_truncation_does_not_invent_verification() -> None:
    raw = (
        "FORGE LOCAL SOURCE INSPECTION\n"
        "INSPECTION MODE: deterministic read-only source analysis; no model provider required.\n"
        "FILE: core/runtime.py\n"
        + ("SOURCE FACTS OBSERVED: no terminal result.\n" * 500)
    )
    bounded = VoiceAgent._diagnostic_result_text(SimpleNamespace(output=raw, metadata={}), limit=12000)
    assert len(bounded) <= 12000
    assert "Verification: PASSED" not in bounded


def test_forge_diagnostic_inspection_uses_provider_independent_real_source_scan(tmp_path: Path) -> None:
    """A slow/unavailable LLM must not block deterministic local evidence inspection."""
    import hashlib
    from agents.coding_agent.agent import ForgeAgent
    from core.contracts import TaskRequest

    runtime_dir = tmp_path / "core"
    tests_dir = tmp_path / "tests"
    runtime_dir.mkdir()
    tests_dir.mkdir()
    runtime_source = (
        "class AgentContext:\n"
        "    async def delegate(self):\n        pass\n"
        "    async def send_message(self):\n        pass\n"
        "    async def receive_message(self):\n        pass\n"
        "\nclass AgentRuntime:\n"
        "    async def run_task(self):\n        pass\n"
        "    async def get_task_result(self):\n        pass\n"
    )
    test_source = (
        "def test_runtime_delegates_and_preserves_correlation():\n    assert True\n\n"
        "def test_targeted_agent_message_is_delivered_to_inbox():\n    assert True\n"
    )
    runtime_path = runtime_dir / "runtime.py"
    tests_path = tests_dir / "test_network.py"
    runtime_path.write_text(runtime_source, encoding="utf-8")
    tests_path.write_text(test_source, encoding="utf-8")
    before_hashes = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (runtime_path, tests_path)
    }

    class ModelMustNotRun:
        async def complete(self, *_args, **_kwargs):
            raise AssertionError("The local diagnostic source scan must not call the configured model provider")

    agent = ForgeAgent(workspace=tmp_path, model=ModelMustNotRun())
    task = TaskRequest(
        prompt="Inspect local runtime source evidence read-only; do not modify files.",
        context={
            "workflow": "local_agent_system_audit",
            "stage": "local_source_inspection",
            "local_source_paths": ["core/runtime.py", "tests/test_network.py"],
            "local_source_symbols": [
                "AgentContext", "AgentRuntime", "delegate", "send_message", "receive_message",
                "run_task", "get_task_result", "test_runtime_delegates_and_preserves_correlation",
                "test_targeted_agent_message_is_delivered_to_inbox",
            ],
        },
    )

    output = asyncio.run(agent(task, context=None))
    assert "INSPECTION MODE: deterministic read-only source analysis" in output
    assert "no model provider required" in output
    assert "FILE: core/runtime.py" in output
    assert "FILE: tests/test_network.py" in output
    assert "AgentRuntime" in output and "test_targeted_agent_message_is_delivered_to_inbox" in output
    assert "Verification: PASSED" in output

    evidence = {
        "paths": ["core/runtime.py", "tests/test_network.py"],
        "symbols": [
            "AgentContext", "AgentRuntime", "delegate", "send_message", "receive_message",
            "run_task", "get_task_result", "test_runtime_delegates_and_preserves_correlation",
            "test_targeted_agent_message_is_delivered_to_inbox",
        ],
    }
    validated = VoiceAgent._validate_local_source_inspection(output, evidence)
    assert validated["valid"] is True
    assert set(validated["cited_paths"]) == {"core/runtime.py", "tests/test_network.py"}
    after_hashes = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (runtime_path, tests_path)
    }
    assert after_hashes == before_hashes
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()) == [
        "core/runtime.py", "tests/test_network.py"
    ]



def test_full_diagnostic_completes_with_real_forge_when_model_provider_is_unavailable(tmp_path: Path) -> None:
    """The actual diagnostic route must not depend on Forge's LLM for source inspection."""
    from agents.coding_agent.agent import ForgeAgent

    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "core" / "runtime.py").write_text(
        "class AgentContext:\n"
        "    async def delegate(self): pass\n"
        "    async def send_message(self): pass\n"
        "    async def receive_message(self): pass\n\n"
        "class AgentRuntime:\n"
        "    async def run_task(self): pass\n"
        "    async def get_task_result(self): pass\n",
        encoding="utf-8",
    )
    (tmp_path / "core" / "contracts.py").write_text(
        "class AgentMessage: pass\nclass TaskRequest: pass\nclass TaskResult: pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_network.py").write_text(
        "# AgentMessage TaskRequest TaskResult delegation correlation inbox evidence\n"
        "def test_runtime_delegates_and_preserves_correlation():\n    assert True\n\n"
        "def test_targeted_agent_message_is_delivered_to_inbox():\n    assert True\n",
        encoding="utf-8",
    )

    class ModelMustNotRun:
        async def complete(self, *_args, **_kwargs):
            raise AssertionError("Forge provider should not be required for local diagnostic inspection")

    audit_path = tmp_path / "data" / "task_history.jsonl"
    runtime = AgentRuntime(
        registry=AgentRegistry(),
        bus=EventBus(),
        max_delegation_depth=6,
        audit_log_path=audit_path,
    )
    forge = ForgeAgent(workspace=tmp_path, model=ModelMustNotRun())
    runtime.register_agent(_spec("voice.jarvis", "JARVIS", ("conversation.handle", "task.coordinate")), VoiceAgent())
    runtime.register_agent(_spec("planning.planner", "Planner", ("planning.breakdown",)), PlannerAgent())
    runtime.register_agent(_spec("coding.forge", "Forge", ("code.inspect", "code.test")), forge)
    runtime.register_agent(_spec("research.athena", "Athena", ("research.synthesize", "research.verify")), ResearchAgent())
    runtime.register_agent(_spec("documentation.scribe", "Scribe", ("writing.report",)), DocumentationAgent())

    prompt = (
        "Investigate the actual local codebase, verify agent collaboration and inbox delivery, "
        "inspect source evidence, run existing tests, and produce an evidence-backed report. "
        "Do not modify application code."
    )
    root_task = TaskRequest(prompt=prompt, target_agent_id="voice.jarvis", capabilities=("conversation.handle",))
    result = asyncio.run(runtime.run_task(root_task))
    assert result.status is TaskStatus.SUCCEEDED, result.error or result.output
    output = str(result.output)
    assert "LOCAL AGENT-SYSTEM DIAGNOSTIC — SUCCESS" in output
    assert "execution mode=deterministic_read_only_source_analysis" in output
    assert "Test outcome: passed" in output
    assert "data/reports/agent_collaboration_test" in output

    delegated = [item for item in runtime._task_results.values() if item.task_id != root_task.task_id]
    assert {item.agent_id for item in delegated} >= {
        "planning.planner", "coding.forge", "research.athena", "documentation.scribe"
    }
    assert sum(1 for item in delegated if item.agent_id == "coding.forge") == 2
    assert (tmp_path / "data" / "reports").is_dir()
    written_reports = list((tmp_path / "data" / "reports").glob("agent_collaboration_test*.md"))
    assert len(written_reports) == 1
    report_text = written_reports[0].read_text(encoding="utf-8")
    assert "deterministic_read_only_source_analysis" in report_text
    assert "core/runtime.py" in report_text
    assert "test_targeted_agent_message_is_delivered_to_inbox" in report_text
