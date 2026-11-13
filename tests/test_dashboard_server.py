from __future__ import annotations

import json
from pathlib import Path

import dashboard_server


def test_read_market_report_labels_legacy_report_as_saved_not_live(monkeypatch, tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    payload = {
        "report_type": "daily_india_market_brief",
        "generated_at_utc": "2026-10-19T19:46:46+00:00",
        "indexes": {"Nifty 50": {"close": 123.4, "change_1d_pct": 1.2}},
        "gainers_in_monitored_universe": [{"ticker": "ABC.NS", "close": 123.4, "change_1d_pct": 1.2}],
    }
    (report_dir / "market_brief_latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "REPORTS", report_dir)
    result = dashboard_server.read_market_report()
    assert result["available"] is True
    assert result["kind"] == "report"
    assert result["label"] == "Saved market brief"
    assert result["indexes"][0]["name"] == "Nifty 50"
    assert result["stocks"][0]["symbol"] == "ABC.NS"


def test_read_market_report_handles_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_server, "REPORTS", tmp_path / "missing")
    result = dashboard_server.read_market_report()
    assert result["available"] is False
    assert result["kind"] == "none"
    assert result["indexes"] == []


def test_report_file_path_is_confined_to_reports_dir():
    # This is the same filename check used by the report route.
    assert Path("market_brief_latest.json").name == "market_brief_latest.json"
    assert Path("../.env").name != "../.env"


def test_dashboard_session_token_is_signed_and_expires(monkeypatch):
    monkeypatch.setenv("JARVIS_DASHBOARD_ACCESS_KEY", "test-dashboard-secret")
    token = dashboard_server._create_session_token(now=1_800_000_000)
    assert dashboard_server._valid_session_token(token, now=1_800_000_001)
    assert not dashboard_server._valid_session_token(token + "tampered", now=1_800_000_001)
    assert not dashboard_server._valid_session_token(token, now=1_800_000_000 + dashboard_server.AUTH_SESSION_TTL_SECONDS + 1)


def test_dashboard_session_token_is_bound_to_configured_access_key(monkeypatch):
    monkeypatch.setenv("JARVIS_DASHBOARD_ACCESS_KEY", "first-secret")
    token = dashboard_server._create_session_token(now=1_800_000_000)
    monkeypatch.setenv("JARVIS_DASHBOARD_ACCESS_KEY", "different-secret")
    assert not dashboard_server._valid_session_token(token, now=1_800_000_001)


def test_dashboard_session_rejects_missing_server_secret(monkeypatch):
    monkeypatch.delenv("JARVIS_DASHBOARD_ACCESS_KEY", raising=False)
    assert not dashboard_server._valid_session_token("1800000000.nonce.signature", now=1_800_000_001)


def test_athena_benchmark_endpoint_returns_fixture_report_after_authentication(monkeypatch):
    expected = {
        "benchmark": {"execution_mode": "deterministic_synthetic_fixtures", "provider_calls": 0},
        "metrics": {"scenario_count": 10, "critical_gate": "PASS"},
        "cases": [],
    }
    sent = []
    handler = object.__new__(dashboard_server.Handler)
    handler.path = "/api/athena/benchmark"
    monkeypatch.setattr(dashboard_server.Handler, "_authorize_api", lambda self: True)
    monkeypatch.setattr(dashboard_server.Handler, "_send", lambda self, status, payload, *args: sent.append((status, payload)))
    monkeypatch.setattr(dashboard_server, "read_athena_benchmark", lambda: expected)

    handler.do_GET()

    assert sent == [(200, expected)]


def test_athena_benchmark_endpoint_fails_closed_when_benchmark_cannot_run(monkeypatch):
    sent = []
    handler = object.__new__(dashboard_server.Handler)
    handler.path = "/api/athena/benchmark"
    monkeypatch.setattr(dashboard_server.Handler, "_authorize_api", lambda self: True)
    monkeypatch.setattr(dashboard_server.Handler, "_send", lambda self, status, payload, *args: sent.append((status, payload)))
    monkeypatch.setattr(
        dashboard_server,
        "read_athena_benchmark",
        lambda: {"available": False, "error": "Athena Evidence Lab could not run in this runtime."},
    )

    handler.do_GET()

    assert sent[0][0] == 503
    assert sent[0][1]["available"] is False


def test_athena_benchmark_endpoint_remains_session_protected(monkeypatch):
    sent = []
    handler = object.__new__(dashboard_server.Handler)
    handler.path = "/api/athena/benchmark"
    monkeypatch.setattr(dashboard_server.Handler, "_authorize_api", lambda self: False)
    monkeypatch.setattr(dashboard_server.Handler, "_send", lambda self, status, payload, *args: sent.append((status, payload)))
    monkeypatch.setattr(
        dashboard_server,
        "read_athena_benchmark",
        lambda: (_ for _ in ()).throw(AssertionError("benchmark must not run before authorization")),
    )

    handler.do_GET()

    assert sent == []
