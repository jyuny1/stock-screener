from __future__ import annotations

import gzip
import json
from pathlib import Path

import app.scripts.build_foundation_update_artifact as script


def test_build_foundation_update_artifact_reuses_prior_and_fetches_missing(monkeypatch, tmp_path: Path):
    optionable = {
        "schema_version": "optionable-symbols-v1",
        "market": "US",
        "source": "optionable_schwab:20260604",
        "symbols": ["AAPL", "MSFT"],
        "symbol_metadata": {
            "AAPL": {"symbol": "AAPL", "mic": "XNAS", "exchange": "NASDAQ", "name": "Apple Inc.", "is_etf": False},
            "MSFT": {"symbol": "MSFT", "mic": "XNAS", "exchange": "NASDAQ", "name": "Microsoft Corporation", "is_etf": False},
        },
    }
    prior = {
        "schema_version": "foundation-update-bundle-v1",
        "market": "US",
        "snapshot": {
            "rows": [
                {
                    "symbol": "AAPL",
                    "exchange": "XNAS",
                    "normalized_payload": {
                        "symbol": "AAPL",
                        "market": "US",
                        "exchange": "XNAS",
                        "company_name": "Apple Inc.",
                        "sector": "Technology",
                        "industry": "Consumer Electronics",
                        "market_cap": 100,
                        "foundation_updated_at": "2099-01-01T00:00:00Z",
                    },
                }
            ]
        },
    }
    optionable_path = tmp_path / "optionable.json"
    prior_path = tmp_path / "prior.json.gz"
    optionable_path.write_text(json.dumps(optionable), encoding="utf-8")
    with gzip.open(prior_path, "wt", encoding="utf-8") as handle:
        json.dump(prior, handle)

    def fake_fetch(symbol, base):
        assert symbol == "MSFT"
        return {
            **base,
            "company_name": "Microsoft Corporation",
            "sector": "Technology",
            "industry": "Software - Infrastructure",
            "market_cap": 200,
            "beta": 1.1,
            "foundation_status": "complete",
            "foundation_updated_at": "2026-06-05T00:00:00Z",
            "field_availability": {"identity": True, "classification": True, "fundamentals": True, "growth": False, "ipo": False},
        }

    monkeypatch.setattr(script, "_fetch_symbol", fake_fetch)
    monkeypatch.setattr(script, "_utc_now", lambda: "2026-06-05T00:00:00Z")
    monkeypatch.setattr(script, "_today", lambda: "2026-06-05")

    summary = script.build_foundation_update_artifact(
        optionable_symbols=optionable_path,
        prior_foundation=prior_path,
        output_dir=tmp_path / "out",
        stale_days=7,
        batch_sleep_seconds=0,
        min_symbol_coverage=1.0,
        min_identity_coverage=1.0,
        min_market_cap_coverage=1.0,
    )

    manifest = json.loads((tmp_path / "out" / "foundation-update-latest-us.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "foundation-update-manifest-v1"
    assert manifest["symbol_count"] == 2
    assert manifest["symbol_coverage"] == 1.0
    assert manifest["fetched_symbol_count"] == 1
    assert summary["bundle_asset_name"] == "foundation-update-us-20260605.json.gz"

    with gzip.open(tmp_path / "out" / manifest["bundle_asset_name"], "rt", encoding="utf-8") as handle:
        bundle = json.load(handle)
    rows = bundle["snapshot"]["rows"]
    assert [row["symbol"] for row in rows] == ["AAPL", "MSFT"]
    assert rows[1]["normalized_payload"]["market_cap"] == 200


def test_legacy_prior_with_provider_fields_is_not_refetched(monkeypatch, tmp_path: Path):
    optionable = {
        "market": "US",
        "source": "optionable_schwab:20260604",
        "symbols": ["AAPL"],
        "symbol_metadata": {
            "AAPL": {"symbol": "AAPL", "mic": "XNAS", "exchange": "NASDAQ", "name": "Apple Inc.", "is_etf": False},
        },
    }
    prior = {
        "schema_version": "weekly-reference-bundle-v1",
        "market": "US",
        "snapshot": {
            "rows": [
                {
                    "symbol": "AAPL",
                    "exchange": "XNAS",
                    "normalized_payload": {
                        "symbol": "AAPL",
                        "market": "US",
                        "exchange": "XNAS",
                        "company_name": "Apple Inc.",
                        "sector": "Technology",
                        "industry": "Consumer Electronics",
                        "market_cap": 100,
                    },
                }
            ]
        },
    }
    optionable_path = tmp_path / "optionable.json"
    prior_path = tmp_path / "prior.json"
    optionable_path.write_text(json.dumps(optionable), encoding="utf-8")
    prior_path.write_text(json.dumps(prior), encoding="utf-8")

    def fail_fetch(symbol, base):
        raise AssertionError("legacy rows with provider fields should be reused during migration")

    monkeypatch.setattr(script, "_fetch_symbol", fail_fetch)
    monkeypatch.setattr(script, "_utc_now", lambda: "2026-06-05T00:00:00Z")
    monkeypatch.setattr(script, "_today", lambda: "2026-06-05")

    summary = script.build_foundation_update_artifact(
        optionable_symbols=optionable_path,
        prior_foundation=prior_path,
        output_dir=tmp_path / "out",
        batch_sleep_seconds=0,
        min_symbol_coverage=1.0,
        min_identity_coverage=1.0,
        min_market_cap_coverage=1.0,
    )

    assert summary["fetched_symbol_count"] == 0
