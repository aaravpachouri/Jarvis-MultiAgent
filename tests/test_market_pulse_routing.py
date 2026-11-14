from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.contracts import TaskRequest, TaskResult, TaskStatus
from agents.trading_agent.agent import DEFAULT_UNIVERSE, TradingAgent
from agents.voice_agent.agent import VoiceAgent


MARKET_PULSE_PROMPT = """OPERATION: MARKET PULSE — INDIA

MISSION OBJECTIVE
Run a real, provider-backed analysis of the Indian stock market and identify a shortlist of stocks worth investigating for today's trading session. Use the existing JARVIS Agent Network and Quant Desk, including the company/ticker search feature. This is a research-only mission.

PHASE 1 — MARKET STATUS
Request the latest available Nifty 50 and Sensex data, source and quote timestamps, retrieval time, and freshness. Never describe historical or delayed data as live.

PHASE 2 — COMPANY SEARCH
Use the Quant Desk company-search functionality to look up these Indian companies individually:
- Reliance Industries
- Tata Consultancy Services (TCS)
- Infosys
- ICICI Bank
- Larsen & Toubro
- Bharti Airtel
- Hindustan Aeronautics Limited (HAL)
- State Bank of India (SBI)

For each company: verify listing, quote, currency, previous close, change, and historical data.

PHASE 3 — TECHNICAL RESEARCH
Calculate indicators only when the provider returns sufficient candles.

PHASE 4 — SHORTLIST
Create a ranked research shortlist of up to five candidates. If no candidate meets the evidence requirements, return an empty shortlist.

PHASE 5 — INDEPENDENT VERIFICATION
Use Athena to review the supplied data and report the actual agents executed, task IDs and handoffs.

PHASE 6 — FINAL REPORT
Have Scribe create data/reports/market_pulse_india.md; if it exists, use a numbered alternative.

SAFETY
Do not place trades, use paid APIs, fabricate prices, modify application source code, or overwrite existing reports. Record provider failures honestly.
"""


class FakeYahooLookup:
    def __init__(self):
        self.search_calls: list[str] = []
        self.quote_calls: list[tuple[str, str]] = []
        self.symbol_by_company = {
            "reliance industries": ("RELIANCE.NS", "Reliance Industries Limited"),
            "tata consultancy services": ("TCS.NS", "Tata Consultancy Services Limited"),
            "infosys": ("INFY.NS", "Infosys Limited"),
            "icici bank": ("ICICIBANK.NS", "ICICI Bank Limited"),
            "larsen & toubro": ("LT.NS", "Larsen & Toubro Limited"),
            "bharti airtel": ("BHARTIARTL.NS", "Bharti Airtel Limited"),
            "hindustan aeronautics limited": ("HAL.NS", "Hindustan Aeronautics Limited"),
            "state bank of india": ("SBIN.NS", "State Bank of India"),
        }

    def search(self, query: str, *, region: str = "IN", limit: int = 12) -> dict[str, Any]:
        self.search_calls.append(query)
        key = next((name for name in self.symbol_by_company if name in query.casefold()), None)
        if key is None:
            return {"ok": True, "query": query, "region": region, "count": 0, "results": []}
        symbol, name = self.symbol_by_company[key]
        return {"ok": True, "query": query, "region": region, "count": 1, "results": [{
            "symbol": symbol, "name": name, "exchange": "NSE", "quote_type": "EQUITY", "region": "IN",
        }]}

    def quote(self, symbol: str, *, chart_range: str = "3mo") -> dict[str, Any]:
        self.quote_calls.append((symbol, chart_range))
        now = datetime.now(timezone.utc)
        stamp = now.isoformat(timespec="seconds")
        closes = [100 + idx * 1.5 for idx in range(65)]
        candles = []
        for idx, close in enumerate(closes):
            date = now - timedelta(days=(64 - idx))
            candles.append({
                "timestamp_utc": date.isoformat(timespec="seconds"),
                "open": close - 0.2, "high": close + 0.8, "low": close - 0.6,
                "close": close, "volume": 1000 + idx * 10,
            })
        if symbol == "^NSEI":
            name, currency, price = "Nifty 50", "INR", 25000.0
        elif symbol == "^BSESN":
            name, currency, price = "BSE Sensex", "INR", 82000.0
        else:
            name = next((name for _key, (sym, name) in self.symbol_by_company.items() if sym == symbol), symbol)
            currency, price = "INR", closes[-1] + 1.0
        previous = price - 1.0
        return {
            "ok": True, "symbol": symbol, "name": name, "exchange": "NSE", "currency": currency,
            "instrument_type": "INDEX" if symbol.startswith("^") else "EQUITY",
            "price": price, "previous_close": previous, "change": 1.0, "change_pct": 1.0 / previous * 100,
            "source_timestamp_utc": stamp, "source_age_seconds": 30, "freshness_label": "RECENT SOURCE TIMESTAMP",
            "retrieved_at_utc": stamp, "provider": "Yahoo Finance test fixture",
            "data_mode": "Fixture for tests; not live market data.", "chart_range": chart_range,
            "interval": "1d", "candles": candles, "source_url": f"https://finance.yahoo.com/quote/{symbol}/", "cached": False,
        }


def _market_data_payload(*, usable_quotes: int = 8, index_quotes: int = 2) -> dict[str, Any]:
    companies = []
    requested = [
        ("RELIANCE.NS", "Reliance Industries"), ("TCS.NS", "Tata Consultancy Services"),
        ("INFY.NS", "Infosys"), ("ICICIBANK.NS", "ICICI Bank"),
        ("LT.NS", "Larsen & Toubro"), ("BHARTIARTL.NS", "Bharti Airtel"),
        ("HAL.NS", "Hindustan Aeronautics Limited"), ("SBIN.NS", "State Bank of India"),
    ]
    for index, (ticker, name) in enumerate(requested):
        if index < usable_quotes:
            quote = {
                "symbol": ticker, "name": name, "exchange": "NSE", "currency": "INR", "price": 100.0 + index,
                "previous_close": 99.0 + index, "change": 1.0, "change_pct": 1.0,
                "source_timestamp_utc": "2026-10-21T10:00:00+00:00", "retrieved_at_utc": "2026-10-21T10:01:00+00:00",
                "freshness_label": "RECENT SOURCE TIMESTAMP", "provider": "Yahoo fixture", "data_mode": "Historical chart data",
                "source_url": f"https://finance.yahoo.com/quote/{ticker}/",
            }
            metrics = {"history_points": 65, "five_session_return_pct": 2.0, "twenty_session_return_pct": 5.0,
                       "sma20": 120.0, "sma50": 110.0, "daily_volatility_20d_pct": 1.2, "latest_volume": 1234,
                       "volume_vs_recent_median": 1.1, "trend": "bullish"}
            status, eligible, error = "quoted", True, None
        else:
            quote, metrics, status, eligible, error = None, {}, "failed", False, "Provider unavailable"
        companies.append({"requested_name": name, "status": status, "listing": {"symbol": ticker, "name": name, "exchange": "NSE"},
                          "quote": quote, "technical_metrics": metrics, "shortlist_eligible": eligible, "error": error})
    indexes = []
    for index, label in enumerate(("Nifty 50", "BSE Sensex")):
        if index < index_quotes:
            indexes.append({"label": label, "status": "quoted", "symbol": "^NSEI" if index == 0 else "^BSESN",
                            "price": 25000.0 if index == 0 else 82000.0, "currency": "INR", "change": 100.0, "change_pct": .4,
                            "source_timestamp_utc": "2026-10-21T10:00:00+00:00", "source_age_seconds": 30,
                            "freshness_label": "RECENT SOURCE TIMESTAMP", "retrieved_at_utc": "2026-10-21T10:01:00+00:00",
                            "provider": "Yahoo fixture", "source_url": "https://finance.yahoo.com/"})
        else:
            indexes.append({"label": label, "status": "failed", "symbol": "^NSEI" if index == 0 else "^BSESN", "error": "Provider unavailable"})
    shortlist = [{"rank": idx + 1, "requested_name": row["requested_name"], "symbol": row["quote"]["symbol"],
                  "price": row["quote"]["price"], "currency": "INR", "source_timestamp_utc": row["quote"]["source_timestamp_utc"],
                  "freshness_label": row["quote"]["freshness_label"], "score": 7.0, "five_session_return_pct": 2.0,
                  "twenty_session_return_pct": 5.0, "sma20": 120.0, "sma50": 110.0, "volatility_20d_pct": 1.2,
                  "reason": "fixture-supported trend", "invalidation": "reassess below SMA20"}
                 for idx, row in enumerate(companies) if row.get("shortlist_eligible")][:5]
    return {
        "report_type": "market_pulse_india", "generated_at_utc": "2026-10-21T10:01:00+00:00",
        "market_status": "UNKNOWN", "market_status_note": "Session status not independently verified.",
        "provider": "Yahoo fixture", "data_mode": "Historical chart fixture; no real-time claim.",
        "company_results": companies, "index_results": indexes, "research_shortlist": shortlist,
        "attempted_company_searches": 8, "successful_company_quotes": usable_quotes,
        "failed_company_searches_or_quotes": 8 - usable_quotes, "successful_index_quotes": index_quotes,
        "summary": f"Attempted 8 companies; usable quotes {usable_quotes}; index quotes {index_quotes}/2; shortlist {len(shortlist)}.",
        "analysis_policy": "Fixture evidence only; no orders were placed.", "disclaimer": "Research only.",
    }


def test_market_pulse_intent_preempts_collaboration_diagnostic_and_research_to_forge():
    assert VoiceAgent._is_quant_market_research_request(MARKET_PULSE_PROMPT)
    assert not VoiceAgent._is_local_agent_collaboration_audit(MARKET_PULSE_PROMPT)
    assert not VoiceAgent._is_research_to_forge_request(MARKET_PULSE_PROMPT)  # It may contain words like Forge in broader variants.
    diagnostic = "Investigate our actual local codebase and trace the runtime event bus, agent delegation, inbox delivery and task lineage."
    assert VoiceAgent._is_local_agent_collaboration_audit(diagnostic)
    assert not VoiceAgent._is_quant_market_research_request(diagnostic)


def test_company_query_parser_recovers_eight_names_even_if_bullets_are_flattened():
    flattened = MARKET_PULSE_PROMPT.replace("\n", " ")
    targets = TradingAgent._extract_market_pulse_company_queries(flattened, DEFAULT_UNIVERSE)
    assert [target["requested_name"] for target in targets] == [
        "Reliance Industries", "Tata Consultancy Services", "Infosys", "ICICI Bank",
        "Larsen & Toubro", "Bharti Airtel", "Hindustan Aeronautics Limited", "State Bank of India",
    ]
    assert targets[1]["ticker_hint"] == "TCS"
    assert targets[6]["ticker_hint"] == "HAL"


def test_quant_market_pulse_uses_provider_search_and_real_quote_payloads_without_writing_files(tmp_path):
    lookup = FakeYahooLookup()
    agent = TradingAgent(stock_lookup=lookup)
    task = TaskRequest(prompt=MARKET_PULSE_PROMPT, context={"workflow": "market_pulse_india"}, target_agent_id="trading.quant")
    context = SimpleNamespace(agent=SimpleNamespace(agent_id="trading.quant"), task=task, runtime=None)
    result = asyncio.run(agent(task, context))
    assert result["report_type"] == "market_pulse_india"
    assert result["attempted_company_searches"] == 8
    assert result["successful_company_quotes"] == 8
    assert result["successful_index_quotes"] == 2
    assert len(lookup.search_calls) == 8
    assert len(lookup.quote_calls) == 10
    assert len(result["research_shortlist"]) == 5
    assert result["company_results"][0]["quote"]["symbol"] == "RELIANCE.NS"
    assert result["company_results"][6]["quote"]["symbol"] == "HAL.NS"
    assert result["company_results"][0]["technical_metrics"]["history_points"] == 65
    assert not list(tmp_path.iterdir())  # Quant returns data only; coordinator owns report creation.


def test_market_pulse_refuses_to_rank_stale_quotes():
    class StaleLookup(FakeYahooLookup):
        def quote(self, symbol: str, *, chart_range: str = "3mo") -> dict[str, Any]:
            result = super().quote(symbol, chart_range=chart_range)
            result["source_age_seconds"] = 90000
            result["freshness_label"] = "OLDER DATA · TEST FIXTURE"
            return result

    agent = TradingAgent(stock_lookup=StaleLookup())
    task = TaskRequest(prompt=MARKET_PULSE_PROMPT, context={"workflow": "market_pulse_india"}, target_agent_id="trading.quant")
    context = SimpleNamespace(agent=SimpleNamespace(agent_id="trading.quant"), task=task, runtime=None)
    result = asyncio.run(agent(task, context))
    assert result["successful_company_quotes"] == 8
    assert result["research_shortlist"] == []
    assert all(item["shortlist_eligible"] is False for item in result["company_results"])


class _Spec:
    def __init__(self, agent_id: str, capabilities: tuple[str, ...]):
        self.agent_id = agent_id
        self.name = agent_id
        self.capabilities = capabilities


class _Registry:
    def __init__(self, specs):
        self._specs = specs
    def list(self, enabled_only=True):
        return list(self._specs)


class _FakeRuntime:
    def __init__(self):
        self.registry = _Registry([
            _Spec("trading.quant", ("trading.market_analysis",)),
            _Spec("research.athena", ("research.synthesize",)),
            _Spec("documentation.scribe", ("writing.report",)),
        ])
        self.audit_log_path = None
    def has_handler(self, agent_id: str) -> bool:
        return any(spec.agent_id == agent_id for spec in self.registry.list())


class _DelegatingContext:
    def __init__(self, root_task: TaskRequest, runtime: _FakeRuntime, *, quant_data: dict[str, Any]):
        self.task = root_task
        self.runtime = runtime
        self.agent = SimpleNamespace(agent_id="voice.jarvis")
        self.quant_data = quant_data
        self.calls: list[tuple[str, dict[str, Any]]] = []
    async def delegate(self, prompt: str, *, agent_id: str, capabilities=(), context=None, requested_by=None, **kwargs):
        self.calls.append((agent_id, dict(context or {})))
        task_id = f"task-{len(self.calls)}"
        if agent_id == "trading.quant":
            output = self.quant_data
        elif agent_id == "research.athena":
            output = {"summary": "Athena reviewed only the supplied market observations; no external search was performed."}
        elif agent_id == "documentation.scribe":
            body = str((context or {}).get("body") or "")
            output = {"content": "# Market Pulse — India\n\nProvider-backed company search summary and report outline.\n\n" + body}
        else:
            raise AssertionError(f"Unexpected delegate: {agent_id}")
        return TaskResult(task_id=task_id, agent_id=agent_id, status=TaskStatus.SUCCEEDED, output=output, summary="fixture result")


def test_market_pulse_end_to_end_routes_quant_athena_scribe_and_persists_numbered_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_root = tmp_path / "project"
    for directory in (project_root / "core", project_root / "agents", project_root / "data" / "reports"):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(project_root)
    reports = project_root / "data" / "reports"
    (reports / "market_pulse_india.md").write_text("pre-existing report; preserve me\n", encoding="utf-8")

    root = TaskRequest(prompt=MARKET_PULSE_PROMPT, capabilities=("conversation.handle",), target_agent_id="voice.jarvis")
    runtime = _FakeRuntime()
    ctx = _DelegatingContext(root, runtime, quant_data=_market_data_payload())
    voice = VoiceAgent()
    result = asyncio.run(voice(root, ctx))

    assert result.status is TaskStatus.SUCCEEDED, result.error or result.output
    assert "MARKET PULSE — INDIA — SUCCESS" in str(result.output)
    assert "LOCAL AGENT-SYSTEM DIAGNOSTIC" not in str(result.output)
    assert [agent_id for agent_id, _context in ctx.calls] == ["trading.quant", "research.athena", "documentation.scribe"]
    quant_context = ctx.calls[0][1]
    assert quant_context["workflow"] == "market_pulse_india"
    assert ctx.calls[1][1]["analysis_scope"] == "offline_context_analysis_only"
    assert ctx.calls[2][1]["title"] == "Market Pulse — India"
    report_path = Path(result.metadata["report_path"])
    assert report_path == reports / "market_pulse_india-1.md"
    assert report_path.is_file()
    report = report_path.read_text(encoding="utf-8")
    assert "Reliance Industries" in report
    assert "Athena evidence review" in report
    assert "Runtime execution trace" in report
    assert (reports / "market_pulse_india.md").read_text(encoding="utf-8") == "pre-existing report; preserve me\n"


def test_market_pulse_returns_failed_status_if_provider_data_is_unusable_but_saves_failure_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for directory in (tmp_path / "core", tmp_path / "agents", tmp_path / "data" / "reports"):
        directory.mkdir(parents=True, exist_ok=True)
    root = TaskRequest(prompt=MARKET_PULSE_PROMPT, capabilities=("conversation.handle",), target_agent_id="voice.jarvis")
    ctx = _DelegatingContext(root, _FakeRuntime(), quant_data=_market_data_payload(usable_quotes=0, index_quotes=0))
    result = asyncio.run(VoiceAgent()(root, ctx))
    assert result.status is TaskStatus.FAILED
    assert "PARTIAL/FAILED" in str(result.output)
    path = Path(result.metadata["report_path"])
    assert path.is_file()
    assert "Provider unavailable" in path.read_text(encoding="utf-8")


def test_actual_runtime_delegates_market_pulse_to_quant_then_athena_and_scribe(tmp_path, monkeypatch):
    from core.contracts import AgentSpec
    from core.runtime import AgentRuntime
    from agents.research_agent.agent import ResearchAgent
    from agents.documentation_agent.agent import DocumentationAgent

    monkeypatch.delenv("JARVIS_MARKET_REPORTS_DIR", raising=False)
    project_root = tmp_path / "jarvis-project"
    for directory in (project_root / "core", project_root / "agents", project_root / "data" / "reports"):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(project_root)
    runtime = AgentRuntime(audit_log_path=project_root / "data" / "logs" / "audit.jsonl")
    trace = runtime.bus.subscribe(queue_size=512)
    runtime.register_agent(AgentSpec(
        agent_id="voice.jarvis", name="JARVIS", role="coordinator", description="Coordinates supported tasks",
        capabilities=("conversation.handle",),
    ), VoiceAgent())
    runtime.register_agent(AgentSpec(
        agent_id="trading.quant", name="Quant", role="market analysis", description="Read-only provider-backed market analysis",
        capabilities=("trading.market_analysis",),
    ), TradingAgent(stock_lookup=FakeYahooLookup()))
    runtime.register_agent(AgentSpec(
        agent_id="research.athena", name="Athena", role="evidence review", description="Reviews supplied evidence",
        capabilities=("research.synthesize",),
    ), ResearchAgent())
    runtime.register_agent(AgentSpec(
        agent_id="documentation.scribe", name="Scribe", role="report drafting", description="Drafts reports from supplied context",
        capabilities=("writing.report",),
    ), DocumentationAgent())

    result = asyncio.run(runtime.run_task(TaskRequest(
        prompt=MARKET_PULSE_PROMPT,
        capabilities=("conversation.handle",),
        target_agent_id="voice.jarvis",
    )))
    assert result.status is TaskStatus.SUCCEEDED, result.error or result.output
    assert "MARKET PULSE — INDIA — SUCCESS" in str(result.output)
    assert result.metadata["workflow"] == "market_pulse_india"
    assert result.metadata["company_searches_attempted"] == 8
    assert result.metadata["usable_company_quotes"] == 8
    assert result.metadata["usable_index_quotes"] == 2
    children = [item for item in runtime._task_results.values() if item.task_id != result.task_id]
    assert {item.agent_id for item in children} == {"trading.quant", "research.athena", "documentation.scribe"}
    assert len(children) == 3
    report = Path(result.metadata["report_path"])
    assert report == project_root / "data" / "reports" / "market_pulse_india.md"
    content = report.read_text(encoding="utf-8")
    assert "Reliance Industries" in content and "State Bank of India" in content
    assert "Athena evidence review" in content and "Runtime execution trace" in content
    events = []
    while not trace.queue.empty():
        events.append(trace.get_nowait())
    action_names = [event.payload.get("action_name") for event in events
                    if getattr(event, "event_type", "").startswith("agent.action.")]
    assert "quant.market_pulse.research" in action_names
    assert "quant.company.search" in action_names
    assert "quant.company.quote" in action_names
