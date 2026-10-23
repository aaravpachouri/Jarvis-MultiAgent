from types import SimpleNamespace
import pytest
from agents.voice_agent.agent import VoiceAgent

@pytest.mark.asyncio
async def test_native_sync_chat_client_receives_messages(monkeypatch):
    class Client:
        configured = True
        def answer_chat(self, messages):
            assert any(m.get('content') == 'Hello JARVIS' for m in messages)
            return 'Hello from the configured model.'
    agent=VoiceAgent(model_client=Client())
    class Registry:
        def list(self, enabled_only=False): return []
    class Runtime:
        registry=Registry()
        def agent_status(self, agent_id): return SimpleNamespace(value='ready')
        def has_handler(self, agent_id): return False
    class Context: runtime=Runtime()
    assert await agent._model_fallback('Hello JARVIS', Context()) == 'Hello from the configured model.'

@pytest.mark.asyncio
async def test_native_sync_source_synthesis_returns_answer():
    class Client:
        configured=True
        def answer_with_sources(self, messages): return {'answer':'FastAPI is a Python API framework [1].','sources':[]}
    agent=VoiceAgent(model_client=Client())
    source={'title':'FastAPI docs','url':'https://fastapi.tiangolo.com/','snippet':'Framework documentation'}
    response=await agent._synthesize_search_response('Search FastAPI', {'query':'FastAPI','results':[source],'retrieved_at':'now'})
    assert 'FastAPI is a Python API framework' in response

@pytest.mark.asyncio
async def test_model_adapter_error_is_not_returned_as_successful_chat():
    class BrokenClient:
        configured=True
        async def answer_chat(self, **kwargs): raise TypeError('simulated adapter mismatch')
    agent=VoiceAgent(model_client=BrokenClient())
    class Registry:
        def list(self, enabled_only=False): return []
    class Runtime:
        registry=Registry()
        def agent_status(self, agent_id): return SimpleNamespace(value='ready')
        def has_handler(self, agent_id): return False
    class Context: runtime=Runtime()
    with pytest.raises(RuntimeError, match='failed safely'):
        await agent._model_fallback('hello', Context())

def test_research_query_stops_at_stage_heading():
    prompt=('Research the official FastAPI documentation for the current stable release and its Python compatibility requirements.\n\n'
            '**Stage 1 — Research:** Use Athena to coordinate Scout searches.\n'
            '**Stage 2 — Implementation:** Pass the actual findings to Forge.')
    query=VoiceAgent._research_query_from_request(prompt)
    assert 'FastAPI' in query
    assert 'Stage 1' not in query

class FakeContext:
    def __init__(self, research_result):
        self.runtime=SimpleNamespace(
            executable_agent_ids=lambda:{'research.athena','coding.forge'},
            discover_executable=lambda capabilities, match_all=True: [
                SimpleNamespace(agent_id=agent_id, capabilities=agent_caps)
                for agent_id, agent_caps in (
                    ('research.athena', ('research.deep','research.synthesize')),
                    ('coding.forge', ('code.write','code.test')),
                )
                if (set(capabilities).issubset(set(agent_caps)) if match_all else bool(set(capabilities) & set(agent_caps)))
            ],
        )
        self.agent=SimpleNamespace(agent_id='voice.jarvis')
        self.research_result=research_result
        self.calls=[]
    async def delegate(self, **kwargs):
        self.calls.append(kwargs)
        return self.research_result

@pytest.mark.asyncio
async def test_research_question_mismatch_stops_before_forge():
    research=SimpleNamespace(succeeded=True, output={'summary':'A research summary about a different topic.', 'question':'succeeds pass actual research findings to Forge and create files', 'sources':[{'title':'FastAPI docs','url':'https://fastapi.tiangolo.com/','snippet':'FastAPI documentation'}]}, error=None, summary='', status=SimpleNamespace(value='succeeded'))
    context=FakeContext(research)
    with pytest.raises(RuntimeError, match='does not match the requested topic'):
        await VoiceAgent()._run_research_to_forge('Research the official FastAPI documentation for its current stable release and Python compatibility requirements. Create athena_forge_smoke_test/app.py and athena_forge_smoke_test/test_app.py.', context)
    assert [c['agent_id'] for c in context.calls]==['research.athena']

@pytest.mark.asyncio
async def test_fastapi_research_requires_a_primary_source():
    research=SimpleNamespace(succeeded=True, output={'summary':'Some generic search results.', 'question':'Research the official FastAPI documentation and release requirements', 'sources':[{'title':'Generic research guide','url':'https://example.com/research-guide','snippet':'How to research sources'}]}, error=None, summary='', status=SimpleNamespace(value='succeeded'))
    context=FakeContext(research)
    with pytest.raises(RuntimeError, match='recognized official primary source'):
        await VoiceAgent()._run_research_to_forge('Research the official FastAPI documentation for its current stable release and Python compatibility requirements. Create athena_forge_smoke_test/app.py and athena_forge_smoke_test/test_app.py.', context)
    assert [c['agent_id'] for c in context.calls]==['research.athena']
