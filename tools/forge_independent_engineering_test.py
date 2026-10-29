"""Second-stage direct Forge test: inspect, repair, extend, and verify a tiny repo.

Run from the JARVIS project root using its .venv Python. This invokes ForgeAgent
DIRECTLY; it does not invoke Athena, Scout, Atlas, or the agent orchestrator.
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
from agents.coding_agent.agent import ForgeAgent


def main() -> int:
    root = Path.cwd().resolve()
    if not (root / "agents" / "coding_agent" / "agent.py").is_file():
        print("ERROR: Run this from the JARVIS project root.")
        return 2
    load_project_env(root)
    os.environ["PYTHON"] = sys.executable
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    workspace = root / f".forge_engineering_test_{stamp}"
    tests = workspace / "tests"
    tests.mkdir(parents=True, exist_ok=False)

    module = workspace / "calculator.py"
    test_file = tests / "test_calculator.py"
    module.write_text("def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    # Intentional bug: multiplication incorrectly adds.\n    return a + b\n", encoding="utf-8")
    test_file.write_text("from calculator import add, multiply, subtract\n\ndef test_add_positive_negative_and_zero():\n    assert add(2, 3) == 5\n    assert add(-2, 3) == 1\n    assert add(0, 0) == 0\n\ndef test_multiply_positive_negative_and_zero():\n    assert multiply(2, 3) == 6\n    assert multiply(-2, 3) == -6\n    assert multiply(0, 9) == 0\n\ndef test_subtract_positive_negative_and_zero():\n    assert subtract(9, 4) == 5\n    assert subtract(-2, 3) == -5\n    assert subtract(0, 0) == 0\n", encoding="utf-8")
    before_module = module.read_text(encoding="utf-8")
    before_tests = test_file.read_text(encoding="utf-8")

    task = (
        "Inspect the existing calculator.py and tests/test_calculator.py in this workspace. "
        "Repair the existing multiply bug and implement the missing subtract(a, b) function. "
        "Keep add(a, b), multiply(a, b), and subtract(a, b) correct for positive values, "
        "negative values, and zero. Rewrite tests/test_calculator.py to use pytest parametrization "
        "for positive, negative, and zero cases, and add at least one identity/boundary case. "
        "Modify both existing files rather than replacing this task with unrelated files. "
        "Run pytest for tests/test_calculator.py and report only real verification results. "
        "Do not edit anything outside this workspace."
    )
    print("DIRECT FORGE ENGINEERING TEST")
    print("Orchestration: direct ForgeAgent call only (no Athena, Scout, Atlas, or orchestrator)")
    print(f"Workspace: {workspace}")
    print(f"Python: {sys.executable}")
    try:
        result = asyncio.run(ForgeAgent(workspace=workspace)(task, context=None))
    except Exception as exc:
        print(f"\nFAIL: Forge raised {type(exc).__name__}: {exc}")
        print(f"Artifacts retained at: {workspace}")
        return 1
    report = str(result)
    print("\nFORGE REPORT\n" + report)

    missing = [str(p.relative_to(workspace)) for p in (module, test_file) if not p.is_file()]
    if missing:
        print("\nFAIL: expected existing files missing: " + ", ".join(missing))
        return 1
    try:
        actual_module = module.read_text(encoding="utf-8")
        actual_tests = test_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"\nFAIL: could not read files back: {exc}")
        return 1
    if actual_module == before_module or actual_tests == before_tests:
        print("\nFAIL: Forge did not modify both existing files.")
        print(f"Module changed: {actual_module != before_module}; tests changed: {actual_tests != before_tests}")
        return 1
    if not all(re.search(rf"(?m)^def\s+{name}\s*\(", actual_module) for name in ("add", "multiply", "subtract")):
        print("\nFAIL: calculator.py does not expose all three required functions.")
        return 1

    run = subprocess.run([sys.executable, "-m", "pytest", "-q", str(test_file)], cwd=workspace,
                         capture_output=True, text=True, timeout=120, check=False)
    output = (run.stdout + "\n" + run.stderr).strip()
    print("\nINDEPENDENT VERIFICATION")
    print(f"Both existing files changed: YES")
    print(f"pytest exit code: {run.returncode}")
    print(output or "(pytest produced no output)")

    explicit_fail = bool(re.search(r"Verification:\s*(?:FAILED|RESULT UNKNOWN)", report, re.I))
    section = report.split("Verification:", 1)[1] if "Verification:" in report else ""
    claimed_pass = "Verification: PASSED" in report or bool(re.search(r"(?m)^Exit code:\s*0\s*$", section))
    if run.returncode != 0:
        print("\nFAIL: independent pytest run failed.")
        return 1
    if explicit_fail or not claimed_pass:
        print("\nFAIL: Forge did not provide an explicit successful verification report.")
        return 1
    print("\nPASS: Forge inspected and changed both existing files, repaired the bug, added the missing function, and independent pytest passed.")
    print(f"Artifacts retained at: {workspace}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
