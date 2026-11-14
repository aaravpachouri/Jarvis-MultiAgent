from __future__ import annotations

import sys
import types

import pytest

from agents.voice_agent.audio_interface import SpeechNotRecognized, VoiceIO


class FakeMicrophone:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeRecognizer:
    def adjust_for_ambient_noise(self, source, duration):
        pass

    def listen(self, source, timeout, phrase_time_limit):
        return object()

    def recognize_google(self, audio, language):
        raise FakeSpeechRecognition.UnknownValueError()


class FakeSpeechRecognition(types.ModuleType):
    class UnknownValueError(Exception):
        pass

    class RequestError(Exception):
        pass

    class WaitTimeoutError(Exception):
        pass

    class Recognizer:
        pass

    Microphone = FakeMicrophone


def test_unrecognized_speech_is_converted_to_domain_exception(monkeypatch):
    monkeypatch.setitem(sys.modules, "speech_recognition", FakeSpeechRecognition("speech_recognition"))
    voice = VoiceIO.__new__(VoiceIO)
    voice._recognizer = FakeRecognizer()
    voice._microphone_type = FakeMicrophone
    voice._calibrated = True
    voice.language = "en-IN"
    voice.listen_timeout = 1
    voice.phrase_time_limit = 2

    with pytest.raises(SpeechNotRecognized, match="couldn't understand"):
        voice.listen_once()
