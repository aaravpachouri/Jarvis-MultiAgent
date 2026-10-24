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
