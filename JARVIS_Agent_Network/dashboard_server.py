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
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from core import TaskRequest, create_runtime
from core.env import load_project_env
from core.observability import build_diagnostics

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
    def __init__(self) -> None:
        load_project_env(ROOT)
        self.runtime = create_runtime(project_root=ROOT)
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._run_loop, name="jarvis-dashboard-loop", daemon=True)
        self.loop_thread.start()
        self.started_at = time.time()
        self.task_lock = threading.Lock()
        self.recent_results: list[dict] = []

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

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
            "recent_results": self.recent_results[-10:][::-1],
        }

    def submit_task(self, prompt: str, target_agent_id: str | None = None) -> dict:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Enter a task before running JARVIS.")
        if len(prompt) > 6000:
            raise ValueError("Task is too long (maximum 6,000 characters).")
        target = (target_agent_id or "voice.jarvis").strip().lower()
        if target != "voice.jarvis" and not self.runtime.has_handler(target):
            raise ValueError("That agent has no executable handler. Choose JARVIS or a ready specialist.")
        request = TaskRequest(prompt=prompt, target_agent_id=target, requested_by="dashboard")
        future = asyncio.run_coroutine_threadsafe(self.runtime.run_task(request), self.loop)
        try:
            result = future.result(timeout=360)
        except FutureTimeout:
            future.cancel()
            raise TimeoutError("The task exceeded the dashboard's 6-minute wait limit.")
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
            "prompt": prompt[:500],
        }
        self.recent_results.append(record)
        self.recent_results = self.recent_results[-30:]
        return record


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
        if path == "/api/tasks":
            return self._send(200, {"history": APP.overview()["task_history"], "recent_results": APP.recent_results[-30:][::-1]})
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
        if path == "/" or path == "/index.html":
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
        if path != "/api/tasks":
            return self._send(404, {"error": "Not found."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY:
                return self._send(413, {"error": "Request body must be between 1 byte and 16 KB."})
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
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
        APP.loop.call_soon_threadsafe(APP.loop.stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
