
"""Interactive voice session for JARVIS's main-handler agent."""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

from core.contracts import TaskRequest
from .audio_interface import (
    ListenTimeout,
    SpeechNotRecognized,
    SpeechServiceUnavailable,
    VoiceIO,
    VoiceInterfaceError,
)

_WAKE_WORD_RE = re.compile(r"\bjarvis\b", re.IGNORECASE)
_EXIT_PHRASES = {
    "exit", "quit", "stop listening", "exit voice mode", "leave voice mode",
    "stop voice", "goodbye jarvis", "shut down voice mode",
}


def extract_wake_word(transcript: str) -> tuple[bool, str]:
    """Return (wake_word_found, remaining_request)."""
    text = str(transcript or "").strip()
    match = _WAKE_WORD_RE.search(text)
    if not match:
        return False, text

    before = text[:match.start()].strip(" ,.!?\t\n")
    after = text[match.end():].strip(" ,.!?\t\n")

    if before.casefold() in {"hey", "hello", "hi", "okay", "ok"} and not after:
        return True, ""

    before = re.sub(
        r"^(?:hey|hello|hi|okay|ok)[\s,!.:-]*",
        "",
        before,
        flags=re.IGNORECASE,
    ).strip()

    request = " ".join(part for part in (before, after) if part).strip()
    return True, request


def is_voice_exit_command(prompt: str) -> bool:
    return str(prompt or "").casefold().strip(" .,!\t\n") in _EXIT_PHRASES


def _result_text(result: Any) -> str:
    """Extract the full result text for terminal display."""
    output = getattr(result, "output", None)

    if isinstance(output, str) and output.strip():
        return output.strip()

    if isinstance(output, dict):
        for key in ("answer", "summary", "message", "text", "output"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return str(
        getattr(result, "error", None)
        or getattr(result, "summary", None)
        or "The request finished without a text response."
    )


def _spoken_reply(result: Any, reply: str) -> str:
    """Choose a concise spoken response without reading long results aloud."""
    status_value = getattr(result, "status", None)
    status = str(getattr(status_value, "value", status_value) or "").casefold()
    text = reply.strip()

    # Don't announce success for a failed task.
    if status and status not in {
        "succeeded", "success", "completed", "complete"
    }:
        return "I couldn't complete that task. I've displayed the details on screen."

    # Search synthesis: speak the answer paragraph, not the source list.
    if text.startswith("JARVIS answer:"):
        answer = text[len("JARVIS answer:"):].strip().split("\n\n", 1)[0]
        answer = re.sub(r"\[(?:\d+)\]", "", answer).strip()
        answer = re.sub(r"\s+", " ", answer)
        if answer:
            if len(answer) > 260:
                shortened = answer[:250].rsplit(" ", 1)[0].rstrip(" ,;:-")
                return shortened + ". I've displayed the sources on screen."
            return answer
        return "I've summarized the search results and displayed the sources on screen."

    # Scout: retain the full results in the terminal, speak only a summary.
    if text.startswith("Scout search results for:"):
        count = len(re.findall(r"(?m)^\s*\d+\.\s+", text))
        if count:
            return (
                f"Search complete. I found {count} results "
                "and displayed them on screen."
            )
        return "Search finished. I've displayed the results on screen."

    # Keep lengthy or multiline agent output on screen.
    if len(text) > 240 or "\n" in text:
        return "Task complete. I've displayed the results on screen."

    # Normal short conversations should still sound natural.
    return text


def _speak_safely(voice_io: VoiceIO, text: str) -> None:
    """Speak one message and keep voice errors from crashing the session."""
    try:
        voice_io.speak(text)
    except VoiceInterfaceError as exc:
        print(f"[JARVIS voice output unavailable] {exc}")


async def run_voice_session(
    runtime: Any,
    *,
    voice_io: VoiceIO | None = None,
    use_wake_word: bool = True,
    language: str | None = None,
) -> int:
    """Listen for speech, route requests through JARVIS, and speak each reply."""
    audio = voice_io or VoiceIO(language=language)
    executable_count = len(runtime.executable_agent_ids())

    print("JARVIS Voice Session")
    print(
        "Say 'Jarvis' followed by a request. Follow-ups stay active briefly; "
        "say 'Jarvis' alone to re-arm."
    )
    print("Say 'stop listening' to end the session, or press Ctrl+C.\n")
    print(f"Agent handlers active: {executable_count}")

    _speak_safely(
        audio,
        "JARVIS voice interface online. Say Jarvis followed by your request.",
    )

    awaiting_command = False
    conversation_timeout = max(
        5.0,
        float(os.getenv("JARVIS_CONVERSATION_TIMEOUT_SECONDS", "35")),
    )
    conversation_active_until = 0.0

    while True:
        try:
            transcript = await asyncio.to_thread(audio.listen_once)
        except ListenTimeout:
            continue
        except SpeechNotRecognized:
            continue
        except Exception as exc:
            import traceback
            
            print(f"[Voice input error] {type(exc).__name__}: {exc}")
            traceback.print_exc()
            print(
                "Voice session stopped. Check the internet connection or "
                "use 'python main.py chat'."
            )
            return 2
        except VoiceInterfaceError as exc:
            print(f"[Voice input error] {exc}")
            print(
                'Install the optional voice dependencies with: '
                'python -m pip install -e ".[dev,voice]"'
            )
            return 2
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nJARVIS voice session stopped.")
            return 0
        except Exception as exc:
            print(f"[Voice input error] {type(exc).__name__}: {exc}")
            return 2

        print(f"Heard: {transcript}")

        if is_voice_exit_command(transcript):
            _speak_safely(audio, "Voice session ending. Goodbye.")
            print("JARVIS voice session ended.")
            return 0

        now = time.monotonic()

        if use_wake_word and not awaiting_command:
            found, prompt = extract_wake_word(transcript)

            if not found:
                if now > conversation_active_until:
                    continue
                prompt = transcript.strip()
            elif not prompt:
                awaiting_command = True
                _speak_safely(audio, "I'm listening.")
                continue
        elif awaiting_command:
            prompt = transcript.strip()
        else:
            prompt = transcript.strip()

        awaiting_command = False

        if use_wake_word:
            conversation_active_until = time.monotonic() + conversation_timeout

        if not prompt:
            continue

        if is_voice_exit_command(prompt):
            _speak_safely(audio, "Voice session ending. Goodbye.")
            print("JARVIS voice session ended.")
            return 0

        request = TaskRequest(
            prompt=prompt,
            target_agent_id="voice.jarvis",
            requested_by="voice.input" if use_wake_word else "voice.continuous",
            context={
                "input_mode": "voice",
                "transcript": transcript,
                "language": language or audio.language,
            },
        )

        result = await runtime.run_task(request)
        reply = _result_text(result)

        # Print the complete result, but speak only the selected voice response.
        print(f"JARVIS > {reply}\n")
        spoken_reply = _spoken_reply(result, reply)
        _speak_safely(audio, spoken_reply)
