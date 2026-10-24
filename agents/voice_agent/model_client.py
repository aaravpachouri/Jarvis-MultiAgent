"""OpenAI-compatible model client with Gemini -> Groq -> Ollama failover.

Public API preserves answer_chat(), answer_with_sources(), and decide().
Uses requests for HTTP requests to avoid urllib-specific provider blocks.
"""
from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests


class ModelClientError(RuntimeError):
    """Raised when no configured provider can complete a request."""


@dataclass(frozen=True)
class _Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    timeout: float
    requires_key: bool = True


def _is_loopback_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()

        if host in {"localhost", "127.0.0.1", "::1"}:
            return True

        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    except (ValueError, OSError):
        return False

def _is_render_hosted() -> bool:
    """Detect Render using its documented platform environment variable."""
    return (
        os.getenv("RENDER", "").strip().casefold() == "true"
        or bool(os.getenv("RENDER_SERVICE_ID", "").strip())
    )


class OpenAICompatibleClient:
    """Try Gemini first, then Groq, then local Ollama."""

    def __init__(self) -> None:
        try:
            timeout = float(
                os.getenv("JARVIS_LLM_TIMEOUT_SECONDS", "25")
            )
        except (TypeError, ValueError):
            timeout = 25.0

        self.timeout = min(max(timeout, 1.0), 120.0)
        self.active_provider: str | None = None
        self.last_provider_errors: list[str] = []
        self.providers = self._load_providers()
        self.configured = bool(self.providers)

        # Reuse connections where possible.
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "JARVIS-Agent-Network/1.0"}
        )

    def _load_providers(self) -> list[_Provider]:
        providers: list[_Provider] = []
        render_hosted = _is_render_hosted()

        # Primary: Gemini OpenAI-compatible endpoint.
        gemini_url = os.getenv(
            "JARVIS_LLM_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai",
        ).strip().rstrip("/")

        gemini_key = os.getenv("JARVIS_LLM_API_KEY", "").strip()
        gemini_model = os.getenv(
            "JARVIS_LLM_MODEL", "gemini-3.8-flash"
        ).strip()

        if gemini_url and gemini_key and gemini_model:
            providers.append(
                _Provider(
                    "gemini",
                    gemini_url,
                    gemini_key,
                    gemini_model,
                    self.timeout,
                )
            )

        # Secondary: Groq.
        groq_url = os.getenv(
            "JARVIS_LLM_SECONDARY_BASE_URL",
            "https://api.groq.com/openai/v1",
        ).strip().rstrip("/")

        groq_key = os.getenv(
            "JARVIS_LLM_SECONDARY_API_KEY", ""
        ).strip()

        groq_model = os.getenv(
            "JARVIS_LLM_SECONDARY_MODEL", ""
        ).strip()

        if groq_url and groq_key and groq_model:
            providers.append(
                _Provider(
                    "groq",
                    groq_url,
                    groq_key,
                    groq_model,
                    self.timeout,
                )
            )

        # Final fallback: local Ollama.
        # On Render, localhost refers to the Render container, not the
        # user's desktop. Do not register a loopback provider by default there.
        default_ollama_url = "" if render_hosted else "http://localhost:11434/v1"
        ollama_url = os.getenv(
            "JARVIS_LLM_FALLBACK_BASE_URL",
            default_ollama_url,
        ).strip().rstrip("/")

        ollama_key = os.getenv(
            "JARVIS_LLM_FALLBACK_API_KEY", ""
        ).strip()

        ollama_model = os.getenv(
            "JARVIS_LLM_FALLBACK_MODEL",
            "llama3.2:latest",
        ).strip()

        if ollama_url and ollama_model:
            # A localhost Ollama instance on the developer's PC cannot be
            # reached from Render. Also skip an explicitly stale loopback URL.
            if render_hosted and _is_loopback_url(ollama_url):
                pass
            elif ollama_key or _is_loopback_url(ollama_url):
                providers.append(
                    _Provider(
                        "ollama",
                        ollama_url,
                        ollama_key,
                        ollama_model,
                        self.timeout,
                        requires_key=False,
                    )
                )

        return providers

    def answer_chat(
        self, messages: list[dict[str, str]]
    ) -> str:
        return self._request_with_fallback(
            messages, json_mode=False
        )

    def answer_with_sources(
        self, messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        answer = self.answer_chat(messages)

        return {
            "answer": answer,
            "sources": [],
            "provider": self.active_provider,
        }

    def decide(
        self, messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        raw = self._request_with_fallback(
            messages, json_mode=True
        )

        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ModelClientError(
                f"Model returned invalid decision JSON: {exc}"
            ) from exc

        if not isinstance(value, dict):
            raise ModelClientError(
                "Model decision response must be a JSON object."
            )

        return value

    def _request_with_fallback(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool,
    ) -> str:
        if not self.providers:
            if _is_render_hosted():
                raise ModelClientError(
                    "No cloud model provider is configured for Render. Add "
                    "JARVIS_LLM_API_KEY in the Render service Environment settings "
                    "(Gemini; the default model is gemini-3.8-flash), or configure "
                    "JARVIS_LLM_SECONDARY_API_KEY and JARVIS_LLM_SECONDARY_MODEL "
                    "for Groq. Local Ollama on your computer is not reachable from Render."
                )
            raise ModelClientError(
                "No model provider is configured. Set Gemini credentials, "
                "configure Groq, or explicitly configure a reachable Ollama endpoint."
            )

        errors: list[str] = []

        for provider in self.providers:
            try:
                result = self._request_provider(
                    provider,
                    messages,
                    json_mode=json_mode,
                )

                if not isinstance(result, str) or not result.strip():
                    raise ModelClientError(
                        "Provider returned an empty response."
                    )

                if json_mode:
                    try:
                        parsed = json.loads(result)
                    except json.JSONDecodeError as exc:
                        raise ModelClientError(
                            "Provider returned invalid JSON for a decision."
                        ) from exc

                    if not isinstance(parsed, dict):
                        raise ModelClientError(
                            "Provider decision response was not "
                            "a JSON object."
                        )

                self.active_provider = provider.name
                self.last_provider_errors = errors.copy()

                return result.strip()

            except ModelClientError as exc:
                errors.append(f"{provider.name}: {exc}")

            except Exception as exc:
                errors.append(
                    f"{provider.name}: {type(exc).__name__}: {exc}"
                )

        self.last_provider_errors = errors.copy()
        details = " | ".join(errors[-3:])

        raise ModelClientError(
            f"All configured model providers failed. {details}"
        )

    def _request_provider(
        self,
        provider: _Provider,
        messages: list[dict[str, str]],
        *,
        json_mode: bool,
    ) -> str:
        payload: dict[str, Any] = {
            "model": provider.model,
            "messages": messages,
            "temperature": 0.2,
        }

        if json_mode:
            payload["response_format"] = {
                "type": "json_object"
            }

        headers = {"Content-Type": "application/json"}

        if provider.api_key:
            headers["Authorization"] = (
                f"Bearer {provider.api_key}"
            )

        url = f"{provider.base_url}/chat/completions"

        try:
            response = self._session.post(
                url,
                headers=headers,
                json=payload,
                timeout=provider.timeout,
            )
            response.raise_for_status()

        except requests.HTTPError as exc:
            status = (
                exc.response.status_code
                if exc.response is not None
                else "unknown"
            )

            # Do not expose response bodies or authorization headers.
            raise ModelClientError(
                f"HTTP {status}"
            ) from exc

        except requests.Timeout as exc:
            raise ModelClientError(
                "request timed out"
            ) from exc

        except requests.RequestException as exc:
            raise ModelClientError(
                f"request failed ({type(exc).__name__})"
            ) from exc

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]

        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelClientError(
                "provider returned an unexpected response format"
            ) from exc

        # Handle compatible APIs that return content blocks.
        if isinstance(content, list):
            content = "".join(
                str(item.get("text", ""))
                if isinstance(item, dict)
                else str(item)
                for item in content
            )

        if not isinstance(content, str) or not content.strip():
            raise ModelClientError(
                "provider returned no text content"
            )

        return content.strip()