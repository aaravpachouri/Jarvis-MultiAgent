"""Phase 8 tests for safe Scribe activation and evidence-grounded drafting."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agents.documentation_agent.agent import DocumentationAgent, create_handler
from core.contracts import AgentSpec, TaskRequest, TaskStatus
from core.registry import AgentRegistry
from core.runtime import AgentRuntime


def _spec() -> AgentSpec:
    return AgentSpec(
        agent_id="documentation.scribe",
        name="Scribe",
        role="Documentation drafting",
        description="Evidence-grounded documentation draft generator",
        capabilities=("documentation.write", "documentation.api", "documentation.architecture"),
        metadata={"profile_only": False, "execution_mode": "draft_only"},
    )


def test_scribe_factory_returns_real_callable_handler():
    handler = create_handler()
    assert isinstance(handler, DocumentationAgent)
    assert callable(handler)


def test_scribe_is_executable_and_returns_a_draft_without_side_effects():
    runtime = AgentRuntime(registry=AgentRegistry())
    runtime.register_agent(_spec(), create_handler())
    request = TaskRequest(
        prompt="Write a README for this project",
        target_agent_id="documentation.scribe",
        capabilities=("documentation.write",),
        context={
            "project_name": "OpenSTEM Example",
            "project_summary": "An offline-first learning platform.",
            "features": ["Bilingual lessons", "Interactive simulations"],
            "usage_examples": ["Open the home page and choose a subject."],
            "source_material": ["Provided project brief"],
        },
    )
    result = asyncio.run(runtime.run_task(request))
    assert result.status is TaskStatus.SUCCEEDED
    assert isinstance(result.output, dict)
    assert result.output["draft_only"] is True
    assert result.output["written_files"] == []
    assert result.output["commands_executed"] == []
    assert "OpenSTEM Example" in result.output["content"]
    assert "Bilingual lessons" in result.output["content"]
    assert "offline-first learning platform" in result.output["content"]


def test_scribe_does_not_invent_test_results_or_project_facts():
    handler = create_handler()
    request = TaskRequest(prompt="Write release notes", target_agent_id="documentation.scribe", capabilities=("documentation.release_notes",))
    output = asyncio.run(handler(request, None))
    content = output["content"]
    assert output["draft_only"] is True
    assert output["independently_verified"] is False
    assert "No test results were supplied" in content
    assert "Missing context" in content
    assert "passed" not in content.casefold()


def test_scribe_manifest_marks_safe_draft_mode_not_profile_only():
    manifest_path = Path(__file__).parents[1] / "agents" / "documentation_agent" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["metadata"]["profile_only"] is False
    assert manifest["metadata"]["execution_mode"] == "draft_only_with_scoped_diagnostic_report_persistence"
    assert manifest["metadata"]["writes_files"] == "coordinator_authorized_local_diagnostic_report_only"
    assert "coordinator-authorized local agent-system diagnostic" in manifest["description"]
    assert manifest["requires_approval_for"]


def test_scribe_drafts_email_from_supplied_details_without_inventing_facts():
    task = TaskRequest(
        prompt="Draft an email to the project team",
        target_agent_id="documentation.scribe",
        context={
            "recipient": "Project team",
            "subject": "Launch review",
            "purpose": "Please review the launch checklist before Friday.",
            "key_points": ["Confirm the owner for each open item", "Report blockers"],
            "sender": "Aarav",
        },
    )
    output = asyncio.run(create_handler()(task, None))
    assert output["document_type"] == "email"
    assert "To: Project team" in output["content"]
    assert "Subject: Launch review" in output["content"]
    assert "Report blockers" in output["content"]
    assert output["draft_only"] is True
    assert output["written_files"] == []
    assert output["independently_verified"] is False


def test_scribe_drafts_meeting_notes_with_action_checklist():
    task = TaskRequest(
        prompt="Create meeting notes",
        target_agent_id="documentation.scribe",
        context={
            "title": "Sprint review",
            "date": "2026-10-19",
            "attendees": ["Design", "Engineering"],
            "key_points": ["Search latency needs investigation"],
            "decisions": ["Keep Forge out of the current sprint"],
            "action_items": ["Measure baseline latency"],
        },
    )
    output = asyncio.run(create_handler()(task, None))
    assert output["document_type"] == "meeting notes"
    assert "2026-10-19" in output["content"]
    assert "- [ ] Measure baseline latency" in output["content"]
    assert "Keep Forge out of the current sprint" in output["content"]


def test_scribe_renders_complete_mission_report_from_structured_research_evidence():
    task = TaskRequest(
        prompt=(
            "Prepare the complete final report. Compare Qwen 3 4B, Llama 3.2 3B "
            "and Gemma 3 4B, include official sources, limitations and the agent trace."
        ),
        target_agent_id="documentation.scribe",
        context={
            "workflow": "complex_mission",
            "stage": "final_report_draft",
            "read_only": True,
            "planner_result": {
                "agent_id": "planning.planner",
                "task_id": "planner-task-1",
                "status": "succeeded",
            },
            "workstream_results": {
                "workstream_1": {
                    "agent_id": "research.athena",
                    "capability": "research.deep",
                    "task_id": "athena-task-1",
                    "status": "quality_gate_failed",
                    "error": "Research quality gate failed: insufficient_model_target_dimension_coverage",
                    "research_evidence": {
                        "complete": False,
                        "research_status": "insufficient_model_target_dimension_coverage",
                        "requested_model_targets": ["Gemma 3 4B"],
                        "model_target_coverage": {
                            "Gemma 3 4B": ["https://ai.google.dev/gemma/docs/core/model_card_3"]
                        },
                        "model_target_extracted_coverage": {
                            "Gemma 3 4B": ["https://ai.google.dev/gemma/docs/core/model_card_3"]
                        },
                        "technical_dimensions_required": ["Reasoning", "Speed"],
                        "model_target_dimension_matrix": {
                            "Gemma 3 4B": {
                                "Reasoning": [{
                                    "excerpt": "Gemma 3 4B reports a reasoning benchmark.",
                                    "url": "https://ai.google.dev/gemma/docs/core/model_card_3",
                                }],
                                "Speed": [],
                            }
                        },
                        "model_target_dimension_coverage": {
                            "Gemma 3 4B": {"Reasoning": True, "Speed": False}
                        },
                        "query_provenance": [{
                            "delegated_agent_id": "search.scout",
                            "capability": "web.search",
                            "delegation_task_id": "scout-task-1",
                            "status": "complete",
                            "result_count": 5,
                            "query": "Gemma 3 4B official model card",
                        }],
                        "sources": [{
                            "title": "Gemma 3 model card",
                            "url": "https://ai.google.dev/gemma/docs/core/model_card_3",
                            "provider": "Tavily Search API",
                            "retrieved_at": "2026-11-16T10:00:00+00:00",
                        }],
                    },
                }
            },
            "body": "This generic body must not replace the structured mission report.",
            "limitations": ["No matched Windows hardware benchmark was provided."],
        },
    )

    output = asyncio.run(create_handler()(task, None))
    report = output["content"]

    assert output["structured_report"] is True
    assert output["draft_only"] is True
    assert output["written_files"] == []
    assert "## Executive recommendation" in report
    assert "Recommendation withheld" in report
    assert "## Model-by-model comparison" in report
    assert "Gemma 3 4B" in report
    assert "Speed" in report
    assert "scout-task-1" in report
    assert "planner-task-1" in report
    assert "https://ai.google.dev/gemma/docs/core/model_card_3" in report
    assert "Limitations and unresolved gaps" in report
    assert "No same-hardware Windows 11 benchmark" in report
    assert "This generic body must not replace" not in report


def test_scribe_email_without_context_uses_clear_placeholders_not_fake_body() -> None:
    output = asyncio.run(create_handler()(
        TaskRequest(prompt="Draft a professional email to the project team"), None
    ))
    content = output["content"]
    assert "Subject: [Subject]" in content
    assert "[topic/purpose]" in content
    assert "[Add the key message" in content
    assert "Documentation Draft" not in content
    assert "purpose_or_body_or_key_points" in output["missing_context"]
