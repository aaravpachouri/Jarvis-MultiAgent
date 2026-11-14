"""Security and release-readiness audit for the local JARVIS agent ecosystem.

The report intentionally lists environment variable NAMES only, never values.
Run with: python -m core.hardening [--json-out path]
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from .bootstrap import PROJECT_ROOT, create_runtime, discover_manifests
from .policy import manifest_policy_issues

_PLACEHOLDER_MARKERS = (
    "your_", "your-", "replace_me", "changeme", "change_me", "placeholder",
    "paste_", "insert_", "example_key", "xxxx", "<your", "<insert", "todo",
)
_SECRET_NAME = re.compile(r"(API[_-]?KEY|TOKEN|SECRET|PASSWORD|ACCESS[_-]?KEY)$", re.IGNORECASE)


def _parse_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return result
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[key] = value
    return result


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    return any(marker in normalized for marker in _PLACEHOLDER_MARKERS) or normalized in {"xxx", "none", "null"}


def _gitignore_protects_env(project_root: Path) -> bool:
    path = project_root / ".gitignore"
    if not path.is_file():
        return False
    try:
        lines = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
    except OSError:
        return False
    return ".env" in lines and ".env.*" in lines


def build_hardening_report(project_root: str | os.PathLike[str] = PROJECT_ROOT) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    manifest_count = 0
    manifest_ids: set[str] = set()
    manifests = []

    try:
        manifests = discover_manifests(root / "agents")
        manifest_count = len(manifests)
    except Exception as exc:
        errors.append(f"Manifest discovery/validation failed: {type(exc).__name__}: {exc}")

    for _folder, spec in manifests:
        if spec.agent_id in manifest_ids:
            errors.append(f"Duplicate manifest agent ID: {spec.agent_id}")
        manifest_ids.add(spec.agent_id)
        errors.extend(manifest_policy_issues(spec))

    gitignore_ok = _gitignore_protects_env(root)
    if (root / ".env").exists() and not gitignore_ok:
        errors.append(".env exists but .gitignore does not clearly exclude both .env and .env.*")
    elif not gitignore_ok:
        warnings.append(".gitignore does not clearly exclude both .env and .env.*")

    env_path = root / ".env"
    env_values = _parse_env_file(env_path)
    placeholder_keys = sorted(key for key, value in env_values.items() if _SECRET_NAME.search(key) and _is_placeholder(value))
    if placeholder_keys:
        warnings.append("Secret/configuration values are blank or placeholders for keys: " + ", ".join(placeholder_keys))

    example_path = root / ".env.example"
    example_values = _parse_env_file(example_path)
    suspicious_example_keys = sorted(
        key for key, value in example_values.items()
        if _SECRET_NAME.search(key) and value and not _is_placeholder(value)
        and len(value) >= 20 and not value.startswith(("${", "$env:"))
    )
    if suspicious_example_keys:
        errors.append(".env.example may contain non-placeholder secret values for keys: " + ", ".join(suspicious_example_keys))

    runtime_summary: dict[str, Any] = {"available": False, "executable_agents": 0, "registered_only_agents": 0, "profile_only_mismatches": []}
    try:
        runtime = create_runtime(project_root=root, registry_path=root / "data" / "agent_registry.json")
        agents = runtime.list_agents()
        executable = runtime.executable_agent_ids()
        mismatches = []
        for agent in agents:
            profile_only = agent.get("metadata", {}).get("profile_only") is True
            is_executable = agent["agent_id"] in executable
            if profile_only and is_executable:
                mismatches.append(agent["agent_id"])
            if not profile_only and not is_executable:
                warnings.append(f"Agent '{agent['agent_id']}' is not executable despite not being marked profile-only")
        if mismatches:
            errors.append("Profile-only agents unexpectedly have executable handlers: " + ", ".join(sorted(mismatches)))
        runtime_summary = {
            "available": True,
            "agent_identities": len(agents),
            "executable_agents": len(executable),
            "registered_only_agents": max(0, len(agents) - len(executable)),
            "profile_only_mismatches": sorted(mismatches),
        }
    except Exception as exc:
        errors.append(f"Runtime readiness check failed: {type(exc).__name__}: {exc}")

    return {
        "schema_version": 1,
        "project_root": str(root),
        "status": "failed" if errors else ("warnings" if warnings else "passed"),
        "manifests_checked": manifest_count,
        "env_file_present": env_path.is_file(),
        "env_values_exposed": False,
        "gitignore_env_protection": gitignore_ok,
        "runtime": runtime_summary,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "manifest_schema_and_unique_ids": not any("manifest" in item.lower() or "agent id" in item.lower() for item in errors),
            "sensitive_capabilities_have_approval_policy": not any("requires approval token" in item for item in errors),
            "profile_only_agents_not_executable": not bool(runtime_summary.get("profile_only_mismatches")),
            "secrets_not_printed": True,
        },
    }


def render_report(report: dict[str, Any]) -> str:
    runtime = report["runtime"]
    lines = [
        "JARVIS ECOSYSTEM HARDENING AUDIT",
        "================================",
        f"Status:                    {report['status'].upper()}",
        f"Manifests checked:         {report['manifests_checked']}",
        f"Runtime readiness check:   {'available' if runtime['available'] else 'unavailable'}",
        f"Executable agents:         {runtime.get('executable_agents', 0)}",
        f"Registered-only agents:    {runtime.get('registered_only_agents', 0)}",
        f".env present:              {report['env_file_present']}",
        f".env values printed:       {report['env_values_exposed']}",
        f"Gitignore protects env:    {report['gitignore_env_protection']}",
        "",
        f"ERRORS ({len(report['errors'])})",
        "------",
    ]
    lines.extend(f"- {item}" for item in report["errors"] or ["None"])
    lines.extend(["", f"WARNINGS ({len(report['warnings'])})", "--------"])
    lines.extend(f"- {item}" for item in report["warnings"] or ["None"])
    lines.extend(["", "CHECKS", "------"])
    lines.extend(f"{'PASS' if value else 'FAIL'}  {key}" for key, value in report["checks"].items())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit JARVIS manifests, approval policies, configuration safety, and runtime readiness.")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT, help="JARVIS project root")
    parser.add_argument("--json-out", type=Path, help="Optional path to write a JSON audit report")
    args = parser.parse_args(argv)
    root = args.project_root.expanduser().resolve()
    if not (root / "core" / "runtime.py").is_file() or not (root / "agents").is_dir():
        parser.error(f"not a JARVIS project root: {root}")
    report = build_hardening_report(root)
    if args.json_out:
        output = args.json_out.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Hardening JSON written to: {output}")
    print(render_report(report))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
