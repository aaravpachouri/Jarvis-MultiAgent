from __future__ import annotations

import asyncio

from agents.coding_agent.agent import ForgeAgent, ForgeModel


class JsonModeClient:
    providers = [object()]
    active_provider = None
    last_provider_errors = []

    def __init__(self):
        self.called = False

    def decide(self, messages):
        self.called = True
        return {"summary": "created", "files": [{"path": "x.py", "content": "x = 1\n"}]}

    def answer_chat(self, messages):
        raise AssertionError("Implementation should use decide()/JSON mode")


def test_forge_model_complete_json_uses_shared_decide():
    client = JsonModeClient()
    result = asyncio.run(ForgeModel(client=client).complete_json([
        {"role": "system", "content": "json"},
        {"role": "user", "content": "create a file"},
    ]))
    assert client.called
    assert result["files"][0]["path"] == "x.py"


def test_implementation_request_with_tests_is_implementation():
    assert ForgeAgent._classify("Create a file, then run pytest") == "implement"


def test_test_only_request_remains_test_action():
    assert ForgeAgent._classify("Run pytest and report results") == "test"
