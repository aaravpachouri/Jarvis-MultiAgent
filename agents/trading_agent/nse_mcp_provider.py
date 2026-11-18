"""Read-only client for the official NSE Market Data MCP endpoints.

Uses the public Streamable HTTP MCP interface documented by NSE. The service
may provide prices a few minutes behind exchange activity; every result is
labelled with retrieval time and provider freshness metadata when available.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

NSE_CM_MCP_URL = "https://mcp.nseindia.in/cmmkt/mcp"
NSE_BHAVCOPY_MCP_URL = "https://mcp.nseindia.in/bhavcopy/cm/mcp"


class NSEMCPError(RuntimeError):
    """NSE MCP connection, protocol, or tool-call error."""


class NSEMCPProvider:
    def __init__(self, *, endpoint: str | None = None, timeout_seconds: float = 15.0) -> None:
        configured_endpoint = os.getenv("JARVIS_NSE_MCP_URL", "").strip()
        self.endpoint = (endpoint or configured_endpoint or NSE_CM_MCP_URL).strip()
        # A stale/mistyped .env override is a common source of HTTP 404s. When
        # the endpoint came from .env, retry the canonical official endpoint once
        # before surfacing the error. Explicit constructor endpoints stay isolated
        # so tests and intentional custom deployments never unexpectedly switch.
        self._retry_canonical_on_404 = (
            endpoint is None and bool(configured_endpoint) and self.endpoint != NSE_CM_MCP_URL
        )
        self.timeout_seconds = max(3.0, float(timeout_seconds))
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "JARVIS-Quant/1.1",
        })
        self._request_id = int(time.time() * 1000) % 2_000_000_000
        self._session_id: str | None = None
        self._initialized = False

    @staticmethod
    def _display_endpoint(endpoint: str) -> str:
        """Return the endpoint without query strings or credentials."""
        try:
            parsed = urlsplit(endpoint)
            hostname = parsed.hostname or "configured-endpoint"
            host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
            try:
                if parsed.port:
                    host += f":{parsed.port}"
            except ValueError:
                pass
            return f"{parsed.scheme}://{host}{parsed.path}"[:240]
        except ValueError:
            return "configured NSE MCP endpoint"

    def _rpc(self, method: str, params: dict[str, Any] | None = None, *, notification: bool = False) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notification:
            self._request_id += 1
            payload["id"] = self._request_id
        if params is not None:
            payload["params"] = params

        endpoint = self.endpoint
        headers = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            response = self.session.post(endpoint, json=payload, headers=headers, timeout=self.timeout_seconds)
            if response.status_code == 404 and self._retry_canonical_on_404 and endpoint != NSE_CM_MCP_URL:
                # Retry only the documented official live endpoint; do not use a
                # different provider or silently substitute historical data here.
                endpoint = NSE_CM_MCP_URL
                self.endpoint = endpoint
                self._session_id = None
                response = self.session.post(
                    endpoint, json=payload, headers={}, timeout=self.timeout_seconds
                )
                self._retry_canonical_on_404 = False
                if response.status_code == 404:
                    raise NSEMCPError(
                        "NSE MCP returned HTTP 404 during " + method + " at " + self._display_endpoint(endpoint) +
                        ". The canonical endpoint was retried after the configured JARVIS_NSE_MCP_URL also returned 404. "
                        "Verify NSE service availability or remove the stale URL override."
                    )
            if response.status_code >= 400:
                endpoint_label = self._display_endpoint(endpoint)
                if response.status_code == 404:
                    detail = (
                        f"NSE MCP returned HTTP 404 during {method} at {endpoint_label}. "
                        "NSE's published CM Market Live endpoint is " + NSE_CM_MCP_URL + ". "
                        "Check JARVIS_NSE_MCP_URL and retry; the provider may also be temporarily unavailable."
                    )
                else:
                    detail = f"NSE MCP returned HTTP {response.status_code} during {method} at {endpoint_label}."
                raise NSEMCPError(detail)
            self._session_id = response.headers.get("Mcp-Session-Id", self._session_id)
            if notification or response.status_code == 202 or not response.text.strip():
                return None
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/event-stream" in content_type:
                result = None
                for line in response.text.splitlines():
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data and data != "[DONE]":
                            try:
                                item = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            if item.get("id") == payload.get("id") or "result" in item or "error" in item:
                                result = item
                envelope = result
            else:
                envelope = response.json()
        except NSEMCPError:
            raise
        except requests.RequestException as exc:
            raise NSEMCPError(
                f"Could not reach NSE MCP at {self._display_endpoint(endpoint)} during {method} ({type(exc).__name__})."
            ) from None
        except ValueError:
            raise NSEMCPError(f"NSE MCP returned invalid JSON during {method} at {self._display_endpoint(endpoint)}.") from None
        if not isinstance(envelope, dict):
            raise NSEMCPError(f"NSE MCP returned no JSON-RPC response during {method}.")
        if envelope.get("error"):
            error = envelope["error"]
            message = error.get("message", "Unknown MCP error") if isinstance(error, dict) else str(error)
            raise NSEMCPError(f"NSE MCP error during {method}: {str(message)[:240]}")
        return envelope.get("result")

    def initialize(self) -> None:
        if self._initialized:
            return
        self._rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                  "clientInfo": {"name": "jarvis-quant", "version": "1.1"}})
        self._rpc("notifications/initialized", notification=True)
        self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        result = self._rpc("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if isinstance(result, dict) and result.get("isError"):
            raise NSEMCPError(f"NSE tool {name} reported an error.")
        return result

    @staticmethod
    def _tool_arguments(tool: dict[str, Any]) -> dict[str, Any]:
        schema = tool.get("inputSchema") or {}
        props = schema.get("properties") or {}
        args: dict[str, Any] = {}
        for key, spec in props.items():
            text = f"{key} {spec.get('description', '')}".lower() if isinstance(spec, dict) else key.lower()
            if any(word in text for word in ("index",)):
                args[key] = "NIFTY 50"
            elif any(word in text for word in ("symbol", "ticker", "security")):
                args[key] = "RELIANCE"
            elif any(word in text for word in ("limit", "count", "top")):
                args[key] = 10
            elif "date" in text:
                args[key] = datetime.now().date().isoformat()
            elif isinstance(spec, dict) and "default" in spec:
                args[key] = spec["default"]
        required = schema.get("required", [])
        # Do not guess unknown required values. A failed call is safer than fabricating arguments.
        if any(key not in args for key in required):
            return {}
        return args

    def get_live_market_brief(self) -> dict[str, Any]:
        tools = self.list_tools()
        if not tools:
            raise NSEMCPError("NSE MCP connected but returned no tools.")
        groups = {
            "index": re.compile(r"index.*(live|market|data)|market.*index", re.I),
            "gainers": re.compile(r"top.*gainer|live.*gainer", re.I),
            "losers": re.compile(r"top.*loser|live.*loser", re.I),
            "freshness": re.compile(r"freshness|market.*status", re.I),
        }
        selected: dict[str, dict[str, Any]] = {}
        for category, pattern in groups.items():
            matches = [t for t in tools if pattern.search(f"{t.get('name', '')} {t.get('description', '')}")]
            if matches:
                selected[category] = matches[0]
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for category, tool in selected.items():
            name = str(tool.get("name", ""))
            try:
                results[category] = self.call_tool(name, self._tool_arguments(tool))
            except NSEMCPError as exc:
                errors[category] = str(exc)
        return {
            "provider": "NSE Market Data MCP",
            "endpoint": self.endpoint,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider_claimed_delay": "NSE describes CM Market Live as approximately 1–3 minutes behind real time; verify freshness metadata for each response.",
            "tools_discovered": len(tools),
            "available_tools": [{"name": t.get("name"), "description": t.get("description", "")} for t in tools],
            "selected_tools": {k: v.get("name") for k, v in selected.items()},
            "results": results,
            "tool_errors": errors,
            "data_status": "DATA_RETURNED" if results else "NO_LIVE_DATA_RETURNED",
            "disclaimer": "For informational/educational use. The provider disclaims guarantees of accuracy, completeness, reliability, and timeliness. No trades were placed.",
        }
