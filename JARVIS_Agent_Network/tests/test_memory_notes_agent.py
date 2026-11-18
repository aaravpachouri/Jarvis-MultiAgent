from __future__ import annotations
import json
from types import SimpleNamespace
import pytest
from agents.memory_agent.agent import MemoryNotesAgent

@pytest.mark.asyncio
async def test_explicit_save_persists_across_agent_instances(tmp_path):
    path=tmp_path/'memory_notes.json'; first=MemoryNotesAgent(path)
    saved=await first(SimpleNamespace(prompt='Remember that the JARVIS baseline passed 159 tests',context={}),None)
    assert saved['saved'] is True
    second=MemoryNotesAgent(path); listed=await second(SimpleNamespace(prompt='Show my notes',context={}),None)
    assert listed['count']==1 and listed['notes'][0]['id']==saved['note']['id']
    assert '159 tests' in listed['notes'][0]['content']

@pytest.mark.asyncio
async def test_regular_chat_does_not_save_without_explicit_intent(tmp_path):
    path=tmp_path/'notes.json'; result=await MemoryNotesAgent(path)(SimpleNamespace(prompt='I am working on JARVIS today',context={}),None)
    assert result['operation']=='help' and not path.exists()

@pytest.mark.asyncio
async def test_secret_like_notes_are_rejected(tmp_path):
    path=tmp_path/'notes.json'; result=await MemoryNotesAgent(path)(SimpleNamespace(prompt='Remember that my API key is abc123',context={}),None)
    assert result['saved'] is False and not path.exists()

@pytest.mark.asyncio
async def test_search_and_specific_delete(tmp_path):
    agent=MemoryNotesAgent(tmp_path/'notes.json')
    await agent(SimpleNamespace(prompt='Remember that the JARVIS demo is Friday',context={}),None)
    await agent(SimpleNamespace(prompt='Remember that the Python test suite passed',context={}),None)
    found=await agent(SimpleNamespace(prompt='Find my note about JARVIS',context={}),None)
    assert found['count']==1
    deleted=await agent(SimpleNamespace(prompt=f"Forget note {found['notes'][0]['id']}",context={}),None)
    assert deleted['deleted'] is True
    remaining=await agent(SimpleNamespace(prompt='Show my notes',context={}),None)
    assert remaining['count']==1 and 'Python test suite' in remaining['notes'][0]['content']

@pytest.mark.asyncio
async def test_ambiguous_delete_does_not_remove_multiple_notes(tmp_path):
    path=tmp_path/'notes.json'; agent=MemoryNotesAgent(path)
    await agent(SimpleNamespace(prompt='Remember that JARVIS is the project',context={}),None)
    await agent(SimpleNamespace(prompt='Remember that JARVIS has a test suite',context={}),None)
    result=await agent(SimpleNamespace(prompt='Forget note JARVIS',context={}),None)
    assert result['deleted'] is False and len(result['matches'])==2
    assert len(json.loads(path.read_text(encoding='utf-8')))==2
