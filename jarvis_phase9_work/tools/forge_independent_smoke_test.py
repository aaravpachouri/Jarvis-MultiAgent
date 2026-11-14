"""Run a real, direct Forge implementation smoke test without Athena/Scout/Atlas.

Run from the JARVIS project root with its virtual environment's Python.
The test workspace is isolated under .forge_independent_smoke_test_<timestamp>.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.env import load_project_env
from agents.coding_agent.agent import ForgeAgent, ForgeError


def main() -> int:
    project_root = Path.cwd().resolve()
    if not (project_root / "agents" / "coding_agent" / "agent.py").is_file():
        print("ERROR: Run this from the JARVIS project root; agents/coding_agent/agent.py was not found.")
        return 2

    load_project_env(project_root)
    os.environ["PYTHON"] = sys.executable
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    workspace = project_root / f".forge_independent_smoke_test_{stamp}"
    workspace.mkdir(parents=True, exist_ok=False)

    task_text = (
        "Implement a tiny, self-contained Python arithmetic utility in this workspace. "
        "Create exactly these two files with complete contents: calculator.py and "
        "tests/test_calculator.py. calculator.py must expose add(a, b) and multiply(a, b). "
        "The pytest file must test positive integers, negative values, and zero for both "
        "functions. Use only the Python standard library and pytest. Do not edit any file "
        "outside this workspace. After writing the files, verify them on disk and run pytest."
    )

    print("DIRECT FORGE SMOKE TEST")
    print("Orchestration: direct Forge handler only (no Athena, Scout, or Atlas)")
    print(f"Project: {project_root}")
    print(f"Isolated workspace: {workspace}")
    print(f"Python executable: {sys.executable}")

    try:
        forge = ForgeAgent(workspace=workspace)
        result = asyncio.run(forge(task_text, context=None))
    except Exception as exc:
        print(f"FAIL: Forge raised {type(exc).__name__}: {exc}")
        print("No success is claimed. Inspect the isolated workspace and model configuration.")
        return 1

    print("\nFORGE REPORT\n" + str(result))

    expected = [workspace / "calculator.py", workspace / "tests" / "test_calculator.py"]
    missing = [str(path.relative_to(workspace)) for path in expected if not path.is_file()]
    if missing:
        print("\nFAIL: Forge did not create all requested files: " + ", ".join(missing))
        return 1

    try:
        calculator_source = expected[0].read_text(encoding="utf-8")
        test_source = expected[1].read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"\nFAIL: Created files could not be read back: {exc}")
        return 1

    if not calculator_source.strip() or not test_source.strip():
        print("\nFAIL: One or more requested files are empty.")
        return 1

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(expected[1])],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    test_output = (completed.stdout + "\n" + completed.stderr).strip()
    print("\nINDEPENDENT VERIFICATION")
    print(f"Files exist and are non-empty: YES")
    print(f"pytest exit code: {completed.returncode}")
    print(test_output or "(pytest produced no output)")

    report = str(result)
    explicit_pass = "Verification: PASSED" in report
    explicit_failure = bool(re.search(r"Verification:\s*(?:FAILED|RESULT UNKNOWN)", report, re.IGNORECASE))
    verification_section = report.split("Verification:", 1)[1] if "Verification:" in report else ""
    zero_exit_reported = bool(re.search(r"(?m)^Exit code:\s*0\s*$", verification_section))
    forge_claimed_pass = explicit_pass or zero_exit_reported
    if completed.returncode != 0:
        print("\nFAIL: The independently rerun pytest command failed.")
        return 1
    if explicit_failure:
        print("\nFAIL: Forge explicitly reported failed or unknown verification.")
        return 1
    if not forge_claimed_pass:
        print("\nFAIL: pytest passed independently, but Forge's own report did not provide explicit passing verification evidence.")
        return 1

    print("\nPASS: Forge directly created both files, its report confirmed verification, and an independent pytest run passed.")
    print(f"Artifacts retained at: {workspace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
