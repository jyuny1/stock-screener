from __future__ import annotations

import gzip
import json
from pathlib import Path

from app.scripts.build_static_site_from_artifacts import (
    _enrich_rows_with_option_pcr,
    _write_option_contract_d1_import_sql,
    build_static_site_from_artifacts,
)


ROOT = Path(__file__).resolve().parents[3]


def test_daily_price_workflow_refreshes_all_symbols_not_only_missing() -> None:
    content = (ROOT / ".github" / "workflows" / "daily-price.yml").read_text()

    assert "--fetch-mode all" in content
    assert "--fetch-mode missing" not in content


def test_static_pipeline_daily_runs_after_market_close_for_option_volume_pcr() -> None:
    content = (ROOT / ".github" / "workflows" / "static-pipeline-daily.yml").read_text()

    assert "cron: '0 22,23 * * 1-5'" in content
    assert 'et_hour" = "18"' in content
    assert 'et_hour" = "19"' in content
    assert 'et_hour" = "08"' not in content
    assert 'et_hour" = "09"' not in content
    assert "option-chain volume PCR" in content
    assert "target is Monday-Friday 18:00 America/New_York" in content


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
    assert "actions: write" in content
    assert "group: schwab-token-refresh" in content
    assert "Share the Schwab token rotation lock" in content
    assert "Refresh Schwab token and persist rotation" in content
    assert "refresh_schwab_oauth_token" in content
    assert "SCHWAB_SECRET_WRITE_TOKEN" in content
    assert "Schwab token rotation failed; static site will continue" in content


def test_option_pcr_enrichment_skips_when_schwab_refresh_fails(monkeypatch) -> None:
    rows = [{"symbol": "AAPL"}, {"symbol": "MSFT", "option_pcr_volume_14_28dte": 0.8}]

    monkeypatch.setenv("SCHWAB_CLIENT_ID", "client")
    monkeypatch.setenv("SCHWAB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SCHWAB_REFRESH_TOKEN", "expired")
    monkeypatch.delenv("SCHWAB_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        "app.scripts.build_static_site_from_artifacts._refresh_schwab_access_token",
        lambda: (_ for _ in ()).throw(RuntimeError("HTTP Error 400: Bad Request")),
    )

    assert _enrich_rows_with_option_pcr(rows, tracked_symbols=["AAPL", "MSFT"]) == 0
    assert rows[0]["option_pcr_volume_14_28dte_error"].startswith("Option PCR enrichment skipped")
    assert rows[0]["option_pcr_volume_14_28dte_provider"] == "schwab"
    assert rows[1]["option_pcr_volume_14_28dte"] == 0.8


def test_option_d1_import_uses_option_snapshot_date_not_underlying_reference(tmp_path: Path) -> None:
    rows = [
        {
            "symbol": "AAPL",
            "_option_contracts_14_28dte": [
                {
                    "option_type": "PUT",
                    "contract_symbol": "AAPL 260626P00100000",
                    "expiration_date": "2026-06-26",
                    "strike": 100,
                    "bid": 1.0,
                    "ask": 1.2,
                    "volume": 10,
                    "open_interest": 100,
                    "asof": "2026-06-24T03:35:00+00:00",
                }
            ],
        }
    ]

    _write_option_contract_d1_import_sql(
        rows,
        output_dir=tmp_path,
        generated_at="2026-06-24T03:35:00Z",
        as_of_date="2026-06-24",
        underlying_reference_date="2026-06-23",
    )

    sql = (tmp_path / "markets/us/options/option-contract-liquidity-d1.sql").read_text(encoding="utf-8")
    assert "('as_of_date', '2026-06-24')" in sql
    assert "('underlying_reference_date', '2026-06-23')" in sql
    assert "'2026-06-24', 'AAPL', 'PUT'" in sql
    assert "'2026-06-23', 'AAPL', 'PUT'" not in sql


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
    assert (output_dir / "markets/us/options/option-chain-tracking-pool.json").exists()
    assert (output_dir / "markets/us/options/option-contract-liquidity.sqlite").exists()
    assert (output_dir / "markets/us/options/option-contract-liquidity-d1.sql").exists()
    assert manifest["assets"]["option_chain_tracking_pool"]["max_symbols"] == 500
    assert manifest["assets"]["option_contract_liquidity_sqlite"]["retention_days"] == 90
    assert manifest["assets"]["option_contract_liquidity_d1_import"]["retention_days"] == 90
    assert summary["rows_total"] == 2
