"""Local-first JARVIS Intelligence Dashboard API and static server.

Binds to loopback by default. The dashboard intentionally exposes no shell,
filesystem-write, registry-mutation, or trading-order endpoints.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import threading
import time
import uuid
from collections import deque
from urllib.parse import parse_qs
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from core import AgentMessage, TaskEvent, TaskRequest, create_runtime
from core.env import load_project_env
from core.observability import build_diagnostics
from agents.trading_agent.quant_dashboard import read_quant_dashboard_data
from agents.trading_agent.stock_lookup import YahooStockLookup, StockLookupError, StockLookupRateLimit

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "dashboard"
REPORTS = ROOT / "data" / "reports"
HISTORY = ROOT / "data" / "task_history.jsonl"
HOST = os.environ.get("JARVIS_DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("JARVIS_DASHBOARD_PORT", "8765"))
MAX_BODY = 16_384


def _read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _iso_age(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
    except (ValueError, TypeError):
        return None


def read_market_report() -> dict:
    path = REPORTS / "market_brief_latest.json"
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return {"available": False, "kind": "none", "label": "No market report yet", "indexes": [], "stocks": [], "generated_at": None, "age_seconds": None, "report": None}

    report_type = str(payload.get("report_type", "market_report"))
    is_nse_mcp = "nse_mcp" in report_type.lower() or bool(payload.get("provider") == "nse_mcp") or bool(payload.get("source") == "nse_mcp")
    generated = payload.get("generated_at") or payload.get("generated_at_utc") or payload.get("retrieved_at") or payload.get("timestamp")
    indexes_payload = payload.get("indexes", {})
    indexes = []
    if isinstance(indexes_payload, dict):
        for name, values in indexes_payload.items():
            if isinstance(values, dict):
                indexes.append({"name": str(name), "value": values.get("last") or values.get("close") or values.get("value") or values.get("price"), "change": values.get("change_pct") or values.get("change_1d_pct") or values.get("changePercent"), "source_time": values.get("timestamp") or values.get("time")})
    elif isinstance(indexes_payload, list):
        for item in indexes_payload[:12]:
            if isinstance(item, dict):
                indexes.append({"name": item.get("name") or item.get("symbol") or item.get("index"), "value": item.get("last") or item.get("close") or item.get("value") or item.get("price"), "change": item.get("change_pct") or item.get("change_1d_pct") or item.get("changePercent"), "source_time": item.get("timestamp") or item.get("time")})
    stocks = []
    for key in ("gainers_in_monitored_universe", "losers_in_monitored_universe", "stocks", "quotes", "results", "data"):
        values = payload.get(key)
        if isinstance(values, list):
            for item in values[:8]:
                if isinstance(item, dict):
                    stocks.append({"symbol": item.get("ticker") or item.get("symbol") or item.get("name") or item.get("companyName") or "—", "name": item.get("name") or item.get("companyName") or item.get("symbol") or item.get("ticker") or "", "price": item.get("last") or item.get("close") or item.get("price") or item.get("lastPrice"), "change": item.get("change_pct") or item.get("change_1d_pct") or item.get("pChange") or item.get("changePercent"), "source_time": item.get("timestamp") or item.get("time")})
            if stocks:
                break
    return {
        "available": True,
        "kind": "nse_mcp" if is_nse_mcp else "report",
        "label": "NSE MCP snapshot" if is_nse_mcp else "Saved market brief",
        "report_type": report_type,
        "generated_at": generated,
        "age_seconds": _iso_age(generated if isinstance(generated, str) else None),
        "market": payload.get("market", "India markets"),
        "latest_session": payload.get("latest_session"),
        "stocks_screened": payload.get("stocks_screened"),
        "indexes": indexes[:8],
        "stocks": stocks[:8],
        "summary": payload.get("summary") or payload.get("message") or payload.get("status"),
        "tool_count": payload.get("tool_count") or payload.get("discovered_tool_count"),
        "report_path": str(path.relative_to(ROOT)).replace("\\", "/") if path.is_relative_to(ROOT) else path.name,
        "markdown_available": (REPORTS / "market_brief_latest.md").exists(),
    }


class DashboardApplication:
    """Dashboard facade with live runtime-event capture and asynchronous missions."""

    LIVE_EVENT_CAPACITY = 800

    def __init__(self) -> None:
        load_project_env(ROOT)
        self.runtime = create_runtime(project_root=ROOT)
        self.stock_lookup = YahooStockLookup()
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._run_loop, name="jarvis-dashboard-loop", daemon=True)
        self.loop_thread.start()
        self.started_at = time.time()
        self.task_lock = threading.Lock()
        self.job_lock = threading.Lock()
        self.event_lock = threading.Lock()
        self.recent_results: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.job_futures: dict[str, object] = {}
        self.quant_refresh_jobs: dict[str, dict] = {}
        self.quant_refresh_futures: dict[str, object] = {}
        self.live_events: deque[dict] = deque(maxlen=self.LIVE_EVENT_CAPACITY)
        self.live_event_sequence = 0
        self.event_subscription = None
        self.event_observer_future = asyncio.run_coroutine_threadsafe(self._observe_runtime_events(), self.loop)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    @staticmethod
    def _safe_event_payload(payload) -> dict:
        """Expose event metadata, never arbitrary prompt/output/model payloads."""
        if not isinstance(payload, dict):
            return {}
        allow = {
            "status", "parent_task_id", "correlation_id", "delegation_depth",
            "requested_by", "capabilities_requested", "capabilities", "attempt",
            "max_attempts", "fallback_allowed", "next_attempt", "name",
            "error_type", "policy_denied", "missing_approvals", "max_concurrency",
            "action_id", "action_name", "action_type", "duration_ms", "tool_name",
            "provider", "operation", "phase", "count", "result_count", "file_count",
            "format", "model_provider", "capability", "reason_code", "artifact_count",
            "source_message_id", "source_message_type", "delivery",
        }
        return {key: payload[key] for key in allow if key in payload}

    @classmethod
    def _serialize_runtime_event(cls, item) -> dict:
        if isinstance(item, TaskEvent):
            event_type = item.event_type
            task_id = item.task_id
            agent_id = item.agent_id
            timestamp = item.timestamp
            event_id = item.event_id
            payload = item.payload
            from_agent_id = None
            to_agent_id = None
        elif isinstance(item, AgentMessage):
            event_type = item.message_type
            task_id = item.task_id or ""
            agent_id = item.from_agent_id
            timestamp = item.created_at
            event_id = item.message_id
            payload = item.payload
            from_agent_id = item.from_agent_id
            to_agent_id = item.to_agent_id
        else:
            event_type = str(getattr(item, "event_type", getattr(item, "message_type", "runtime.event")))
            task_id = str(getattr(item, "task_id", "") or "")
            agent_id = getattr(item, "agent_id", getattr(item, "from_agent_id", None))
            timestamp = str(getattr(item, "timestamp", getattr(item, "created_at", datetime.now(timezone.utc).isoformat())))
            event_id = str(getattr(item, "event_id", getattr(item, "message_id", "")))
            payload = getattr(item, "payload", {})
            from_agent_id = getattr(item, "from_agent_id", None)
            to_agent_id = getattr(item, "to_agent_id", None)
        return {
            "event_id": str(event_id or ""),
            "type": str(event_type),
            "task_id": str(task_id or ""),
            "agent_id": str(agent_id or ""),
            "from_agent_id": str(from_agent_id or ""),
            "to_agent_id": str(to_agent_id or ""),
            "timestamp": str(timestamp or datetime.now(timezone.utc).isoformat()),
            "payload": cls._safe_event_payload(dict(payload) if isinstance(payload, dict) else {}),
        }

    async def _observe_runtime_events(self) -> None:
        """Copy real event-bus messages into a bounded thread-safe dashboard buffer."""
        subscription = self.runtime.bus.subscribe(queue_size=2048)
        self.event_subscription = subscription
        try:
            while True:
                item = await subscription.get()
                self.append_live_event(self._serialize_runtime_event(item))
        except asyncio.CancelledError:
            raise
        finally:
            self.runtime.bus.unsubscribe(subscription)

    def append_live_event(self, event: dict) -> dict:
        with self.event_lock:
            self.live_event_sequence += 1
            record = {**event, "sequence": self.live_event_sequence}
            self.live_events.append(record)
            return dict(record)

    def record_dashboard_event(self, event_type: str, task_id: str, agent_id: str, *, status: str = "", parent_task_id: str = "") -> dict:
        payload = {"status": status}
        if parent_task_id:
            payload["parent_task_id"] = parent_task_id
        return self.append_live_event({
            "event_id": f"dashboard-{time.time_ns()}", "type": event_type,
            "task_id": task_id, "agent_id": agent_id, "from_agent_id": "",
            "to_agent_id": "", "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        })

    def events_since(self, after_sequence: int = 0) -> dict:
        with self.event_lock:
            events = [dict(item) for item in self.live_events if item["sequence"] > after_sequence]
            latest = self.live_event_sequence
            oldest = self.live_events[0]["sequence"] if self.live_events else latest + 1
        subscription = self.event_subscription
        return {
            "events": events, "cursor": latest,
            "truncated": bool(after_sequence and after_sequence < oldest - 1),
            "connected": bool(subscription is not None and not subscription.closed),
        }

    def active_missions(self) -> list[dict]:
        with self.job_lock:
            return [
                {"task_id": item["task_id"], "agent_id": item["agent_id"], "status": item["status"],
                 "started_at": item.get("started_at"), "prompt": item.get("prompt", "")}
                for item in self.jobs.values() if item.get("status") in {"queued", "running"}
            ]

    def mission_status(self, task_id: str) -> dict | None:
        with self.job_lock:
            job = self.jobs.get(task_id)
            return dict(job) if job else None

    def overview(self) -> dict:
        diagnostics = build_diagnostics(self.runtime, history_path=HISTORY, history_limit=20)
        agents = self.runtime.list_agents()
        active = sum(1 for a in agents if a.get("status") == "busy")
        ready = sum(1 for a in agents if a.get("status") == "ready")
        history = diagnostics.get("task_history", {})
        return {
            "ok": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": int(time.time() - self.started_at),
            "runtime": {**diagnostics["runtime"], "active_agents": active, "ready_agents": ready},
            "agents": agents,
            "task_history": history,
            "market": read_market_report(),
            "recent_results": self._recent_result_snapshot(),
            "active_missions": self.active_missions(),
        }

    def _recent_result_snapshot(self, limit: int = 10) -> list[dict]:
        limit = max(0, min(int(limit), 100))
        with self.task_lock:
            return [dict(item) for item in self.recent_results[-limit:][::-1]] if limit else []

    def _build_task_request(self, prompt: str, target_agent_id: str | None = None) -> TaskRequest:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Enter a task before running JARVIS.")
        if len(prompt) > 6000:
            raise ValueError("Task is too long (maximum 6,000 characters).")
        target = (target_agent_id or "voice.jarvis").strip().lower()
        if target != "voice.jarvis" and not self.runtime.has_handler(target):
            raise ValueError("That agent has no executable handler. Choose JARVIS or a ready specialist.")
        return TaskRequest(prompt=prompt, target_agent_id=target, requested_by="dashboard")

    async def _execute_task_request(self, request: TaskRequest) -> dict:
        result = await self.runtime.run_task(request)
        output = result.output
        if isinstance(output, (dict, list)):
            output_text = json.dumps(output, ensure_ascii=False, indent=2, default=str)
        elif output is None:
            output_text = result.error or result.summary
        else:
            output_text = str(output)
        record = {
            "task_id": result.task_id,
            "agent_id": result.agent_id,
            "status": result.status.value,
            "summary": result.summary,
            "output": output_text[:30000],
            "error": result.error,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "prompt": request.prompt[:500],
            "metadata": dict(result.metadata),
        }
        with self.task_lock:
            self.recent_results.append(record)
            self.recent_results = self.recent_results[-30:]
        return record

    def submit_task(self, prompt: str, target_agent_id: str | None = None) -> dict:
        """Legacy blocking task endpoint retained for CLI integrations and tests."""
        request = self._build_task_request(prompt, target_agent_id)
        self.record_dashboard_event("dashboard.mission.accepted", request.task_id, request.target_agent_id or "voice.jarvis", status="queued")
        future = asyncio.run_coroutine_threadsafe(self._execute_task_request(request), self.loop)
        try:
            return future.result(timeout=360)
        except FutureTimeout:
            future.cancel()
            raise TimeoutError("The task exceeded the dashboard's 6-minute wait limit.")

    def start_mission(self, prompt: str, target_agent_id: str | None = None) -> dict:
        """Accept a mission immediately so the HUD can render live runtime events."""
        request = self._build_task_request(prompt, target_agent_id)
        now = datetime.now(timezone.utc).isoformat()
        agent_id = request.target_agent_id or "voice.jarvis"
        job = {
            "task_id": request.task_id, "agent_id": agent_id, "status": "queued",
            "prompt": request.prompt[:500], "accepted_at": now, "started_at": None,
            "finished_at": None, "result": None, "error": None,
        }
        with self.job_lock:
            self.jobs[request.task_id] = job
        self.record_dashboard_event("dashboard.mission.queued", request.task_id, agent_id, status="queued")
        future = asyncio.run_coroutine_threadsafe(self._run_mission_job(request), self.loop)
        with self.job_lock:
            self.job_futures[request.task_id] = future
        return dict(job)

    async def _run_mission_job(self, request: TaskRequest) -> None:
        task_id = request.task_id
        agent_id = request.target_agent_id or "voice.jarvis"
        with self.job_lock:
            job = self.jobs.get(task_id)
            if job is None:
                return
            job["status"] = "running"
            job["started_at"] = datetime.now(timezone.utc).isoformat()
        self.record_dashboard_event("dashboard.mission.running", task_id, agent_id, status="running")
        try:
            result = await self._execute_task_request(request)
            status = str(result.get("status") or "failed")
            with self.job_lock:
                job = self.jobs[task_id]
                job["status"] = status
                job["finished_at"] = result.get("finished_at") or datetime.now(timezone.utc).isoformat()
                job["result"] = result
                job["error"] = result.get("error")
            self.record_dashboard_event("dashboard.mission.result", task_id, result.get("agent_id") or agent_id, status=status)
        except asyncio.CancelledError:
            with self.job_lock:
                job = self.jobs[task_id]
                job["status"] = "cancelled"
                job["finished_at"] = datetime.now(timezone.utc).isoformat()
                job["error"] = "Mission cancelled."
            self.record_dashboard_event("dashboard.mission.cancelled", task_id, agent_id, status="cancelled")
            raise
        except Exception as exc:
            with self.job_lock:
                job = self.jobs[task_id]
                job["status"] = "failed"
                job["finished_at"] = datetime.now(timezone.utc).isoformat()
                job["error"] = f"{type(exc).__name__}: {exc}"
            self.record_dashboard_event("dashboard.mission.failed", task_id, agent_id, status="failed")

    @staticmethod
    def _quant_reports_dir() -> Path:
        configured = os.getenv("JARVIS_MARKET_REPORTS_DIR", "").strip()
        return Path(configured).expanduser() if configured else REPORTS

    def quant_dashboard(self) -> dict:
        return read_quant_dashboard_data(self._quant_reports_dir())

    def start_quant_refresh(self) -> dict:
        """Start a free-provider-first Quant refresh without blocking the HTTP request.

        Default/auto mode with no configured broker runs two real Quant tasks:
        official NSE MCP snapshot first, then the historical/technical screen.
        The separate artifacts let the UI present fresh quote metadata and price
        history without pretending daily candles are intraday ticks.
        """
        job_id = str(uuid.uuid4())
        provider_mode = os.getenv("JARVIS_MARKET_DATA_PROVIDER", "auto").strip().lower()
        fyers_configured = bool(os.getenv("FYERS_CLIENT_ID", "").strip() and os.getenv("FYERS_ACCESS_TOKEN", "").strip())
        if provider_mode == "fyers" or (provider_mode == "auto" and fyers_configured):
            planned_stages = [("live_market_scan", "Live market briefing: scan NSE, explain today's moves, chart indices and rank intraday and swing research setups. Do not place orders.")]
            strategy = "configured FYERS quotes and candles"
        elif provider_mode == "yahoo":
            planned_stages = [("historical_research_screen", "Daily Indian stock market brief: scan the monitored Indian large-cap research universe, calculate available historical indicators and price chart series, and rank conditional research setups. Clearly label historical/delayed data. Do not place orders.")]
            strategy = "Yahoo historical/delayed fallback only"
        else:
            planned_stages = [
                ("official_live_snapshot", "Live Indian market snapshot"),
                ("historical_research_screen", "Daily Indian stock market brief: scan the monitored Indian large-cap research universe, calculate available historical indicators and price chart series, and rank conditional research setups. Clearly label historical/delayed data. Do not place orders."),
            ]
            strategy = "NSE MCP free live snapshot plus Yahoo historical/delayed charts; no brokerage credentials required"
        now = datetime.now(timezone.utc).isoformat()
        job = {
            "job_id": job_id, "status": "queued", "provider_mode": provider_mode,
            "strategy": strategy, "created_at": now, "started_at": None,
            "finished_at": None, "stages": [{"name": name, "status": "queued", "agent_id": "trading.quant", "task_id": None}
                                           for name, _ in planned_stages],
            "error": None,
        }
        with self.job_lock:
            self.quant_refresh_jobs[job_id] = job
        self.record_dashboard_event("quant.refresh.queued", job_id, "trading.quant", status="queued")
        future = asyncio.run_coroutine_threadsafe(self._run_quant_refresh_job(job_id, planned_stages), self.loop)
        with self.job_lock:
            self.quant_refresh_futures[job_id] = future
        return dict(job)

    def quant_refresh_status(self, job_id: str) -> dict | None:
        with self.job_lock:
            job = self.quant_refresh_jobs.get(job_id)
            return dict(job) if job else None

    async def _run_quant_refresh_job(self, job_id: str, stages: list[tuple[str, str]]) -> None:
        with self.job_lock:
            job = self.quant_refresh_jobs.get(job_id)
            if not job:
                return
            job["status"] = "running"
            job["started_at"] = datetime.now(timezone.utc).isoformat()
        self.record_dashboard_event("quant.refresh.started", job_id, "trading.quant", status="running")
        succeeded = 0
        failures: list[str] = []
        for index, (stage_name, prompt) in enumerate(stages):
            stage_started = datetime.now(timezone.utc).isoformat()
            stage_task_id = ""
            try:
                request = self._build_task_request(prompt, "trading.quant")
                stage_task_id = request.task_id
                with self.job_lock:
                    current = self.quant_refresh_jobs[job_id]
                    current_stage = current["stages"][index]
                    current_stage.update({"status": "running", "task_id": stage_task_id, "started_at": stage_started})
                self.record_dashboard_event("quant.refresh.stage.started", stage_task_id, "trading.quant", status="running", parent_task_id=job_id)
                result_record = await self._execute_task_request(request)
                output = result_record.get("output", "")
                parsed = None
                if isinstance(output, str):
                    try:
                        parsed = json.loads(output)
                    except json.JSONDecodeError:
                        parsed = None
                provider_error = isinstance(parsed, dict) and bool(parsed.get("error"))
                data_empty = isinstance(parsed, dict) and parsed.get("data_status") == "NO_LIVE_DATA_RETURNED"
                handler_status = str(result_record.get("status", "unknown")).lower()
                stage_status = "succeeded" if handler_status == "succeeded" and not provider_error and not data_empty else "failed"
                detail = str(
                    parsed.get("error") if provider_error else (parsed.get("summary") or result_record.get("summary") or "")
                ) if isinstance(parsed, dict) else str(result_record.get("summary") or "")
                if stage_status == "succeeded":
                    succeeded += 1
                else:
                    failures.append(f"{stage_name}: {detail or 'stage did not return verified data'}")
                stage_update = {
                    "status": stage_status, "task_id": stage_task_id,
                    "handler_status": handler_status, "started_at": stage_started,
                    "finished_at": result_record.get("finished_at") or datetime.now(timezone.utc).isoformat(),
                    "summary": detail[:600],
                    "data_status": parsed.get("data_status") if isinstance(parsed, dict) else None,
                    "report_type": parsed.get("report_type") if isinstance(parsed, dict) else None,
                    "error": (parsed.get("error") if isinstance(parsed, dict) and provider_error else result_record.get("error")),
                }
                with self.job_lock:
                    self.quant_refresh_jobs[job_id]["stages"][index].update(stage_update)
                self.record_dashboard_event("quant.refresh.stage.finished", stage_task_id, "trading.quant", status=stage_status, parent_task_id=job_id)
            except Exception as exc:
                message = f"{type(exc).__name__}: {str(exc)[:240]}"
                failures.append(f"{stage_name}: {message}")
                with self.job_lock:
                    self.quant_refresh_jobs[job_id]["stages"][index].update({
                        "status": "failed", "task_id": stage_task_id or None,
                        "started_at": stage_started, "finished_at": datetime.now(timezone.utc).isoformat(), "error": message,
                    })
                self.record_dashboard_event("quant.refresh.stage.finished", stage_task_id or job_id, "trading.quant", status="failed", parent_task_id=job_id)
        if succeeded == len(stages):
            final_status = "succeeded"
        elif succeeded:
            final_status = "partial"
        else:
            final_status = "failed"
        with self.job_lock:
            job = self.quant_refresh_jobs[job_id]
            job["status"] = final_status
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            job["error"] = "; ".join(failures) if failures else None
        self.record_dashboard_event("quant.refresh.finished", job_id, "trading.quant", status=final_status)

    def stop_event_observer(self) -> None:
        future = getattr(self, "event_observer_future", None)
        if future is not None:
            future.cancel()


APP = None


class Handler(BaseHTTPRequestHandler):
    server_version = "JARVIS-Dashboard/1.0"

    def log_message(self, fmt, *args):
        # Keep the local console useful without logging prompts or response bodies.
        print(f"[dashboard] {self.address_string()} {fmt % args}")

    def _send(self, status: int, payload, content_type: str = "application/json; charset=utf-8"):
        if isinstance(payload, (dict, list)):
            raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        else:
            raw = payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/overview":
            return self._send(200, APP.overview())
        if path == "/api/agents":
            return self._send(200, {"agents": APP.runtime.list_agents()})
        if path == "/api/market":
            return self._send(200, read_market_report())
        if path == "/api/quant/dashboard":
            return self._send(200, APP.quant_dashboard())
        if path == "/api/quant/stock-search":
            query = parse_qs(urlparse(self.path).query)
            try:
                result = APP.stock_lookup.search(
                    query.get("q", [""])[0],
                    region=query.get("region", ["IN"])[0],
                    limit=int(query.get("limit", ["12"])[0]),
                )
                return self._send(200, result)
            except ValueError as exc:
                return self._send(400, {"ok": False, "error": str(exc)})
            except StockLookupRateLimit as exc:
                return self._send(429, {"ok": False, "error": str(exc)})
            except StockLookupError as exc:
                return self._send(502, {"ok": False, "error": str(exc)})
        if path == "/api/quant/stock-quote":
            query = parse_qs(urlparse(self.path).query)
            try:
                result = APP.stock_lookup.quote(
                    query.get("symbol", [""])[0],
                    chart_range=query.get("range", ["3mo"])[0],
                )
                return self._send(200, result)
            except ValueError as exc:
                return self._send(400, {"ok": False, "error": str(exc)})
            except StockLookupRateLimit as exc:
                return self._send(429, {"ok": False, "error": str(exc)})
            except StockLookupError as exc:
                return self._send(502, {"ok": False, "error": str(exc)})
        if path.startswith("/api/quant/refresh/"):
            job_id = path.rsplit("/", 1)[-1]
            if not job_id or len(job_id) > 100:
                return self._send(400, {"error": "Invalid Quant refresh job id."})
            job = APP.quant_refresh_status(job_id)
            if job is None:
                return self._send(404, {"error": "Quant refresh job not found in this dashboard session."})
            return self._send(200, {"job": job})
        if path == "/api/tasks":
            return self._send(200, {"history": APP.overview()["task_history"], "recent_results": APP._recent_result_snapshot(30)})
        if path == "/api/events":
            query = parse_qs(urlparse(self.path).query)
            try:
                after = max(0, int(query.get("after", ["0"])[0]))
            except (ValueError, TypeError):
                return self._send(400, {"error": "The after cursor must be an integer."})
            return self._send(200, {**APP.events_since(after), "active_missions": APP.active_missions()})
        if path.startswith("/api/missions/"):
            task_id = path.rsplit("/", 1)[-1]
            if not task_id or len(task_id) > 100 or "/" in task_id:
                return self._send(400, {"error": "Invalid mission id."})
            job = APP.mission_status(task_id)
            if job is None:
                return self._send(404, {"error": "Mission not found in this dashboard session."})
            return self._send(200, {"job": job})
        if path == "/api/reports":
            reports = []
            if REPORTS.exists():
                for item in sorted(REPORTS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                    if item.is_file() and item.suffix.lower() in {".md", ".json", ".csv", ".html"}:
                        stat = item.stat()
                        reports.append({"name": item.name, "path": item.name, "size": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(), "kind": item.suffix.lower()[1:]})
            return self._send(200, {"reports": reports})
        if path.startswith("/api/report/"):
            name = path.rsplit("/", 1)[-1]
            if not name or Path(name).name != name or name.startswith("."):
                return self._send(400, {"error": "Invalid report name."})
            target = REPORTS / name
            if not target.is_file() or target.suffix.lower() not in {".md", ".json", ".csv", ".html"}:
                return self._send(404, {"error": "Report not found."})
            if target.stat().st_size > 2_000_000:
                return self._send(413, {"error": "Report exceeds the 2 MB viewer limit."})
            mime = {".md": "text/plain; charset=utf-8", ".json": "application/json; charset=utf-8", ".csv": "text/csv; charset=utf-8", ".html": "text/plain; charset=utf-8"}.get(target.suffix.lower(), "text/plain; charset=utf-8")
            return self._send(200, target.read_text(encoding="utf-8", errors="replace"), mime)
        if path in {"/quant", "/quant/", "/quant/index.html"}:
            target = WEB_ROOT / "quant.html"
        elif path == "/" or path == "/index.html":
            target = WEB_ROOT / "index.html"
        elif path.startswith("/assets/"):
            target = WEB_ROOT / path.lstrip("/")
        else:
            return self._send(404, {"error": "Not found."})
        try:
            target = target.resolve()
            if not target.is_relative_to(WEB_ROOT.resolve()) or not target.is_file():
                return self._send(404, {"error": "Not found."})
            mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if mime.startswith("text/") or mime in {"application/javascript", "application/json"}:
                mime += "; charset=utf-8"
            return self._send(200, target.read_bytes(), mime)
        except OSError:
            return self._send(404, {"error": "Not found."})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {"/api/tasks", "/api/missions", "/api/quant/refresh"}:
            return self._send(404, {"error": "Not found."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY:
                return self._send(413, {"error": "Request body must be between 1 byte and 16 KB."})
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
            if path == "/api/quant/refresh":
                job = APP.start_quant_refresh()
                return self._send(202, {"ok": True, "job_id": job["job_id"], "job": job})
            if path == "/api/missions":
                job = APP.start_mission(str(body.get("prompt", "")), body.get("agent_id"))
                return self._send(202, {"ok": True, "task_id": job["task_id"], "job": job})
            result = APP.submit_task(str(body.get("prompt", "")), body.get("agent_id"))
            return self._send(200, {"ok": result["status"] == "succeeded", "result": result})
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        except TimeoutError as exc:
            return self._send(504, {"error": str(exc)})
        except Exception as exc:
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    global APP
    APP = DashboardApplication()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("\n  JARVIS INTELLIGENCE // LOCAL DASHBOARD")
    print(f"  UI:      http://{HOST}:{PORT}")
    print(f"  API:     http://{HOST}:{PORT}/api/overview")
    print("  Safety:  loopback only · no trading/order endpoints · Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nJARVIS dashboard stopped.")
    finally:
        server.server_close()
        APP.stop_event_observer()
        APP.loop.call_soon_threadsafe(APP.loop.stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
