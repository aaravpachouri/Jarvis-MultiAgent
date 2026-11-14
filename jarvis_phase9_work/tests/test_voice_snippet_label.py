import pytest
from agents.voice_agent.agent import VoiceAgent


@pytest.mark.asyncio
async def test_search_synthesis_labels_each_snippet_unverified():
    class Client:
        configured = True

        def __init__(self):
            self.messages = None

        def answer_with_sources(self, messages):
            self.messages = messages
            return {"answer": "FastAPI is a Python API framework [1].", "sources": []}

    client = Client()
    agent = VoiceAgent(model_client=client)
    source = {
        "title": "FastAPI docs",
        "url": "https://fastapi.tiangolo.com/",
        "snippet": "Framework documentation",
    }
    response = await agent._synthesize_search_response(
        "Search FastAPI",
        {"query": "FastAPI", "results": [source], "retrieved_at": "now"},
    )

    assert "FastAPI is a Python API framework" in response
    assert client.messages is not None
    user_content = client.messages[-1]["content"]
    assert "Snippet (unverified)" in user_content
    assert "https://fastapi.tiangolo.com/" in user_content
