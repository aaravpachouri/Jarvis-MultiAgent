"""Keep the test suite independent of a developer's local search API settings."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_search_provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests that exercise a provider set their own values explicitly. Removing
    # shell/.env values here prevents a developer's Tavily key from redirecting
    # unrelated DuckDuckGo-mocked tests and makes the suite reproducible.
    for name in ("JARVIS_SEARCH_PROVIDER", "TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
