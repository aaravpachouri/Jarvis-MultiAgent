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
    assert manifest["metadata"]["execution_mode"] == "draft_only"
    assert manifest["metadata"]["writes_files"] is False
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
