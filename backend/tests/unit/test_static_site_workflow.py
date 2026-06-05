from __future__ import annotations

import gzip
import json
from pathlib import Path

from app.scripts.build_static_site_from_artifacts import build_static_site_from_artifacts


ROOT = Path(__file__).resolve().parents[3]


def test_static_site_workflow_is_us_only_artifact_native_and_uses_rclone() -> None:
    content = (ROOT / ".github" / "workflows" / "static-site.yml").read_text()

    assert "workflow_dispatch" in content
    assert "schedule:" not in content
    assert "services:" not in content
    assert "postgres" not in content.lower()
    assert "pip install -r backend/requirements.txt" not in content
    assert "build_static_site_from_artifacts" in content
    assert "foundation-update-latest-us.json" in content
    assert "US_OPTIONABLE" in content
    assert "rclone sync frontend/public/static-data/" in content
    assert "aws s3 sync" not in content
    assert "pip install awscli" not in content
    assert "cloudflare/pages-action@v1" in content
    assert "deployments: write" in content
    assert "group: static-site-us" in content


def test_artifact_native_static_export_matches_frontend_contract(tmp_path: Path) -> None:
    weekly_bundle = {
        "schema_version": "foundation-update-bundle-v1",
        "market": "US",
        "as_of_date": "2026-06-05",
        "generated_at": "2026-06-05T03:52:57Z",
        "source_revision": "fundamentals_v1_us:optionable:test",
        "coverage": {
            "active_symbols": 2,
            "covered_active_symbols": 2,
            "missing_active_symbols": 0,
            "universe_mode": "US_OPTIONABLE",
        },
        "snapshot": {
            "rows": [
                {
                    "symbol": "AAPL",
                    "exchange": "XNAS",
                    "normalized_payload": {
                        "symbol": "AAPL",
                        "company_name": "Apple Inc.",
                        "market": "US",
                        "exchange": "XNAS",
                        "currency": "USD",
                        "sector": "Technology",
                        "industry": "Consumer Electronics",
                        "market_cap": 3000000000000,
                        "avg_volume": 50000000,
                        "eps_rating": 95,
                        "eps_growth_qq": 25,
                        "sales_growth_qq": 12,
                        "perf_week": 2,
                        "perf_month": 5,
                        "perf_quarter": 10,
                        "perf_half_year": 20,
                        "relative_volume": 1.2,
                    },
                },
                {
                    "symbol": "SPY",
                    "exchange": "ARCX",
                    "normalized_payload": {
                        "symbol": "SPY",
                        "company_name": "SPDR S&P 500 ETF Trust",
                        "market": "US",
                        "exchange": "ARCX",
                        "currency": "USD",
                        "sector": "ETF",
                        "industry": "ETF",
                        "avg_volume": 70000000,
                    },
                },
            ]
        },
    }
    weekly_path = tmp_path / "weekly.json.gz"
    with gzip.open(weekly_path, "wt", encoding="utf-8") as handle:
        json.dump(weekly_bundle, handle)

    output_dir = tmp_path / "static-data"
    summary = build_static_site_from_artifacts(
        foundation_update=weekly_path,
        output_dir=output_dir,
    )

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "static-site-v2"
    assert manifest["default_market"] == "US"
    assert manifest["supported_markets"] == ["US"]
    assert manifest["markets"]["US"]["pages"]["scan"]["path"] == "markets/us/scan/manifest.json"

    scan = json.loads((output_dir / "markets/us/scan/manifest.json").read_text(encoding="utf-8"))
    assert scan["schema_version"] == "static-scan-v1"
    assert scan["rows_total"] == 2
    assert scan["chunks"] == [{"count": 2, "path": "markets/us/scan/chunks/chunk-0001.json"}]
    assert scan["initial_rows"]
    first_row = scan["initial_rows"][0]
    for field in [
        "symbol",
        "company_name",
        "market",
        "composite_score",
        "volume",
        "market_cap",
        "gics_sector",
        "ibd_industry_group",
    ]:
        assert field in first_row

    chunk = json.loads((output_dir / scan["chunks"][0]["path"]).read_text(encoding="utf-8"))
    assert len(chunk["rows"]) == 2
    assert (output_dir / "markets/us/home.json").exists()
    assert (output_dir / "markets/us/breadth.json").exists()
    assert (output_dir / "markets/us/groups.json").exists()
    assert (output_dir / "markets/us/charts/manifest.json").exists()
    assert summary["rows_total"] == 2
