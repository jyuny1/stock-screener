from __future__ import annotations

import gzip
import json
from datetime import date, timedelta
from pathlib import Path

from app.scripts.build_scan_metrics_artifact import build_scan_metrics_artifact


def _prices(symbol: str, start: float, step: float, days: int = 260):
    rows = []
    first_date = date(2025, 6, 1)
    for index in range(days):
        close = start + step * index
        rows.append({
            "date": (first_date + timedelta(days=index)).isoformat(),
            "open": close - 0.5,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1_000_000 + index * 1000,
        })
    return {"symbol": symbol, "exchange": "XNAS", "prices": rows}


def test_build_scan_metrics_artifact_computes_core_static_fields(tmp_path: Path):
    foundation = {
        "market": "US",
        "as_of_date": "2026-06-04",
        "snapshot": {
            "rows": [
                {"symbol": "AAPL", "exchange": "XNAS", "normalized_payload": {"symbol": "AAPL", "eps_growth_qq": 40, "sales_growth_qq": 30, "beta": 1.2}},
                {"symbol": "MSFT", "exchange": "XNAS", "normalized_payload": {"symbol": "MSFT", "eps_growth_qq": 20, "sales_growth_qq": 10, "beta": 1.0}},
                {"symbol": "SPY", "exchange": "ARCX", "normalized_payload": {"symbol": "SPY", "eps_growth_qq": 5, "sales_growth_qq": 5, "beta": 1.0}},
            ]
        },
    }
    daily = {
        "market": "US",
        "as_of_date": "2026-06-04",
        "rows": [_prices("AAPL", 100, 1.0), _prices("MSFT", 100, 0.5), _prices("SPY", 100, 0.25)],
    }
    foundation_path = tmp_path / "foundation.json"
    daily_path = tmp_path / "daily.json.gz"
    foundation_path.write_text(json.dumps(foundation), encoding="utf-8")
    with gzip.open(daily_path, "wt", encoding="utf-8") as handle:
        json.dump(daily, handle)

    summary = build_scan_metrics_artifact(
        foundation_update=foundation_path,
        daily_price=daily_path,
        output_dir=tmp_path / "out",
        min_symbol_coverage=1.0,
    )

    manifest = json.loads((tmp_path / "out" / "scan-metrics-latest-us.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "scan-metrics-manifest-v1"
    assert manifest["symbol_count"] == 3
    assert manifest["symbol_coverage"] == 1.0
    assert manifest["field_coverage"]["rs_rating"] == 1.0
    assert summary["bundle_asset_name"].startswith("scan-metrics-us-")

    with gzip.open(tmp_path / "out" / manifest["bundle_asset_name"], "rt", encoding="utf-8") as handle:
        bundle = json.load(handle)
    row = next(item for item in bundle["rows"] if item["symbol"] == "AAPL")
    assert row["rs_rating"] is not None
    assert row["rs_rating_1m"] is not None
    assert row["minervini_score"] is not None
    assert row["canslim_score"] is not None
    assert row["se_setup_score"] is not None
    assert row["se_pivot_price"] is not None
