"""Unit tests for the SOXL D1 import builder."""

import json

import pytest

from app.scripts import build_soxl_price_d1_import as script


def test_append_intraday_daily_bar_when_daily_endpoint_lags():
    daily = [
        {
            "symbol": "SOXL",
            "trading_date": "2026-06-29",
            "open": 250.0,
            "high": 260.0,
            "low": 248.0,
            "close": 255.0,
            "volume": 100,
            "provider": "schwab",
            "created_at": "2026-06-30T23:31:00Z",
        }
    ]
    intraday = [
        {"trading_date": "2026-06-30", "open": 260.0, "high": 262.0, "low": 259.0, "close": 261.0, "volume": 10},
        {"trading_date": "2026-06-30", "open": 261.0, "high": 268.0, "low": 260.0, "close": 266.8, "volume": 20},
    ]

    rows = script._append_intraday_daily_bar_if_needed(  # noqa: SLF001
        daily,
        intraday,
        symbol="SOXL",
        created_at="2026-06-30T23:31:00Z",
    )

    assert len(rows) == 2
    assert rows[-1] == {
        "symbol": "SOXL",
        "trading_date": "2026-06-30",
        "open": 260.0,
        "high": 268.0,
        "low": 259.0,
        "close": 266.8,
        "volume": 30,
        "provider": "schwab_intraday_provisional_daily",
        "created_at": "2026-06-30T23:31:00Z",
    }


def test_compact_support_payloads_remove_duplicate_verbose_sections():
    daily_support = {
        "status": "ok",
        "spot": 266.8,
        "asOf": "2026-06-30",
        "levels": [{"price": 250}],
        "supportLevels": [{"price": 250}],
        "resistanceLevels": [],
        "historicalStructures": [{"price": 10}] * 100,
        "tacticalReferences": [{"price": 20}] * 100,
    }
    intraday_support = {
        "status": "ok",
        "spot": 266.8,
        "asOf": "2026-06-30",
        "zones": [{"price": 250}],
        "debugRows": [{"x": 1}] * 100,
    }
    merged_support = {
        "status": "ok",
        "spot": 266.8,
        "daily": daily_support,
        "intraday": intraday_support,
        "mergedZones": [{"price": 250, "zoneLow": 249, "zoneHigh": 252}],
    }

    compact_daily, compact_intraday, compact_merged = script._compact_support_payloads(  # noqa: SLF001
        daily_support,
        intraday_support,
        merged_support,
    )

    assert compact_daily["supportLevels"] == [{"price": 250}]
    assert "historicalStructures" not in compact_daily
    assert "tacticalReferences" not in compact_daily
    assert compact_intraday["zones"] == [{"price": 250}]
    assert "debugRows" not in compact_intraday
    assert compact_merged == {
        "status": "ok",
        "spot": 266.8,
        "mergedZones": [{"price": 250, "zoneLow": 249, "zoneHigh": 252}],
    }
    assert len(json.dumps(compact_merged)) < len(json.dumps(merged_support))


def test_statement_size_guard_reports_oversized_statement():
    statements = [
        "CREATE TABLE IF NOT EXISTS ok (id INTEGER)",
        "INSERT OR REPLACE INTO soxl_support_snapshots (payload) VALUES ('" + "x" * 50 + "')",
    ]

    with pytest.raises(RuntimeError, match="soxl_support_snapshots"):
        script._validate_statement_sizes(statements, max_bytes=40)  # noqa: SLF001
