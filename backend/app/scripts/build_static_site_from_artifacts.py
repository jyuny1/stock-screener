"""Build US static-site data directly from release artifacts without Postgres.

This exporter is intentionally artifact-native: it reads the published weekly
reference bundle plus an optional daily-price bundle and emits the JSON files
consumed by ``frontend/src/static``. It does not import into Postgres and does
not call external market-data providers.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.preset_screens import PRESET_SCREENS

STATIC_SITE_SCHEMA_VERSION = "static-site-v2"
SCAN_BUNDLE_SCHEMA_VERSION = "static-scan-v1"
SCAN_CHUNK_SIZE = 1000
DEFAULT_MARKET = "US"
DEFAULT_MARKET_DISPLAY = "United States"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _number(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return number


def _latest_daily_by_symbol(daily_bundle: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not daily_bundle:
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for row in daily_bundle.get("rows") or []:
        symbol = str(row.get("symbol") or "").upper().strip()
        prices = row.get("prices") or []
        if not symbol or not prices:
            continue
        last = prices[-1]
        prev = prices[-2] if len(prices) >= 2 else None
        close = _number(last.get("close"))
        prev_close = _number(prev.get("close")) if prev else None
        change_1d = None
        if close is not None and prev_close not in (None, 0):
            change_1d = round(((float(close) - float(prev_close)) / float(prev_close)) * 100.0, 4)
        latest[symbol] = {
            "date": last.get("date"),
            "close": close,
            "volume": _number(last.get("volume")),
            "change_1d": change_1d,
            "prices": prices,
        }
    return latest


def _weekly_rows(weekly_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = weekly_bundle.get("snapshot") or {}
    rows = snapshot.get("rows") if isinstance(snapshot, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Weekly reference bundle snapshot.rows must be a list")
    return rows


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("normalized_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    symbol = str(row.get("symbol") or payload.get("symbol") or "").upper().strip()
    return {**payload, "symbol": symbol, "exchange": row.get("exchange") or payload.get("exchange")}


def _composite_score(payload: dict[str, Any]) -> float | None:
    parts = [
        _number(payload.get("eps_rating")),
        _number(payload.get("perf_quarter")),
        _number(payload.get("perf_half_year")),
        _number(payload.get("relative_volume")),
    ]
    usable = [float(value) for value in parts if value is not None]
    if not usable:
        return None
    return round(sum(usable) / len(usable), 2)


def _scan_row(payload: dict[str, Any], latest_price: dict[str, Any] | None) -> dict[str, Any]:
    symbol = payload["symbol"]
    volume = (latest_price or {}).get("volume") or _number(payload.get("avg_volume"))
    current_price = (latest_price or {}).get("close")
    change_1d = (latest_price or {}).get("change_1d")
    market_cap = _number(payload.get("market_cap"))
    currency = payload.get("currency") or "USD"
    composite = _composite_score(payload)
    eps_rating = _number(payload.get("eps_rating"))
    row = {
        "symbol": symbol,
        "company_name": payload.get("company_name") or payload.get("name") or symbol,
        "market": payload.get("market") or DEFAULT_MARKET,
        "exchange": payload.get("exchange"),
        "currency": currency,
        "current_price": current_price,
        "price_change_1d": change_1d,
        "volume": volume,
        "avg_volume": _number(payload.get("avg_volume")),
        "market_cap": market_cap,
        "market_cap_usd": _number(payload.get("market_cap_usd")) or market_cap,
        "gics_sector": payload.get("sector"),
        "sector": payload.get("sector"),
        "industry": payload.get("industry"),
        "ibd_industry_group": payload.get("ibd_industry_group") or payload.get("industry"),
        "ipo_date": payload.get("ipo_date") or payload.get("first_trade_date"),
        "rating": payload.get("recommendation") or "Insufficient Data",
        "scan_mode": "artifact_reference",
        "composite_score": composite,
        "minervini_score": None,
        "canslim_score": None,
        "ipo_score": None,
        "custom_score": None,
        "volume_breakthrough_score": None,
        "se_setup_score": None,
        "rs_rating": eps_rating,
        "rs_rating_1m": None,
        "rs_rating_3m": None,
        "rs_rating_12m": None,
        "eps_rating": eps_rating,
        "eps_growth_qq": _number(payload.get("eps_growth_qq")),
        "sales_growth_qq": _number(payload.get("sales_growth_qq")),
        "adr_percent": None,
        "beta": _number(payload.get("beta")),
        "beta_adj_rs": None,
        "vcp_score": None,
        "vcp_pivot": None,
        "stage": None,
        "ma_alignment": None,
        "passes_template": None,
        "pocket_pivot": None,
        "power_trend": None,
        "vcp_detected": None,
        "vcp_ready_for_breakout": None,
        "se_setup_ready": None,
        "se_rs_line_new_high": None,
        "se_pattern_primary": None,
        "se_distance_to_pivot_pct": None,
        "se_bb_width_pctile_252": None,
        "se_volume_vs_50d": None,
        "se_up_down_volume_ratio_10d": None,
        "perf_week": _number(payload.get("perf_week")),
        "perf_month": _number(payload.get("perf_month")),
        "perf_3m": _number(payload.get("perf_quarter")),
        "perf_6m": _number(payload.get("perf_half_year")),
        "gap_percent": None,
        "volume_surge": _number(payload.get("relative_volume")),
        "ema_10_distance": None,
        "ema_20_distance": None,
        "ema_50_distance": None,
        "week_52_high_distance": _number(payload.get("week_52_high_distance")),
        "week_52_low_distance": _number(payload.get("week_52_low_distance")),
        "pct_day": None,
        "pct_week": None,
        "pct_month": None,
        "sparkline": [],
        "rs_sparkline": [],
    }
    return row


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("composite_score") is None,
            -(float(row.get("composite_score") or 0)),
            row.get("symbol") or "",
        ),
    )


def _filter_options(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    def unique(field: str) -> list[str]:
        return sorted({str(row[field]) for row in rows if row.get(field)})

    return {
        "ibd_industries": unique("ibd_industry_group"),
        "gics_sectors": unique("gics_sector"),
        "ratings": unique("rating"),
    }


def _build_scan(output_dir: Path, *, generated_at: str, as_of_date: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    scan_dir = output_dir / "markets" / "us" / "scan"
    chunks_dir = scan_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunk_refs = []
    for index in range(0, len(rows), SCAN_CHUNK_SIZE):
        chunk = rows[index:index + SCAN_CHUNK_SIZE]
        chunk_num = index // SCAN_CHUNK_SIZE + 1
        rel = Path("markets/us/scan/chunks") / f"chunk-{chunk_num:04d}.json"
        _write_json(output_dir / rel, {
            "schema_version": SCAN_BUNDLE_SCHEMA_VERSION,
            "generated_at": generated_at,
            "as_of_date": as_of_date,
            "run_id": "artifact-native-us",
            "chunk_index": chunk_num,
            "rows": chunk,
        })
        chunk_refs.append({"path": rel.as_posix(), "count": len(chunk)})

    default_filters = {"minVolume": None}
    manifest = {
        "schema_version": SCAN_BUNDLE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "run_id": "artifact-native-us",
        "sort": {"field": "composite_score", "order": "desc"},
        "default_page_size": 50,
        "chunk_size": SCAN_CHUNK_SIZE,
        "rows_total": len(rows),
        "default_filters": default_filters,
        "default_filtered_rows_total": len(rows),
        "filter_options": _filter_options(rows),
        "preset_screens": PRESET_SCREENS,
        "chunks": chunk_refs,
        "initial_rows": rows[:50],
        "preview_rows": rows[:10],
        "charts": {"path": "markets/us/charts/manifest.json", "limit": 0, "symbols_total": 0, "available": False},
    }
    _write_json(scan_dir / "manifest.json", manifest)
    _write_json(output_dir / "markets/us/charts/manifest.json", {
        "schema_version": "static-chart-index-v1",
        "generated_at": generated_at,
        "period": "6mo",
        "available": False,
        "symbols": [],
    })
    return manifest


def _build_home(*, generated_at: str, as_of_date: str, rows: list[dict[str, Any]], coverage: dict[str, Any]) -> dict[str, Any]:
    top_groups = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        group = row.get("ibd_industry_group") or row.get("industry") or "No Group"
        groups.setdefault(group, []).append(row)
    for group, group_rows in groups.items():
        top_groups.append({
            "industry_group": group,
            "stock_count": len(group_rows),
            "avg_composite_score": round(
                sum(float(r.get("composite_score") or 0) for r in group_rows) / max(len(group_rows), 1), 2
            ),
            "top_symbol": group_rows[0].get("symbol"),
        })
    top_groups.sort(key=lambda item: (-item["avg_composite_score"], item["industry_group"]))
    return {
        "schema_version": STATIC_SITE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "market": DEFAULT_MARKET,
        "market_display_name": DEFAULT_MARKET_DISPLAY,
        "as_of_date": as_of_date,
        "freshness": {
            "scan_as_of_date": as_of_date,
            "breadth_latest_date": None,
            "groups_latest_date": as_of_date,
            "weekly_reference_source_revision": coverage.get("source_revision"),
        },
        "coverage": coverage,
        "key_markets": [],
        "top_groups": top_groups[:10],
    }


def build_static_site_from_artifacts(
    *,
    weekly_reference: Path,
    output_dir: Path,
    daily_price: Path | None = None,
) -> dict[str, Any]:
    generated_at = _utc_now()
    weekly = _read_json(weekly_reference)
    daily = _read_json(daily_price) if daily_price else None
    latest_prices = _latest_daily_by_symbol(daily)
    as_of_date = str(weekly.get("as_of_date") or datetime.now(timezone.utc).date().isoformat())
    coverage = dict((weekly.get("coverage") or {}))
    coverage["source_revision"] = weekly.get("source_revision")

    rows = []
    for source_row in _weekly_rows(weekly):
        payload = _row_payload(source_row)
        if not payload.get("symbol"):
            continue
        rows.append(_scan_row(payload, latest_prices.get(payload["symbol"])))
    rows = _sort_rows(rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    scan_manifest = _build_scan(output_dir, generated_at=generated_at, as_of_date=as_of_date, rows=rows)

    breadth_payload = {
        "available": False,
        "message": f"Breadth data is not available in artifact-native static export for {DEFAULT_MARKET}.",
        "generated_at": generated_at,
        "payload": {},
    }
    groups_payload = {
        "available": False,
        "message": f"Group rankings are not available in artifact-native static export for {DEFAULT_MARKET}.",
        "generated_at": generated_at,
        "payload": {},
    }
    home_payload = _build_home(generated_at=generated_at, as_of_date=as_of_date, rows=rows, coverage=coverage)
    _write_json(output_dir / "markets/us/home.json", home_payload)
    _write_json(output_dir / "markets/us/breadth.json", breadth_payload)
    _write_json(output_dir / "markets/us/groups.json", groups_payload)

    market_entry = {
        "market": DEFAULT_MARKET,
        "display_name": DEFAULT_MARKET_DISPLAY,
        "as_of_date": as_of_date,
        "features": {"scan": True, "breadth": False, "groups": False, "charts": False},
        "pages": {
            "home": {"path": "markets/us/home.json"},
            "scan": {"path": "markets/us/scan/manifest.json"},
            "breadth": {"path": "markets/us/breadth.json"},
            "groups": {"path": "markets/us/groups.json"},
        },
        "assets": {"charts": {"path": "markets/us/charts/manifest.json", "limit": 0, "symbols_total": 0}},
        "freshness": home_payload["freshness"],
    }
    manifest = {
        "schema_version": STATIC_SITE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "default_market": DEFAULT_MARKET,
        "supported_markets": [DEFAULT_MARKET],
        "features": dict(market_entry["features"]),
        "pages": dict(market_entry["pages"]),
        "assets": dict(market_entry["assets"]),
        "markets": {DEFAULT_MARKET: market_entry},
        "warnings": [
            "Artifact-native export does not require Postgres; breadth, group rankings, and chart payloads are disabled until artifact-native inputs are available."
        ],
    }
    _write_json(output_dir / "manifest.json", manifest)
    _write_json(output_dir / "markets/us/manifest.market.json", {
        "schema_version": STATIC_SITE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "market": DEFAULT_MARKET,
        "entry": market_entry,
        "warnings": manifest["warnings"],
    })
    return {"rows_total": len(rows), "scan_manifest": scan_manifest, "manifest": manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weekly-reference", required=True, type=Path)
    parser.add_argument("--daily-price", type=Path, default=None)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    summary = build_static_site_from_artifacts(
        weekly_reference=args.weekly_reference,
        daily_price=args.daily_price,
        output_dir=args.output_dir,
    )
    print(json.dumps({"rows_total": summary["rows_total"], "output_dir": str(args.output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
