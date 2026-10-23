from __future__ import annotations

from main import _read_prompt


def test_multiline_mode_keeps_pasted_request_as_one_prompt() -> None:
    answers = iter([":multi", "Research FastAPI releases", "Then pass findings to Forge", "Create files and run pytest", ":end"])
    messages: list[str] = []
    result = _read_prompt(input_fn=lambda _prompt: next(answers), output_fn=messages.append)
    assert result == "Research FastAPI releases\nThen pass findings to Forge\nCreate files and run pytest"
    assert len(messages) == 1


def test_normal_single_line_input_is_unchanged() -> None:
    result = _read_prompt(input_fn=lambda _prompt: "hello JARVIS", output_fn=lambda _message: None)
    assert result == "hello JARVIS"
