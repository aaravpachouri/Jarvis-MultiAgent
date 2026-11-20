"""Memory & Notes: explicit, local-first note storage and recall."""
from __future__ import annotations
import json, os, re, tempfile, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

class MemoryNotesAgent:
    """Persist only explicitly requested notes in a local JSON file."""
    _SAVE_PREFIX = re.compile(r"^\s*(?:please\s+)?(?:remember(?:\s+that|\s+for\s+later)?|save(?:\s+this)?(?:\s+note)?|add\s+(?:a\s+)?note|make\s+a\s+note|note\s+this|write\s+this\s+down|store\s+this\s+note)\s*[:,-]?\s*", re.I)
    _SENSITIVE = re.compile(r"\b(?:password|passwd|passphrase|api[\s_-]*key|secret[\s_-]*key|access[\s_-]*token|refresh[\s_-]*token|private[\s_-]*key|recovery[\s_-]*code|one[\s_-]*time[\s_-]*password|otp)\b", re.I)
    def __init__(self, store_path: str | Path | None = None):
        self.store_path=Path(store_path) if store_path else Path(__file__).resolve().parents[2]/'data'/'memory_notes.json'
        self._lock=threading.RLock()
    async def __call__(self, task: Any, context: Any) -> dict[str, Any]:
        prompt=str(getattr(task,'prompt','') or '').strip()
        if not prompt: raise ValueError('Memory & Notes requires a non-empty request.')
        intent,payload=self._classify(prompt)
        if intent=='save': return self._save(payload)
        if intent=='list':
            notes=self._read_all()
            return {'operation':'list','count':len(notes),'notes':[self._public_note(n) for n in notes],'summary':f'Found {len(notes)} saved note(s).' if notes else 'No saved notes yet.'}
        if intent=='search':
            notes=[n for n in self._read_all() if payload.casefold().strip() in n['content'].casefold()]
            return {'operation':'search','query':payload,'count':len(notes),'notes':[self._public_note(n) for n in notes],'summary':f'Found {len(notes)} note(s) matching “{payload}”.' if notes else f'No saved notes matched “{payload}”.'}
        if intent=='delete': return self._delete(payload)
        return {'operation':'help','summary':'I can save a note, list saved notes, search them, or delete a specific note.','examples':['Remember that my project deadline is Friday','Save a note: discuss test results tomorrow','Show my notes','Find my note about the project deadline','Forget note <note-id>']}
    @classmethod
    def _classify(cls,prompt:str)->tuple[str,str]:
        text=prompt.strip()
        if re.search(r"\b(?:show|list|display|view)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?(?:saved\s+)?notes\b",text,re.I) or re.search(r"\bwhat have you remembered\b",text,re.I): return 'list',''
        match=cls._SAVE_PREFIX.match(text)
        if match:
            content=text[match.end():].strip().strip("\"' ")
            if not content or content.casefold() in {'this','that','it','for later'}: return 'help',''
            return 'save',content
        match=re.search(r"^\s*(?:please\s+)?(?:forget|delete|remove)\s+(?:my\s+)?(?:saved\s+)?note\s*(.*)$",text,re.I)
        if match: return 'delete',match.group(1).strip().strip("\"' ")
        match=re.search(r"^\s*(?:please\s+)?forget\s+(?:that\s+)?(.+)$",text,re.I)
        if match and not re.search(r"\bwhat do you remember\b",text,re.I): return 'delete',match.group(1).strip().strip("\"' ")
        match=re.search(r"\b(?:find|search|recall)\s+(?:my\s+)?(?:saved\s+)?notes?\s+(?:about|for|on|containing)\s+(.+)$",text,re.I)
        if match: return 'search',match.group(1).strip().strip("\"' ?")
        match=re.search(r"\bwhat do you remember about\s+(.+)$",text,re.I)
        if match: return 'search',match.group(1).strip().strip("\"' ?")
        return 'help',''
    def _save(self,content:str)->dict[str,Any]:
        if self._SENSITIVE.search(content): return {'operation':'save','saved':False,'summary':"I didn't save that note because it appears to contain a credential or secret. Store credentials in a password manager instead."}
        if len(content)>4000: return {'operation':'save','saved':False,'summary':'That note is over the 4,000-character limit. Please split it into smaller notes.'}
        note={'id':str(uuid.uuid4()),'content':content,'created_at':datetime.now(timezone.utc).isoformat()}
        with self._lock:
            notes=self._read_all(); notes.append(note); self._write_all(notes)
        return {'operation':'save','saved':True,'note':self._public_note(note),'summary':'Saved your note locally. It will remain on this machine until you delete it or remove the local notes file.'}
    def _delete(self,target:str)->dict[str,Any]:
        if not target: return {'operation':'delete','deleted':False,'summary':"Provide a note ID or distinctive phrase. I won't delete all notes without a specific target."}
        with self._lock:
            notes=self._read_all(); folded=target.casefold()
            matches=[n for n in notes if n['id'].casefold()==folded or folded in n['content'].casefold()]
            if len(matches)!=1:
                return {'operation':'delete','deleted':False,'matches':[self._public_note(n) for n in matches],'summary':'More than one note matched. Provide a note ID or a more specific phrase.' if matches else 'No saved note matched that ID or phrase.'}
            chosen=matches[0]; self._write_all([n for n in notes if n['id']!=chosen['id']])
        return {'operation':'delete','deleted':True,'note':self._public_note(chosen),'summary':'Deleted the matching local note.'}
    def _read_all(self)->list[dict[str,str]]:
        with self._lock:
            if not self.store_path.exists(): return []
            try: payload=json.loads(self.store_path.read_text(encoding='utf-8'))
            except (OSError,json.JSONDecodeError) as exc: raise RuntimeError(f'Local notes store could not be read safely: {type(exc).__name__}') from None
            if not isinstance(payload,list): raise RuntimeError('Local notes store has an invalid format; no data was changed.')
            return [{'id':x['id'],'content':x['content'],'created_at':x['created_at']} for x in payload if isinstance(x,dict) and all(isinstance(x.get(k),str) for k in ('id','content','created_at'))]
    def _write_all(self,notes:list[dict[str,str]])->None:
        self.store_path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.memory-notes-',suffix='.tmp',dir=self.store_path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as handle:
                json.dump(notes,handle,ensure_ascii=False,indent=2); handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
            os.replace(tmp,self.store_path)
        finally:
            try: os.unlink(tmp)
            except FileNotFoundError: pass
    @staticmethod
    def _public_note(note:Mapping[str,str])->dict[str,str]: return {'id':note['id'],'content':note['content'],'created_at':note['created_at']}

def create_handler()->MemoryNotesAgent: return MemoryNotesAgent()
