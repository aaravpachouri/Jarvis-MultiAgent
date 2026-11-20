"""Launch: read-only deployment planning and release preflight.

Launch inspects a small allowlist of project configuration files. It never runs
commands, reads environment files, contacts providers, changes infrastructure,
or claims a release/health check succeeded.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from core.action_scope import action_scope


class DeploymentAgent:
    """Build an evidence-backed deployment plan without performing deployment."""

    _CONFIG_FILES = (
        "vercel.json", "render.yaml", "Dockerfile", "docker-compose.yml",
        "docker-compose.yaml", "Procfile", "netlify.toml", "fly.toml",
        "railway.json", "railway.toml", "package.json", "pyproject.toml",
        "requirements.txt", "requirements-dev.txt", "Pipfile", "poetry.lock", "uv.lock",
        ".github/workflows",
    )

    async def __call__(self, task: Any, context: Any) -> dict[str, Any]:
        prompt = str(getattr(task, "prompt", "") or "").strip()
        if not prompt:
            raise ValueError("Launch requires a non-empty deployment planning request.")
        root = self._workspace_root(context)
        if root is None:
            return {
                "agent": "Launch", "status": "needs_workspace", "plan": [],
                "evidence": [], "blockers": ["The active project workspace could not be identified safely."],
                "executed_commands": [], "deployment_performed": False,
                "health_check_performed": False,
                "summary": "Launch needs an identified local project workspace before it can prepare a repository-specific release plan.",
            }
        async with action_scope(
            context, "launch.deployment.preflight", action_type="workflow",
            metadata={"operation": "read_only_preflight", "phase": "inspect_configuration"},
        ):
            evidence, package = self._inspect(root)
        return self._build_plan(prompt, root, evidence, package)

    @staticmethod
    def _workspace_root(context: Any) -> Path | None:
        runtime = getattr(context, "runtime", None)
        audit_path = getattr(runtime, "audit_log_path", None)
        if not audit_path:
            return None
        try:
            audit = Path(audit_path).expanduser().resolve()
            root = audit.parent.parent
            return root if root.is_dir() else None
        except (OSError, RuntimeError, ValueError):
            return None

    @classmethod
    def _inspect(cls, root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        package: dict[str, Any] = {}
        for relative in cls._CONFIG_FILES:
            candidate = root / relative
            try:
                if not candidate.exists():
                    continue
                resolved = candidate.resolve()
                if resolved != root and root not in resolved.parents:
                    continue
                if relative == ".github/workflows":
                    if candidate.is_dir():
                        workflows = sorted(p.name for p in candidate.glob("*.yml") if p.is_file())
                        workflows += sorted(p.name for p in candidate.glob("*.yaml") if p.is_file())
                        evidence.append({"path": relative, "kind": "directory", "items": workflows[:20]})
                    continue
                if not candidate.is_file() or candidate.stat().st_size > 256_000:
                    continue
                item: dict[str, Any] = {"path": relative, "kind": "file"}
                if relative == "package.json":
                    try:
                        data = json.loads(candidate.read_text(encoding="utf-8"))
                        if isinstance(data, dict):
                            scripts = data.get("scripts", {})
                            package = {
                                "name": data.get("name") if isinstance(data.get("name"), str) else None,
                                "package_manager": data.get("packageManager") if isinstance(data.get("packageManager"), str) else None,
                                "scripts": {
                                    key: value for key, value in scripts.items()
                                    if key in {"build", "test", "lint", "typecheck", "start", "dev"}
                                    and isinstance(value, str)
                                } if isinstance(scripts, dict) else {},
                            }
                            item["detected_scripts"] = sorted(package["scripts"])
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        item["parse_warning"] = "package.json exists but could not be parsed."
                evidence.append(item)
            except (OSError, PermissionError, RuntimeError):
                continue
        return evidence, package

    @staticmethod
    def _build_plan(prompt: str, root: Path, evidence: list[dict[str, Any]], package: Mapping[str, Any]) -> dict[str, Any]:
        paths = {item["path"] for item in evidence}
        blockers: list[str] = []
        recommendations: list[str] = []
        if not paths:
            blockers.append("No recognized deployment or build configuration was found in the workspace.")
        if "package.json" in paths and not package.get("scripts", {}).get("build"):
            blockers.append("package.json has no detected build script; confirm the correct build command.")
        if "Dockerfile" in paths and not any(name in paths for name in ("render.yaml", "fly.toml", "railway.json", "railway.toml")):
            recommendations.append("Confirm the target container platform, exposed port, health-check path and start command.")
        if "vercel.json" in paths:
            recommendations.append("Review Vercel framework/build settings, output directory, environment-variable names and deployment protection.")
        if "render.yaml" in paths:
            recommendations.append("Review Render service type, build/start commands, health-check path and secret references.")
        if "package.json" in paths and package.get("scripts", {}).get("test"):
            recommendations.append("Run the repository test script in CI before any production release.")
        elif "pyproject.toml" in paths or "requirements.txt" in paths:
            recommendations.append("Confirm the Python environment, dependency lock strategy, import/compile checks and test command.")
        recommendations.extend([
            "Verify required environment-variable names in the hosting dashboard without exposing their values.",
            "Confirm domain, TLS, access controls, logs, rollback target and an owner for post-release monitoring.",
            "After an approved release, perform an external health check and record its URL, timestamp, status and response evidence.",
        ])
        plan = [
            {"step": 1, "task": "Confirm deployment target and ownership", "gate": "Platform, project, environment and authorized operator are identified."},
            {"step": 2, "task": "Validate build and test configuration", "gate": "Build and regression checks pass in CI."},
            {"step": 3, "task": "Review runtime configuration and secrets", "gate": "Required variable names and secret references are configured securely; values remain private."},
            {"step": 4, "task": "Review release safety", "gate": "Access controls, domain, TLS, health check and rollback plan are documented."},
            {"step": 5, "task": "Request explicit release approval", "gate": "A human authorizes the specific production change."},
            {"step": 6, "task": "Verify the released service", "gate": "A real health check and smoke test produce timestamped evidence."},
        ]
        return {
            "agent": "Launch", "status": "blocked" if blockers else "ready_for_review",
            "workspace": root.name, "request": prompt[:500], "evidence": evidence,
            "package_metadata": dict(package), "blockers": blockers,
            "recommendations": list(dict.fromkeys(recommendations)), "plan": plan,
            "executed_commands": [], "infrastructure_changed": False,
            "deployment_performed": False, "health_check_performed": False,
            "approval_required_before_release": True,
            "summary": (
                "Launch prepared a read-only deployment preflight. No build, deployment, external request or infrastructure change was performed."
                if not blockers else
                "Launch found deployment-readiness blockers. No build, deployment, external request or infrastructure change was performed."
            ),
        }


def create_handler() -> DeploymentAgent:
    return DeploymentAgent()
