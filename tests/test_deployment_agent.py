from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.deployment_agent.agent import DeploymentAgent


def _context(root: Path):
    audit = root / "data" / "task_history.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.touch()
    return SimpleNamespace(runtime=SimpleNamespace(audit_log_path=audit))


@pytest.mark.asyncio
async def test_launch_creates_evidence_based_read_only_preflight(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "sample-app",
        "scripts": {"build": "vite build", "test": "vitest run", "dev": "vite"},
    }), encoding="utf-8")
    (tmp_path / "vercel.json").write_text('{"framework":"vite"}', encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: CI", encoding="utf-8")
    (tmp_path / ".env.local").write_text("SECRET=must-not-be-read", encoding="utf-8")

    result = await DeploymentAgent()(
        SimpleNamespace(prompt="Prepare a deployment plan for this app."),
        _context(tmp_path),
    )

    assert result["status"] == "ready_for_review"
    assert {item["path"] for item in result["evidence"]} >= {"package.json", "vercel.json", ".github/workflows"}
    assert result["package_metadata"]["scripts"] == {"build": "vite build", "test": "vitest run"}
    assert ".env.local" not in {item["path"] for item in result["evidence"]}
    assert result["executed_commands"] == []
    assert result["deployment_performed"] is False
    assert result["health_check_performed"] is False
    assert result["approval_required_before_release"] is True


@pytest.mark.asyncio
async def test_launch_reports_blockers_when_no_deployment_configuration_exists(tmp_path: Path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    result = await DeploymentAgent()(
        SimpleNamespace(prompt="Check deployment readiness."),
        _context(tmp_path),
    )
    assert result["status"] == "blocked"
    assert result["blockers"]
    assert result["deployment_performed"] is False


@pytest.mark.asyncio
async def test_launch_does_not_accept_arbitrary_workspace_path_from_task_context(tmp_path: Path):
    class Context:
        runtime = SimpleNamespace(audit_log_path=None)

    result = await DeploymentAgent()(
        SimpleNamespace(prompt="Deploy it.", context={"workspace_root": str(tmp_path)}),
        Context(),
    )
    assert result["status"] == "needs_workspace"
    assert result["deployment_performed"] is False


@pytest.mark.asyncio
async def test_launch_rejects_empty_prompt(tmp_path: Path):
    with pytest.raises(ValueError, match="non-empty"):
        await DeploymentAgent()(SimpleNamespace(prompt=""), _context(tmp_path))
