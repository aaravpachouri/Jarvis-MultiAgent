from __future__ import annotations

from agents.voice_agent.audio_interface import VoiceIO


def test_kokoro_speech_chunks_preserve_sentence_text(monkeypatch):
    monkeypatch.setenv("JARVIS_KOKORO_CHUNK_CHARS", "60")
    voice = VoiceIO()
    chunks = voice._split_kokoro_chunks("Hello there. How are you? I am ready to help.")
    assert chunks == ["Hello there.", "How are you?", "I am ready to help."]


def test_kokoro_speech_synthesizes_and_plays_each_sentence(monkeypatch):
    monkeypatch.setenv("JARVIS_KOKORO_CHUNK_CHARS", "60")
    voice = VoiceIO()
    calls = []

    class FakeEngine:
        def create(self, text, **kwargs):
            calls.append(("create", text))
            return [0.0, 0.1], 24000

    class FakeAudio:
        def play(self, audio, rate):
            calls.append(("play", rate))

        def wait(self):
            calls.append(("wait", None))

    monkeypatch.setattr(voice, "_load_kokoro", lambda: (FakeEngine(), FakeAudio()))
    voice._speak_kokoro("First sentence. Second sentence.")
    assert [item[1] for item in calls if item[0] == "create"] == ["First sentence.", "Second sentence."]
    assert len([item for item in calls if item[0] == "play"]) == 2
