from __future__ import annotations

import main


class ReconfigurableStream:
    def __init__(self) -> None:
        self.options = None

    def reconfigure(self, **kwargs) -> None:
        self.options = kwargs


def test_console_encoding_uses_unicode_safe_output(monkeypatch) -> None:
    stream = ReconfigurableStream()
    monkeypatch.setattr(main.sys, "stdout", stream)

    main._configure_console_encoding()

    assert stream.options == {"encoding": "utf-8", "errors": "replace"}


def test_console_encoding_tolerates_streams_that_cannot_be_reconfigured(monkeypatch) -> None:
    class LockedStream:
        def reconfigure(self, **kwargs) -> None:
            raise ValueError("stream is already closed")

    monkeypatch.setattr(main.sys, "stdout", LockedStream())

    # Must not prevent JARVIS from starting when an embedded/test stream is locked.
    main._configure_console_encoding()
