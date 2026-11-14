from agents.coding_agent.agent import ForgeAgent, ForgeError
import pytest


def test_implementation_request_with_tests_is_implementation():
    task = (
        "Create math_utils.py and test_math_utils.py, "
        "then run pytest on the new test file."
    )
    assert ForgeAgent._classify(task) == "implement"


def test_test_only_request_remains_test_action():
    assert ForgeAgent._classify("Run pytest and report the results.") == "test"


def test_debugging_request_is_not_misclassified_as_test():
    assert ForgeAgent._classify("Fix the bug causing the math utility tests to fail.") == "implement"


def test_parse_json_accepts_plain_object():
    payload = ForgeAgent._parse_json('{"summary":"ok","files":[]}')
    assert payload["summary"] == "ok"


def test_parse_json_accepts_fenced_object():
    payload = ForgeAgent._parse_json('```json\n{"summary":"ok","files":[]}\n```')
    assert payload["summary"] == "ok"


def test_parse_json_accepts_object_with_preamble():
    payload = ForgeAgent._parse_json('Here is the result:\n{"summary":"ok","files":[]}\nDone.')
    assert payload["summary"] == "ok"


def test_parse_json_rejects_empty_response():
    with pytest.raises(ForgeError, match="empty implementation response"):
        ForgeAgent._parse_json("  ")


@pytest.mark.parametrize("task", [
    "Inspect my project and recommend an implementation plan. Do not modify or create any files.",
    "Review the repository and recommend changes without modifying any files.",
    "Read-only audit: never write or change source files; explain what should be implemented.",
])
def test_explicit_read_only_request_never_classifies_as_implementation(task):
    # Inspect and analyze are both non-writing actions; the safety invariant is
    # that explicit write prohibitions never select implementation.
    assert ForgeAgent._classify(task) in {"inspect", "analyze", "test"}


def test_explicit_read_only_request_does_not_trigger_on_negated_create_and_modify():
    task = "Inspect the codebase. Do not create or modify files; only recommend a plan."
    assert ForgeAgent._classify(task) == "inspect"


def test_explicit_implementation_request_still_classifies_as_implementation():
    task = "Create a parser module, update its tests, and run pytest."
    assert ForgeAgent._classify(task) == "implement"
