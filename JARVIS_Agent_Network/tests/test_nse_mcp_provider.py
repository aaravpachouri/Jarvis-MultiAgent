from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.trading_agent.nse_mcp_provider import NSEMCPProvider, NSEMCPError


class FakeResponse:
    def __init__(self, payload, headers=None, status_code=200):
        self._payload = payload
        self.headers = headers or {"Content-Type": "application/json"}
        self.status_code = status_code
        self.text = "body" if payload is not None else ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("bad status")


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.headers = {}
        self.calls = []

    def post(self, url, json, headers, timeout):
        self.calls.append((url, json, headers, timeout))
        return next(self.responses)


def test_mcp_initialize_and_list_tools():
    session = FakeSession([
        FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}, {"Content-Type": "application/json", "Mcp-Session-Id": "abc"}),
        FakeResponse(None, {"Content-Type": "application/json"}, 202),
        FakeResponse({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "get_live_market_data_by_index", "inputSchema": {"type": "object", "properties": {}}}]} }),
    ])
    provider = NSEMCPProvider(endpoint="https://example.test/mcp")
    provider.session = session
    tools = provider.list_tools()
    assert tools[0]["name"] == "get_live_market_data_by_index"
    assert len(session.calls) == 3
    assert session.calls[1][1]["method"] == "notifications/initialized"
    assert session.calls[2][2]["Mcp-Session-Id"] == "abc"


def test_mcp_call_tool_returns_result():
    provider = NSEMCPProvider(endpoint="https://example.test/mcp")
    provider._initialized = True
    provider.session = FakeSession([FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ok"}]}})])
    result = provider.call_tool("get_quote", {"symbol": "RELIANCE"})
    assert result["content"][0]["text"] == "ok"


def test_mcp_tool_arguments_does_not_guess_unknown_required_fields():
    tool = {"name": "quote", "inputSchema": {"type": "object", "properties": {"required_code": {"type": "string"}}, "required": ["required_code"]}}
    assert NSEMCPProvider._tool_arguments(tool) == {}


def test_mcp_tool_arguments_selects_index_and_symbol():
    tool = {"name": "get_live_market_data_by_index", "inputSchema": {"type": "object", "properties": {"index_name": {"type": "string"}, "symbol": {"type": "string"}}}}
    assert NSEMCPProvider._tool_arguments(tool) == {"index_name": "NIFTY 50", "symbol": "RELIANCE"}


def test_mcp_rpc_error_is_reported_without_secret_leak():
    provider = NSEMCPProvider(endpoint="https://example.test/mcp")
    provider._initialized = True
    provider.session = FakeSession([FakeResponse({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "tool unavailable"}})])
    with pytest.raises(NSEMCPError, match="tool unavailable"):
        provider.call_tool("missing_tool")


def test_nse_markdown_report_is_compact_and_points_to_full_json(tmp_path, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace
    from agents.trading_agent.agent import TradingAgent

    payload = {
        "provider": "NSE Market Data MCP",
        "endpoint": "https://example.test/mcp",
        "data_status": "DATA_RETURNED",
        "tools_discovered": 15,
        "provider_claimed_delay": "about 1-3 minutes",
        "selected_tools": {"index": "live_index"},
        "results": {"index": {"large_blob": "X" * 4000, "last": 123}},
        "tool_errors": {"losers": "not available"},
        "available_tools": [{"name": f"tool_{i}", "description": "long description"} for i in range(30)],
        "disclaimer": "Read-only informational data.",
    }
    agent = TradingAgent()
    monkeypatch.setattr(agent, "reports_dir", str(tmp_path))
    async def fake_fetch():
        return payload
    monkeypatch.setattr("agents.trading_agent.agent.NSEMCPProvider.get_live_market_brief", lambda self: payload)
    result = asyncio.run(agent._daily_market_brief_nse_mcp())
    md = (tmp_path / "market_brief_latest.md").read_text(encoding="utf-8")
    full_json = json.loads((tmp_path / "market_brief_latest.json").read_text(encoding="utf-8"))
    assert result["data_status"] == "DATA_RETURNED"
    assert len(md) < 3000
    assert "market_brief_latest.json" in md
    assert "X" * 100 not in md
    assert len(full_json["available_tools"]) == 30
    assert len(full_json["results"]["index"]["large_blob"]) == 4000
