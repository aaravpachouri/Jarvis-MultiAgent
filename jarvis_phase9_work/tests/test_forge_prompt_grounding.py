from __future__ import annotations

from agents.coding_agent.agent import ForgeAgent


class CapturingModel:
    configured = True

    def __init__(self) -> None:
        self.messages = None

    async def complete(self, messages):
        self.messages = messages
        return "captured"


def test_analysis_prompt_prioritizes_exact_task_and_relevant_component(tmp_path):
    model = CapturingModel()
    agent = ForgeAgent(workspace=tmp_path, model=model)
    messages = agent._build_messages(
        "Explain how Forge's verification gate works, not the VoiceAgent.",
        "analyze",
    )
    system = messages[0]["content"]
    user = messages[1]["content"]

    assert "answer the user's exact task" in system.lower()
    assert "Do not substitute an explanation of another agent/component" in system
    assert "Explain how Forge's verification gate works, not the VoiceAgent." in user
    assert "cite relevant relative file paths and function/class names" in user
    assert "Trace the relevant component rather than describing a different agent" in user


def test_analysis_prompt_requires_explicit_uncertainty_when_evidence_is_missing(tmp_path):
    agent = ForgeAgent(workspace=tmp_path, model=CapturingModel())
    messages = agent._build_messages("Explain an unknown subsystem", "analyze")
    combined = "\n".join(message["content"] for message in messages)

    assert "If the evidence does not support an answer, say exactly what is missing" in combined
    assert "which specific file or evidence would resolve it" in combined
    assert "Separate facts from inferences" in combined


def test_non_implementation_prompt_is_read_only_and_ordinary_prose(tmp_path):
    agent = ForgeAgent(workspace=tmp_path, model=CapturingModel())
    messages = agent._build_messages("Inspect this project", "inspect")
    instruction = messages[1]["content"]

    assert "READ-ONLY" in instruction
    assert "do not create, modify, delete, overwrite, or propose writing files" in instruction
    assert "Respond in ordinary prose, not implementation JSON" in instruction


def test_implementation_prompt_keeps_json_schema_and_task_focus(tmp_path):
    agent = ForgeAgent(workspace=tmp_path, model=CapturingModel())
    messages = agent._build_messages("Fix the parser", "implement")
    system = messages[0]["content"]
    instruction = messages[1]["content"]

    assert '"files": [{"path": "relative/path.py", "content": "complete file contents"}]' in system
    assert "ACTION: IMPLEMENT" in instruction
    assert "Implement the user's specific requested change" in instruction
    assert "complete contents only for files that need to change" in instruction
