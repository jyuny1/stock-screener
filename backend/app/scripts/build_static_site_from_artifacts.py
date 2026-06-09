"""Build US static-site data directly from release artifacts without Postgres.

This exporter is intentionally artifact-native: it reads the published weekly
reference bundle plus an optional daily-price bundle and emits the JSON files
consumed by ``frontend/src/static``. It does not import into Postgres and does
not call external market-data providers.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import math
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from urllib import error, parse, request
from pathlib import Path
from typing import Any

from app.services.preset_screens import PRESET_SCREENS

STATIC_SITE_SCHEMA_VERSION = "static-site-v2"
SCAN_BUNDLE_SCHEMA_VERSION = "static-scan-v1"
SCAN_CHUNK_SIZE = 1000
DEFAULT_MARKET = "US"
DEFAULT_MARKET_DISPLAY = "United States"
SCHWAB_MARKETDATA_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
OPTION_PCR_MIN_DTE = 30
OPTION_PCR_MAX_DTE = 45
OPTION_PCR_MAX_SYMBOLS = 300
OPTION_PCR_REQUEST_INTERVAL_SECONDS = 0.5


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _git_push_hash() -> str | None:
    for name in ("GIT_PUSH", "GITHUB_SHA", "CF_PAGES_COMMIT_SHA", "VERCEL_GIT_COMMIT_SHA"):
        value = os.environ.get(name)
        if value:
            return value
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _has_schwab_auth_material() -> bool:
    if os.environ.get("SCHWAB_ACCESS_TOKEN"):
        return True
    return all(
        os.environ.get(name)
        for name in ("SCHWAB_CLIENT_ID", "SCHWAB_CLIENT_SECRET", "SCHWAB_REFRESH_TOKEN")
    )


def _schwab_access_token() -> str:
    token = os.environ.get("SCHWAB_ACCESS_TOKEN")
    if token:
        return token
    client_id = os.environ["SCHWAB_CLIENT_ID"]
    client_secret = os.environ["SCHWAB_CLIENT_SECRET"]
    refresh_token = os.environ["SCHWAB_REFRESH_TOKEN"]
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    body = parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode("utf-8")
    req = request.Request(
        SCHWAB_TOKEN_URL,
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    access_token = str(payload.get("access_token") or "")
    new_refresh_token = str(payload.get("refresh_token") or "")
    if not access_token:
        raise RuntimeError("Schwab token refresh response did not include access_token")
    os.environ["SCHWAB_ACCESS_TOKEN"] = access_token
    if new_refresh_token:
        os.environ["SCHWAB_REFRESH_TOKEN"] = new_refresh_token
    return access_token


def _flatten_option_contracts(exp_date_map: Any) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    if not isinstance(exp_date_map, dict):
        return contracts
    for strikes in exp_date_map.values():
        if not isinstance(strikes, dict):
            continue
        for contract_list in strikes.values():
            if isinstance(contract_list, list):
                contracts.extend(c for c in contract_list if isinstance(c, dict))
    return contracts


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _fetch_option_pcr(symbol: str, *, access_token: str, today: datetime | None = None) -> dict[str, Any]:
    now = today or datetime.now(timezone.utc)
    from_date = (now.date() + timedelta(days=OPTION_PCR_MIN_DTE)).isoformat()
    to_date = (now.date() + timedelta(days=OPTION_PCR_MAX_DTE)).isoformat()
    query = parse.urlencode({
        "symbol": symbol,
        "contractType": "ALL",
        "strategy": "SINGLE",
        "fromDate": from_date,
        "toDate": to_date,
        "includeUnderlyingQuote": "false",
        "optionType": "ALL",
    })
    req = request.Request(
        f"{SCHWAB_MARKETDATA_BASE_URL}/chains?{query}",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    with request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    puts = _flatten_option_contracts(payload.get("putExpDateMap"))
    calls = _flatten_option_contracts(payload.get("callExpDateMap"))
    put_volume = sum(_int_value(contract.get("totalVolume")) for contract in puts)
    call_volume = sum(_int_value(contract.get("totalVolume")) for contract in calls)
    expirations = {
        str(contract.get("expirationDate"))[:10]
        for contract in [*puts, *calls]
        if contract.get("expirationDate")
    }
    return {
        "option_pcr_volume_30_45dte": (put_volume / call_volume) if call_volume > 0 else None,
        "option_put_volume_30_45dte": put_volume,
        "option_call_volume_30_45dte": call_volume,
        "option_pcr_volume_30_45dte_expirations": len(expirations),
        "option_pcr_volume_30_45dte_contracts": len(puts) + len(calls),
        "option_pcr_volume_30_45dte_min_dte": OPTION_PCR_MIN_DTE,
        "option_pcr_volume_30_45dte_max_dte": OPTION_PCR_MAX_DTE,
        "option_pcr_volume_30_45dte_asof": datetime.now(timezone.utc).isoformat(),
        "option_pcr_volume_30_45dte_provider": "schwab",
    }


def _enrich_rows_with_option_pcr(rows: list[dict[str, Any]]) -> int:
    if not _has_schwab_auth_material():
        print("Option PCR enrichment skipped: missing Schwab auth material")
        return 0
    access_token = _schwab_access_token()
    updated = 0
    top_rows = rows[:OPTION_PCR_MAX_SYMBOLS]
    for index, row in enumerate(top_rows):
        if row.get("option_pcr_volume_30_45dte") is not None:
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        try:
            row.update(_fetch_option_pcr(symbol, access_token=access_token))
            updated += 1
        except error.HTTPError as exc:
            row.update({
                "option_pcr_volume_30_45dte_error": f"HTTP {exc.code}",
                "option_pcr_volume_30_45dte_provider": "schwab",
                "option_pcr_volume_30_45dte_min_dte": OPTION_PCR_MIN_DTE,
                "option_pcr_volume_30_45dte_max_dte": OPTION_PCR_MAX_DTE,
            })
        except Exception as exc:  # noqa: BLE001 - best-effort static enrichment
            row.update({
                "option_pcr_volume_30_45dte_error": str(exc)[:200],
                "option_pcr_volume_30_45dte_provider": "schwab",
                "option_pcr_volume_30_45dte_min_dte": OPTION_PCR_MIN_DTE,
                "option_pcr_volume_30_45dte_max_dte": OPTION_PCR_MAX_DTE,
            })
        if OPTION_PCR_REQUEST_INTERVAL_SECONDS and index < len(top_rows) - 1:
            time.sleep(OPTION_PCR_REQUEST_INTERVAL_SECONDS)
    print(f"Option PCR enrichment complete: updated={updated} rows={len(top_rows)}")
    return updated


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


def _rows_by_symbol(bundle: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not bundle:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in bundle.get("rows") or []:
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol:
            result[symbol] = row
    return result


def _metrics_by_symbol(scan_metrics_bundle: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return _rows_by_symbol(scan_metrics_bundle)


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
        raise ValueError("Foundation update bundle snapshot.rows must be a list")
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


def _sparkline_from_prices(prices: list[dict[str, Any]] | None, *, limit: int = 30) -> list[float]:
    values: list[float] = []
    for row in (prices or [])[-limit:]:
        close = _number(row.get("close"))
        if close is not None:
            values.append(float(close))
    return values


def _trend(values: list[float]) -> int:
    if len(values) < 2 or values[0] == 0:
        return 0
    change = (values[-1] - values[0]) / values[0]
    if change > 0.005:
        return 1
    if change < -0.005:
        return -1
    return 0


def _rs_sparkline(prices: list[dict[str, Any]] | None, benchmark: list[dict[str, Any]] | None, *, limit: int = 30) -> list[float]:
    benchmark_by_date = {
        str(row.get("date")): _number(row.get("close"))
        for row in (benchmark or [])
        if row.get("date") and _number(row.get("close")) not in (None, 0)
    }
    values: list[float] = []
    for row in (prices or [])[-limit:]:
        date_key = str(row.get("date"))
        close = _number(row.get("close"))
        bench_close = benchmark_by_date.get(date_key)
        if close is not None and bench_close not in (None, 0):
            values.append(round(float(close) / float(bench_close), 6))
    return values


def _scan_row(
    payload: dict[str, Any],
    latest_price: dict[str, Any] | None,
    benchmark_prices: list[dict[str, Any]] | None = None,
    scan_metrics: dict[str, Any] | None = None,
    group_rank: dict[str, Any] | None = None,
    listing_profile: dict[str, Any] | None = None,
    etf_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbol = payload["symbol"]
    price_history = (latest_price or {}).get("prices") or []
    price_sparkline = _sparkline_from_prices(price_history)
    rs_sparkline = _rs_sparkline(price_history, benchmark_prices)
    share_volume = (latest_price or {}).get("volume") or _number(payload.get("avg_volume"))
    current_price = (latest_price or {}).get("close")
    dollar_volume = None
    if share_volume is not None and current_price is not None:
        dollar_volume = float(share_volume) * float(current_price)
    change_1d = (latest_price or {}).get("change_1d")
    market_cap = _number(payload.get("market_cap"))
    currency = payload.get("currency") or "USD"
    composite = _composite_score(payload)
    eps_rating = _number(payload.get("eps_rating"))
    metrics = scan_metrics or {}
    group = group_rank or {}
    listing = listing_profile or {}
    etf = etf_profile or {}
    market_cap = market_cap or _number(etf.get("net_assets")) or _number(etf.get("aum"))
    row = {
        "symbol": symbol,
        "company_name": payload.get("company_name") or payload.get("name") or symbol,
        "market": payload.get("market") or DEFAULT_MARKET,
        "exchange": payload.get("exchange"),
        "currency": currency,
        "security_type": payload.get("security_type"),
        "is_etf": bool(payload.get("is_etf") or str(payload.get("security_type") or "").upper() == "ETF"),
        "current_price": current_price,
        "price_change_1d": change_1d,
        "volume": share_volume,
        "dollar_volume": dollar_volume,
        "avg_volume": _number(payload.get("avg_volume")),
        "market_cap": market_cap,
        "market_cap_usd": _number(payload.get("market_cap_usd")) or market_cap,
        "adv_usd": dollar_volume,
        "gics_sector": payload.get("sector"),
        "sector": payload.get("sector"),
        "industry": payload.get("industry"),
        "ibd_industry_group": group.get("ibd_industry_group") or group.get("group_name") or payload.get("ibd_industry_group") or payload.get("industry"),
        "ibd_group_rank": group.get("ibd_group_rank") or group.get("group_rank"),
        "ipo_date": listing.get("ipo_date") or listing.get("listing_date") or payload.get("ipo_date") or payload.get("first_trade_date"),
        "rating": metrics.get("rating") or payload.get("recommendation") or "Insufficient Data",
        "scan_mode": "artifact_reference",
        "composite_score": metrics.get("composite_score", composite),
        "minervini_score": metrics.get("minervini_score"),
        "canslim_score": metrics.get("canslim_score"),
        "ipo_score": metrics.get("ipo_score"),
        "custom_score": metrics.get("custom_score"),
        "volume_breakthrough_score": metrics.get("volume_breakthrough_score"),
        "se_setup_score": metrics.get("se_setup_score"),
        "rs_rating": metrics.get("rs_rating", eps_rating),
        "rs_rating_1m": metrics.get("rs_rating_1m"),
        "rs_rating_3m": metrics.get("rs_rating_3m"),
        "rs_rating_12m": metrics.get("rs_rating_12m"),
        "eps_rating": metrics.get("eps_rating", eps_rating),
        "eps_growth_qq": _number(payload.get("eps_growth_qq")),
        "sales_growth_qq": _number(payload.get("sales_growth_qq")),
        "adr_percent": metrics.get("adr_percent"),
        "beta": metrics.get("beta", _number(payload.get("beta"))),
        "beta_adj_rs": metrics.get("beta_adj_rs"),
        "vcp_score": metrics.get("vcp_score"),
        "vcp_pivot": metrics.get("vcp_pivot"),
        "stage": metrics.get("stage"),
        "ma_alignment": metrics.get("ma_alignment"),
        "passes_template": metrics.get("passes_template"),
        "pocket_pivot": None,
        "power_trend": None,
        "vcp_detected": metrics.get("vcp_detected"),
        "vcp_ready_for_breakout": metrics.get("vcp_ready_for_breakout"),
        "se_setup_ready": metrics.get("se_setup_ready"),
        "se_rs_line_new_high": metrics.get("se_rs_line_new_high"),
        "se_pattern_primary": metrics.get("se_pattern_primary"),
        "se_distance_to_pivot_pct": metrics.get("se_distance_to_pivot_pct"),
        "se_bb_width_pctile_252": metrics.get("se_bb_width_pctile_252"),
        "se_volume_vs_50d": metrics.get("se_volume_vs_50d"),
        "se_pivot_price": metrics.get("se_pivot_price"),
        "se_up_down_volume_ratio_10d": metrics.get("se_up_down_volume_ratio_10d"),
        "perf_week": _number(payload.get("perf_week")),
        "perf_month": _number(payload.get("perf_month")),
        "perf_3m": _number(payload.get("perf_quarter")),
        "perf_6m": _number(payload.get("perf_half_year")),
        "gap_percent": metrics.get("gap_percent"),
        "volume_surge": metrics.get("volume_surge", _number(payload.get("relative_volume"))),
        "ema_10_distance": metrics.get("ema_10_distance"),
        "ema_20_distance": metrics.get("ema_20_distance"),
        "ema_50_distance": metrics.get("ema_50_distance"),
        "week_52_high_distance": _number(payload.get("week_52_high_distance")),
        "week_52_low_distance": _number(payload.get("week_52_low_distance")),
        "pct_day": metrics.get("pct_day"),
        "pct_week": metrics.get("pct_week"),
        "pct_month": metrics.get("pct_month"),
        "sparkline": price_sparkline,
        "price_sparkline_data": price_sparkline,
        "price_trend": _trend(price_sparkline),
        "rs_sparkline": rs_sparkline,
        "rs_sparkline_data": rs_sparkline,
        "rs_trend": metrics.get("rs_trend", _trend(rs_sparkline)),
        "option_pcr_volume_30_45dte": metrics.get("option_pcr_volume_30_45dte"),
        "option_put_volume_30_45dte": metrics.get("option_put_volume_30_45dte"),
        "option_call_volume_30_45dte": metrics.get("option_call_volume_30_45dte"),
        "option_pcr_volume_30_45dte_expirations": metrics.get("option_pcr_volume_30_45dte_expirations"),
        "option_pcr_volume_30_45dte_contracts": metrics.get("option_pcr_volume_30_45dte_contracts"),
        "option_pcr_volume_30_45dte_min_dte": metrics.get("option_pcr_volume_30_45dte_min_dte"),
        "option_pcr_volume_30_45dte_max_dte": metrics.get("option_pcr_volume_30_45dte_max_dte"),
        "option_pcr_volume_30_45dte_asof": metrics.get("option_pcr_volume_30_45dte_asof"),
        "option_pcr_volume_30_45dte_provider": metrics.get("option_pcr_volume_30_45dte_provider"),
        "option_pcr_volume_30_45dte_error": metrics.get("option_pcr_volume_30_45dte_error"),
    }
    return row


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("adv_usd") is None,
            -(float(row.get("adv_usd") or 0)),
            row.get("rs_rating") is None,
            -(float(row.get("rs_rating") or 0)),
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


def _build_scan(
    output_dir: Path,
    *,
    generated_at: str,
    as_of_date: str,
    universe_as_of_date: str,
    price_as_of_date: str | None,
    scan_as_of_date: str | None,
    universe_updated_at: str | None,
    price_updated_at: str | None,
    scan_updated_at: str | None,
    git_push_hash: str | None,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
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
            "universe_as_of_date": universe_as_of_date,
            "price_as_of_date": price_as_of_date,
            "scan_as_of_date": scan_as_of_date,
            "universe_updated_at": universe_updated_at,
            "price_updated_at": price_updated_at,
            "scan_updated_at": scan_updated_at,
            "git_push_hash": git_push_hash,
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
        "universe_as_of_date": universe_as_of_date,
        "price_as_of_date": price_as_of_date,
        "scan_as_of_date": scan_as_of_date,
        "universe_updated_at": universe_updated_at,
        "price_updated_at": price_updated_at,
        "scan_updated_at": scan_updated_at,
        "git_push_hash": git_push_hash,
        "run_id": "artifact-native-us",
        "sort": {"field": "adv_rs", "order": "desc"},
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


def _build_home(
    *,
    generated_at: str,
    as_of_date: str,
    universe_as_of_date: str,
    price_as_of_date: str | None,
    scan_as_of_date: str | None,
    universe_updated_at: str | None,
    price_updated_at: str | None,
    scan_updated_at: str | None,
    git_push_hash: str | None,
    rows: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
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
        "universe_as_of_date": universe_as_of_date,
        "price_as_of_date": price_as_of_date,
        "scan_as_of_date": scan_as_of_date,
        "universe_updated_at": universe_updated_at,
        "price_updated_at": price_updated_at,
        "scan_updated_at": scan_updated_at,
        "git_push_hash": git_push_hash,
        "freshness": {
            "universe_as_of_date": universe_as_of_date,
            "price_as_of_date": price_as_of_date,
            "scan_as_of_date": scan_as_of_date,
            "universe_updated_at": universe_updated_at,
            "price_updated_at": price_updated_at,
            "scan_updated_at": scan_updated_at,
            "breadth_latest_date": None,
            "groups_latest_date": scan_as_of_date or as_of_date,
            "foundation_update_source_revision": coverage.get("source_revision"),
        },
        "coverage": coverage,
        "key_markets": [],
        "top_groups": top_groups[:10],
    }


def build_static_site_from_artifacts(
    *,
    foundation_update: Path,
    output_dir: Path,
    daily_price: Path | None = None,
    scan_metrics: Path | None = None,
    group_rank: Path | None = None,
    listing_profile: Path | None = None,
    etf_profile: Path | None = None,
) -> dict[str, Any]:
    generated_at = _utc_now()
    weekly = _read_json(foundation_update)
    daily = _read_json(daily_price) if daily_price else None
    metrics_bundle = _read_json(scan_metrics) if scan_metrics else None
    group_bundle = _read_json(group_rank) if group_rank else None
    listing_bundle = _read_json(listing_profile) if listing_profile else None
    etf_bundle = _read_json(etf_profile) if etf_profile else None
    latest_prices = _latest_daily_by_symbol(daily)
    metrics_by_symbol = _metrics_by_symbol(metrics_bundle)
    group_by_symbol = _rows_by_symbol(group_bundle)
    listing_by_symbol = _rows_by_symbol(listing_bundle)
    etf_by_symbol = _rows_by_symbol(etf_bundle)
    benchmark_prices = (latest_prices.get("SPY") or {}).get("prices") or []
    universe_as_of_date = str(weekly.get("as_of_date") or datetime.now(timezone.utc).date().isoformat())
    price_as_of_date = str(daily.get("as_of_date")) if daily and daily.get("as_of_date") else None
    scan_as_of_date = str(metrics_bundle.get("as_of_date")) if metrics_bundle and metrics_bundle.get("as_of_date") else None
    universe_updated_at = str(weekly.get("generated_at")) if weekly.get("generated_at") else None
    price_updated_at = str(daily.get("generated_at")) if daily and daily.get("generated_at") else None
    scan_updated_at = str(metrics_bundle.get("generated_at")) if metrics_bundle and metrics_bundle.get("generated_at") else None
    git_push_hash = _git_push_hash()
    as_of_date = universe_as_of_date
    coverage = dict((weekly.get("coverage") or {}))
    coverage["source_revision"] = weekly.get("source_revision")

    rows = []
    for source_row in _weekly_rows(weekly):
        payload = _row_payload(source_row)
        if not payload.get("symbol"):
            continue
        symbol = payload["symbol"]
        rows.append(_scan_row(
            payload,
            latest_prices.get(symbol),
            benchmark_prices,
            metrics_by_symbol.get(symbol),
            group_by_symbol.get(symbol),
            listing_by_symbol.get(symbol),
            etf_by_symbol.get(symbol),
        ))
    rows = _sort_rows(rows)
    _enrich_rows_with_option_pcr(rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    scan_manifest = _build_scan(
        output_dir,
        generated_at=generated_at,
        as_of_date=as_of_date,
        universe_as_of_date=universe_as_of_date,
        price_as_of_date=price_as_of_date,
        scan_as_of_date=scan_as_of_date,
        universe_updated_at=universe_updated_at,
        price_updated_at=price_updated_at,
        scan_updated_at=scan_updated_at,
        git_push_hash=git_push_hash,
        rows=rows,
    )

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
    home_payload = _build_home(
        generated_at=generated_at,
        as_of_date=as_of_date,
        universe_as_of_date=universe_as_of_date,
        price_as_of_date=price_as_of_date,
        scan_as_of_date=scan_as_of_date,
        universe_updated_at=universe_updated_at,
        price_updated_at=price_updated_at,
        scan_updated_at=scan_updated_at,
        git_push_hash=git_push_hash,
        rows=rows,
        coverage=coverage,
    )
    _write_json(output_dir / "markets/us/home.json", home_payload)
    _write_json(output_dir / "markets/us/breadth.json", breadth_payload)
    _write_json(output_dir / "markets/us/groups.json", groups_payload)

    market_entry = {
        "market": DEFAULT_MARKET,
        "display_name": DEFAULT_MARKET_DISPLAY,
        "as_of_date": as_of_date,
        "universe_as_of_date": universe_as_of_date,
        "price_as_of_date": price_as_of_date,
        "scan_as_of_date": scan_as_of_date,
        "universe_updated_at": universe_updated_at,
        "price_updated_at": price_updated_at,
        "scan_updated_at": scan_updated_at,
        "git_push_hash": git_push_hash,
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
        "universe_as_of_date": universe_as_of_date,
        "price_as_of_date": price_as_of_date,
        "scan_as_of_date": scan_as_of_date,
        "universe_updated_at": universe_updated_at,
        "price_updated_at": price_updated_at,
        "scan_updated_at": scan_updated_at,
        "git_push_hash": git_push_hash,
        "freshness": home_payload["freshness"],
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
    parser.add_argument("--foundation-update", required=True, type=Path)
    parser.add_argument("--daily-price", type=Path, default=None)
    parser.add_argument("--scan-metrics", type=Path, default=None)
    parser.add_argument("--group-rank", type=Path, default=None)
    parser.add_argument("--listing-profile", type=Path, default=None)
    parser.add_argument("--etf-profile", type=Path, default=None)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    summary = build_static_site_from_artifacts(
        foundation_update=args.foundation_update,
        daily_price=args.daily_price,
        scan_metrics=args.scan_metrics,
        group_rank=args.group_rank,
        listing_profile=args.listing_profile,
        etf_profile=args.etf_profile,
        output_dir=args.output_dir,
    )
    print(json.dumps({"rows_total": summary["rows_total"], "output_dir": str(args.output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
