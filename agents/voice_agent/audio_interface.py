
"""Optional microphone and speech-output adapters for the JARVIS main handler.

Speech recognition uses the Google web recognition endpoint supplied by
SpeechRecognition. Do not use this adapter for sensitive audio unless that
external processing is acceptable. Imports are lazy so text-only installs do
not require microphone/TTS dependencies.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import threading
import time
from typing import Any


class VoiceInterfaceError(RuntimeError):
    """Base error for optional audio input/output failures."""


class ListenTimeout(VoiceInterfaceError):
    """No speech started before the configured listen timeout."""


class SpeechNotRecognized(VoiceInterfaceError):
    """Audio was captured but speech could not be transcribed."""


class SpeechServiceUnavailable(VoiceInterfaceError):
    """The speech recognition endpoint could not be reached."""


class VoiceIO:
    """Lazy-loaded microphone input with Kokoro TTS and pyttsx3 fallback."""

    def __init__(
        self,
        *,
        language: str | None = None,
        listen_timeout: float = 6.0,
        phrase_time_limit: float = 15.0,
        tts_rate: int | None = None,
    ) -> None:
        if listen_timeout <= 0:
            raise ValueError("listen_timeout must be positive")
        if phrase_time_limit <= 0:
            raise ValueError("phrase_time_limit must be positive")

        self.language = language or os.getenv("JARVIS_STT_LANGUAGE", "en-IN")
        self.listen_timeout = listen_timeout
        self.phrase_time_limit = phrase_time_limit
        self.tts_rate = tts_rate or int(os.getenv("JARVIS_TTS_RATE", "185"))

        self.tts_backend = os.getenv(
            "JARVIS_TTS_ENGINE", "auto"
        ).strip().casefold()
        if self.tts_backend not in {"auto", "kokoro", "pyttsx3"}:
            raise ValueError(
                "JARVIS_TTS_ENGINE must be auto, kokoro, or pyttsx3"
            )

        project_root = Path(__file__).resolve().parents[2]
        self.kokoro_model_path = Path(
            os.getenv(
                "JARVIS_KOKORO_MODEL",
                str(project_root / "models" / "kokoro-v1.0.int8.onnx"),
            )
        ).expanduser()
        self.kokoro_voices_path = Path(
            os.getenv(
                "JARVIS_KOKORO_VOICES",
                str(project_root / "models" / "voices-v1.0.bin"),
            )
        ).expanduser()

        self.kokoro_voice = (
            os.getenv("JARVIS_KOKORO_VOICE", "af_heart").strip() or "af_heart"
        )
        self.kokoro_language = (
            os.getenv("JARVIS_KOKORO_LANG", "en-us").strip() or "en-us"
        )
        self.kokoro_speed = float(os.getenv("JARVIS_KOKORO_SPEED", "1.0"))
        self.kokoro_chunk_chars = max(
            60, int(os.getenv("JARVIS_KOKORO_CHUNK_CHARS", "140"))
        )
        self.tts_debug = (
            os.getenv("JARVIS_TTS_DEBUG", "0").strip().casefold()
            in {"1", "true", "yes", "on"}
        )

        self._speech_recognition_module: Any = None
        self._recognizer: Any = None
        self._microphone_type: Any = None

        self._tts_engine: Any = None
        self._kokoro_engine: Any = None
        self._kokoro_audio: Any = None
        self._active_tts_backend: str | None = None
        self._calibrated = False
        self._tts_lock = threading.RLock()

    def _load_speech_recognition(self) -> tuple[Any, Any]:
        """Load the module lazily while preserving injected test adapters."""
        sr = getattr(self, "_speech_recognition_module", None)

        # Load the module even when a recognizer was injected externally.
        # This matters for VoiceIO instances created without __init__().
        if sr is None:
            try:
                import speech_recognition as sr
            except ImportError as exc:
                raise VoiceInterfaceError(
                    'Voice dependencies are missing. Run: '
                    'python -m pip install -e ".[dev,voice]"'
                ) from exc

            self._speech_recognition_module = sr

        # Keep injected recognizers/microphones intact, including test doubles.
        if getattr(self, "_recognizer", None) is None:
            try:
                self._recognizer = sr.Recognizer()
            except Exception as exc:
                raise VoiceInterfaceError(
                    f"Could not initialise speech recognition: {exc}"
                ) from exc

        if getattr(self, "_microphone_type", None) is None:
            self._microphone_type = sr.Microphone

        return sr, self._microphone_type

    def listen_once(self) -> str:
        """Capture one phrase and return its transcript; may make a network call."""
        sr, microphone_type = self._load_speech_recognition()
        recognizer = self._recognizer

        try:
            with microphone_type() as source:
                if not self._calibrated:
                    recognizer.adjust_for_ambient_noise(source, duration=0.35)
                    self._calibrated = True

                audio = recognizer.listen(
                    source,
                    timeout=self.listen_timeout,
                    phrase_time_limit=self.phrase_time_limit,
                )
        except sr.WaitTimeoutError as exc:
            raise ListenTimeout(
                "No speech detected before the listening timeout."
            ) from exc
        except VoiceInterfaceError:
            raise
        except Exception as exc:
            raise VoiceInterfaceError(
                "Could not open/read the microphone. Check PyAudio "
                "installation, Windows microphone permissions, and input "
                f"device: {exc}"
            ) from exc

        try:
            transcript = recognizer.recognize_google(
                audio, language=self.language
            )
        except sr.UnknownValueError as exc:
            raise SpeechNotRecognized(
                "I couldn't understand that phrase. Please try again."
            ) from exc
        except sr.RequestError as exc:
            raise SpeechServiceUnavailable(
                f"Speech recognition service is unavailable: {exc}"
            ) from exc

        transcript = str(transcript or "").strip()
        if not transcript:
            raise SpeechNotRecognized(
                "No words were recognised. Please try again."
            )

        return transcript

    def _kokoro_is_configured(self) -> bool:
        return (
            self.kokoro_model_path.is_file()
            and self.kokoro_voices_path.is_file()
        )

    def _load_kokoro(self) -> tuple[Any, Any]:
        if self._kokoro_engine is None:
            if not self._kokoro_is_configured():
                raise VoiceInterfaceError(
                    "Kokoro model files are missing. Put "
                    "kokoro-v1.0.int8.onnx and voices-v1.0.bin in the "
                    "project's models/ folder, or set JARVIS_KOKORO_MODEL "
                    "and JARVIS_KOKORO_VOICES. See README.md."
                )

            try:
                from kokoro_onnx import Kokoro
                import sounddevice as sd
            except ImportError as exc:
                raise VoiceInterfaceError(
                    'Kokoro dependencies are missing. Run: '
                    'python -m pip install -e ".[dev,voice,kokoro]"'
                ) from exc

            try:
                self._kokoro_engine = Kokoro(
                    str(self.kokoro_model_path),
                    str(self.kokoro_voices_path),
                )
                self._kokoro_audio = sd
            except Exception as exc:
                self._kokoro_engine = None
                self._kokoro_audio = None
                raise VoiceInterfaceError(
                    f"Could not initialise Kokoro TTS: {exc}"
                ) from exc

        return self._kokoro_engine, self._kokoro_audio

    def _split_kokoro_chunks(self, text: str) -> list[str]:
        """Split speech at sentence/clause boundaries for faster first audio."""
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text:
            return []

        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks: list[str] = []

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            if len(sentence) <= self.kokoro_chunk_chars:
                chunks.append(sentence)
                continue

            clauses = re.split(r"(?<=[,;:])\s+", sentence)
            current = ""

            for clause in clauses:
                clause = clause.strip()
                if not clause:
                    continue

                if len(clause) > self.kokoro_chunk_chars:
                    words = clause.split()
                    for word in words:
                        if (
                            current
                            and len(current) + 1 + len(word)
                            > self.kokoro_chunk_chars
                        ):
                            chunks.append(current)
                            current = word
                        else:
                            current = f"{current} {word}".strip()
                    continue

                candidate = f"{current} {clause}".strip()
                if (
                    current
                    and len(candidate) > self.kokoro_chunk_chars
                ):
                    chunks.append(current)
                    current = clause
                else:
                    current = candidate

            if current:
                chunks.append(current)

        return chunks

    def _speak_kokoro(self, text: str) -> None:
        engine, audio_output = self._load_kokoro()
        chunks = self._split_kokoro_chunks(text)

        try:
            for index, chunk in enumerate(chunks, start=1):
                started = time.perf_counter()
                audio, sample_rate = engine.create(
                    chunk,
                    voice=self.kokoro_voice,
                    speed=self.kokoro_speed,
                    lang=self.kokoro_language,
                )
                generated = time.perf_counter()

                audio_output.play(audio, int(sample_rate))
                audio_output.wait()
                finished = time.perf_counter()

                if self.tts_debug:
                    print(
                        f"[Kokoro TTS] chunk {index}/{len(chunks)}: "
                        f"generation={generated - started:.2f}s, "
                        f"playback={finished - generated:.2f}s"
                    )
        except Exception as exc:
            raise VoiceInterfaceError(
                f"Kokoro speech output failed: {exc}"
            ) from exc

    def _speak_pyttsx3(self, text: str) -> None:
        if self._tts_engine is None:
            try:
                import pyttsx3
            except ImportError as exc:
                raise VoiceInterfaceError(
                    'Text-to-speech dependencies are missing. Run: '
                    'python -m pip install -e ".[dev,voice]"'
                ) from exc

            try:
                self._tts_engine = pyttsx3.init()
                self._tts_engine.setProperty("rate", self.tts_rate)
            except Exception as exc:
                self._tts_engine = None
                raise VoiceInterfaceError(
                    f"Could not initialise speech output: {exc}"
                ) from exc

        try:
            self._tts_engine.say(text)
            self._tts_engine.runAndWait()
        except Exception as exc:
            self._tts_engine = None
            raise VoiceInterfaceError(
                f"Speech output failed: {exc}"
            ) from exc

    def speak(self, text: str) -> None:
        """Speak text using Kokoro when configured, otherwise Windows/local TTS."""
        text = str(text or "").strip()
        if not text:
            return

        with self._tts_lock:
            backend = self.tts_backend
            if backend == "auto":
                backend = (
                    "kokoro"
                    if self._kokoro_is_configured()
                    else "pyttsx3"
                )

            if backend == "kokoro":
                self._speak_kokoro(text)
                self._active_tts_backend = "kokoro"
            else:
                self._speak_pyttsx3(text)
                self._active_tts_backend = "pyttsx3"
