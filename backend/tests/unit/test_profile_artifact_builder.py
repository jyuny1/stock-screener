from __future__ import annotations

import gzip
import json
from pathlib import Path

from app.scripts.build_profile_artifact import (
    build_etf_profile_artifact,
    build_group_rank_artifact,
    build_listing_profile_artifact,
)
from app.scripts.build_static_site_from_artifacts import build_static_site_from_artifacts


def _write_gz(path: Path, payload: dict):
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


def test_profile_artifacts_merge_into_static_rows(monkeypatch, tmp_path: Path):
    foundation = {
        "schema_version": "foundation-update-bundle-v1",
        "market": "US",
        "as_of_date": "2026-06-05",
        "source_revision": "test",
        "coverage": {"active_symbols": 3, "covered_active_symbols": 3, "missing_active_symbols": 0, "universe_mode": "US_OPTIONABLE"},
        "snapshot": {
            "rows": [
                {"symbol": "AAPL", "exchange": "XNAS", "normalized_payload": {"symbol": "AAPL", "company_name": "Apple", "industry": "Consumer Electronics", "sector": "Technology", "market_cap": 10}},
                {"symbol": "MSFT", "exchange": "XNAS", "normalized_payload": {"symbol": "MSFT", "company_name": "Microsoft", "industry": "Software", "sector": "Technology", "market_cap": 9}},
                {"symbol": "SPY", "exchange": "ARCX", "normalized_payload": {"symbol": "SPY", "company_name": "SPY ETF", "is_etf": True, "security_type": "ETF"}},
            ]
        },
    }
    metrics = {
        "schema_version": "scan-metrics-bundle-v1",
        "market": "US",
        "rows": [
            {"symbol": "AAPL", "composite_score": 90, "rs_rating": 95, "rating": "A+", "adr_percent": 2.1},
            {"symbol": "MSFT", "composite_score": 80, "rs_rating": 85, "rating": "A", "adr_percent": 1.8},
            {"symbol": "SPY", "composite_score": 70, "rs_rating": 75, "rating": "B", "adr_percent": 1.0},
        ],
    }
    daily = {
        "market": "US",
        "as_of_date": "2026-06-05",
        "rows": [
            {"symbol": "AAPL", "prices": [{"date": "2026-06-05", "close": 100, "volume": 10}]},
            {"symbol": "MSFT", "prices": [{"date": "2026-06-05", "close": 50, "volume": 20}]},
            {"symbol": "SPY", "prices": [{"date": "2026-06-05", "close": 500, "volume": 30}]},
        ],
    }
    foundation_path = tmp_path / "foundation.json.gz"
    metrics_path = tmp_path / "metrics.json.gz"
    daily_path = tmp_path / "daily.json.gz"
    _write_gz(foundation_path, foundation)
    _write_gz(metrics_path, metrics)
    _write_gz(daily_path, daily)

    group = build_group_rank_artifact(foundation_update=foundation_path, scan_metrics=metrics_path, output_dir=tmp_path / "group")

    monkeypatch.setattr("app.scripts.build_profile_artifact._yahoo_info", lambda symbol: {"firstTradeDateEpochUtc": 1000000000, "totalAssets": 123456, "fundFamily": "Issuer"})
    listing = build_listing_profile_artifact(foundation_update=foundation_path, output_dir=tmp_path / "listing")
    etf = build_etf_profile_artifact(foundation_update=foundation_path, output_dir=tmp_path / "etf")

    summary = build_static_site_from_artifacts(
        foundation_update=foundation_path,
        daily_price=daily_path,
        scan_metrics=metrics_path,
        group_rank=Path(group["bundle_path"]),
        listing_profile=Path(listing["bundle_path"]),
        etf_profile=Path(etf["bundle_path"]),
        output_dir=tmp_path / "static",
    )
    rows = []
    scan = summary["scan_manifest"]
    for chunk in scan["chunks"]:
        rows.extend(json.loads((tmp_path / "static" / chunk["path"]).read_text())["rows"])
    spy = next(row for row in rows if row["symbol"] == "SPY")
    aapl = next(row for row in rows if row["symbol"] == "AAPL")
    assert aapl["ibd_group_rank"] is not None
    assert aapl["ipo_date"] == "2001-09-09"
    assert aapl["rating"] == "A+"
    assert aapl["adr_percent"] == 2.1
    assert spy["market_cap"] == 123456
