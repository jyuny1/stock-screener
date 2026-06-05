from __future__ import annotations

import gzip
import json
from pathlib import Path

import app.scripts.build_daily_price_artifact as script


def test_build_daily_price_artifact_from_weekly_symbols(monkeypatch, tmp_path: Path):
    weekly = {
        "market": "US",
        "coverage": {"universe_mode": "US_OPTIONABLE"},
        "snapshot": {
            "rows": [
                {"symbol": "AAPL", "exchange": "XNAS", "normalized_payload": {"symbol": "AAPL"}},
                {"symbol": "SPY", "exchange": "ARCX", "normalized_payload": {"symbol": "SPY"}},
            ]
        },
    }
    weekly_path = tmp_path / "weekly.json.gz"
    with gzip.open(weekly_path, "wt", encoding="utf-8") as handle:
        json.dump(weekly, handle)

    def fake_download(symbols, *, period):
        return {
            symbol: [
                {
                    "date": "2026-06-04",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100.5,
                    "adj_close": 100.5,
                    "volume": 123456,
                }
            ]
            for symbol in symbols
        }

    monkeypatch.setattr(script, "_download_batch", fake_download)

    summary = script.build_daily_price_artifact(
        foundation_update=weekly_path,
        output_dir=tmp_path / "out",
        batch_size=2,
        batch_sleep_seconds=0,
        min_symbol_coverage=1.0,
    )

    manifest = json.loads((tmp_path / "out" / "daily-price-latest-us.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "daily-price-manifest-v1"
    assert manifest["symbol_count"] == 2
    assert manifest["symbol_coverage"] == 1.0
    assert summary["symbol_count"] == 2

    bundle_path = tmp_path / "out" / manifest["bundle_asset_name"]
    with gzip.open(bundle_path, "rt", encoding="utf-8") as handle:
        bundle = json.load(handle)
    assert bundle["schema_version"] == "daily-price-bundle-v1"
    assert [row["symbol"] for row in bundle["rows"]] == ["AAPL", "SPY"]
    assert bundle["rows"][0]["prices"][0]["close"] == 100.5
