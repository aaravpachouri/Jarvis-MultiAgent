"""Local JARVIS runtime diagnostics and persistent task-history reporting.

The audit log intentionally stores lifecycle metadata only; prompts, model output,
API keys, and exception text are excluded by the runtime's audit writer.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .bootstrap import PROJECT_ROOT, create_runtime

TERMINAL_EVENTS = {
    "task.succeeded", "task.failed", "task.timed_out", "task.cancelled"
}


def read_task_history(path: str | Path, *, limit: int = 20) -> dict[str, Any]:
    """Read task lifecycle JSONL safely, reporting malformed lines without failing."""
    source = Path(path)
    events: list[dict[str, Any]] = []
    malformed_lines = 0
    if source.exists():
        try:
            with source.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict) and isinstance(value.get("event"), str):
                            events.append(value)
                        else:
                            malformed_lines += 1
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        malformed_lines += 1
        except OSError:
            malformed_lines += 1
    terminal = [event for event in events if event.get("event") in TERMINAL_EVENTS]
    counts = Counter(str(event.get("status", "unknown")) for event in terminal)
    # A task may have multiple terminal events only if a caller reuses a task ID;
    # report lifecycle records honestly rather than silently collapsing history.
    recent = terminal[-max(0, limit):] if limit else []
    return {
        "path": str(source),
        "event_count": len(events),
        "terminal_event_count": len(terminal),
        "status_counts": dict(sorted(counts.items())),
        "malformed_lines": malformed_lines,
        "recent_tasks": recent,
    }


def build_diagnostics(runtime, *, history_path: str | Path | None = None, history_limit: int = 20) -> dict[str, Any]:
    """Build a machine-readable readiness, runtime, and task-history snapshot."""
    agents = runtime.list_agents()
    status_counts = Counter(str(agent.get("status", "unknown")) for agent in agents)
    executable_ids = sorted(runtime.executable_agent_ids())
    if history_path is None:
        history_path = getattr(runtime, "audit_log_path", None)
    history = read_task_history(history_path, limit=history_limit) if history_path else {
        "path": None, "event_count": 0, "terminal_event_count": 0,
        "status_counts": {}, "malformed_lines": 0, "recent_tasks": [],
    }
    return {
        "schema_version": 1,
        "runtime": {
            "registered_agents": len(agents),
            "executable_agents": len(executable_ids),
            "registered_only_agents": max(0, len(agents) - len(executable_ids)),
            "agent_status_counts": dict(sorted(status_counts.items())),
            "executable_agent_ids": executable_ids,
            "audit_write_errors": int(getattr(runtime, "audit_errors", 0)),
        },
        "task_history": history,
    }


def render_diagnostics(report: dict[str, Any]) -> str:
    """Render a compact human-readable health report."""
    runtime = report["runtime"]
    history = report["task_history"]
    statuses = runtime["agent_status_counts"]
    lines = [
        "JARVIS ECOSYSTEM DIAGNOSTICS",
        "=" * 29,
        f"Agent identities:       {runtime['registered_agents']}",
        f"Executable agents:      {runtime['executable_agents']}",
        f"Registered-only agents: {runtime['registered_only_agents']}",
        "Agent statuses:         " + (", ".join(f"{key}={value}" for key, value in statuses.items()) or "none"),
        f"Audit write errors:     {runtime['audit_write_errors']}",
        "",
        "PERSISTED TASK HISTORY",
        "-" * 23,
        f"Log:                    {history['path'] or '(not configured)'}",
        f"Lifecycle events:       {history['event_count']}",
        f"Terminal task events:   {history['terminal_event_count']}",
        f"Malformed log lines:    {history['malformed_lines']}",
        "Task outcomes:          " + (", ".join(f"{key}={value}" for key, value in history["status_counts"].items()) or "none yet"),
    ]
    if history["recent_tasks"]:
        lines.extend(["", "RECENT TASKS", "------------"])
        for event in history["recent_tasks"]:
            error_type = event.get("error_type")
            error_label = f" error_type={error_type}" if error_type else ""
            lines.append(
                f"{event.get('status', 'unknown'):<10} agent={event.get('agent_id', '?')} "
                f"task={event.get('task_id', '?')} correlation={event.get('correlation_id', '?')}"
                f"{error_label}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect JARVIS agent readiness and persisted task outcomes.")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT, help="JARVIS project root")
    parser.add_argument("--json-out", type=Path, help="Write the report to this JSON file")
    parser.add_argument("--history-limit", type=int, default=20, help="Number of recent terminal task records to show")
    args = parser.parse_args(argv)
    if args.history_limit < 0:
        parser.error("--history-limit cannot be negative")
    project_root = args.project_root.resolve()
    if not (project_root / "core" / "runtime.py").is_file() or not (project_root / "agents").is_dir():
        parser.error(f"not a JARVIS project root: {project_root}")
    runtime = create_runtime(project_root=project_root)
    report = build_diagnostics(runtime, history_path=project_root / "data" / "task_history.jsonl", history_limit=args.history_limit)
    if args.json_out:
        output_path = args.json_out.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Diagnostics JSON written to: {output_path}")
    print(render_diagnostics(report))
    return 1 if report["runtime"]["audit_write_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
