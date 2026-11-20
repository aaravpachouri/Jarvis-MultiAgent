import asyncio
import json
from pathlib import Path

from core.contracts import AgentSpec, TaskRequest, TaskStatus
from core.hardening import build_hardening_report, render_report
from core.policy import manifest_policy_issues, missing_approvals
from core.registry import AgentRegistry
from core.runtime import AgentRuntime


def _sensitive_spec(*, approval=True):
    return AgentSpec(
        agent_id="test.writer",
        name="Test Writer",
        role="writer",
        description="test only",
        capabilities=("filesystem.write",),
        requires_approval_for=("filesystem.write",) if approval else (),
    )


def test_sensitive_capability_is_blocked_without_explicit_approval(tmp_path):
    invoked = {"value": False}
    runtime = AgentRuntime(registry=AgentRegistry(), audit_log_path=tmp_path / "history.jsonl")

    async def handler(task, context):
        invoked["value"] = True
        return "wrote file"

    runtime.register_agent(_sensitive_spec(), handler)
    result = asyncio.run(runtime.run_task(TaskRequest(
        prompt="write a file", target_agent_id="test.writer", capabilities=("filesystem.write",)
    )))

    assert result.status is TaskStatus.FAILED
    assert result.metadata["policy_denied"] is True
    assert result.metadata["missing_approvals"] == ["filesystem.write"]
    assert not invoked["value"]


def test_sensitive_capability_runs_only_with_explicit_approval(tmp_path):
    runtime = AgentRuntime(registry=AgentRegistry(), audit_log_path=tmp_path / "history.jsonl")

    async def handler(task, context):
        return "approved write"

    runtime.register_agent(_sensitive_spec(), handler)
    result = asyncio.run(runtime.run_task(TaskRequest(
        prompt="write a file", target_agent_id="test.writer", capabilities=("filesystem.write",),
        context={"approved_actions": ["filesystem.write"]},
    )))

    assert result.status is TaskStatus.SUCCEEDED
    assert result.output == "approved write"


def test_explicit_requested_action_requires_approval_even_without_capability_field():
    spec = _sensitive_spec()
    request = TaskRequest(prompt="release", target_agent_id="test.writer", context={"requested_actions": ["filesystem.write"]})
    assert missing_approvals(spec, request) == ("filesystem.write",)
    approved = TaskRequest(
        prompt="release", target_agent_id="test.writer",
        context={"requested_actions": ["filesystem.write"], "approved_actions": ["filesystem.write"]},
    )
    assert missing_approvals(spec, approved) == ()


def test_target_agent_cannot_bypass_capability_contract(tmp_path):
    invoked = {"value": False}
    runtime = AgentRuntime(registry=AgentRegistry(), audit_log_path=tmp_path / "history.jsonl")

    async def handler(task, context):
        invoked["value"] = True
        return "should not run"

    runtime.register_agent(AgentSpec(
        agent_id="test.reader", name="Test Reader", role="reader", description="test",
        capabilities=("filesystem.read",),
    ), handler)
    result = asyncio.run(runtime.run_task(TaskRequest(
        prompt="write a file", target_agent_id="test.reader", capabilities=("filesystem.write",)
    )))
    assert result.status is TaskStatus.FAILED
    assert result.metadata["policy_denied"] is True
    assert not invoked["value"]


def test_manifest_policy_detects_missing_approval_token():
    issues = manifest_policy_issues(_sensitive_spec(approval=False))
    assert any("filesystem.write" in issue and "approval token" in issue for issue in issues)


def test_project_hardening_report_does_not_expose_env_values():
    root = Path(__file__).resolve().parents[1]
    report = build_hardening_report(root)
    rendered = render_report(report)
    assert report["env_values_exposed"] is False
    assert "JARVIS ECOSYSTEM HARDENING AUDIT" in rendered
    assert "OPENAI_API_KEY=" not in rendered
    assert report["manifests_checked"] >= 8
    assert report["runtime"]["available"] is True
    assert report["runtime"]["profile_only_mismatches"] == []
    assert not report["errors"], json.dumps(report["errors"], indent=2)
