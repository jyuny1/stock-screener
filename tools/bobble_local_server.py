#!/usr/bin/env python3
"""Local Bobble Schwab proxy.

Serves a small local-only API that reads ~/options/schwab_tokens.json, refreshes
Schwab OAuth tokens when needed, and proxies the option-chain endpoint for the
local Bobble HTML page. Tokens are never sent to the browser.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import shutil
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
CHAINS_URL = "https://api.schwabapi.com/marketdata/v1/chains"
PRICE_HISTORY_URL = "https://api.schwabapi.com/marketdata/v1/pricehistory"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,11}$")
DEFAULT_SOXL_D1_DATABASE = "stock-screener-soxl-price"


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None


def _safe_int(value: Any) -> int:
    parsed = _safe_float(value)
    return int(parsed) if parsed is not None else 0


def _quote_time_iso(value: Any) -> str | None:
    millis = _safe_float(value)
    if millis is None:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


class TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    def read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def needs_refresh(self, data: dict[str, Any]) -> bool:
        access_token = str(data.get("access_token") or "")
        if not access_token:
            return True
        refreshed_at = data.get("refreshed_at")
        expires_in = _safe_int(data.get("expires_in"))
        if not refreshed_at or not expires_in:
            return False
        try:
            refreshed = datetime.fromisoformat(str(refreshed_at).replace("Z", "+00:00"))
        except ValueError:
            return False
        return time.time() >= refreshed.timestamp() + expires_in - 90

    def refresh(self, data: dict[str, Any]) -> dict[str, Any]:
        client_id = str(data.get("client_id") or "")
        client_secret = str(data.get("client_secret") or "")
        refresh_token = str(data.get("refresh_token") or "")
        if not client_id or not client_secret or not refresh_token:
            raise RuntimeError("token file missing client_id/client_secret/refresh_token")
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode("utf-8")
        req = urllib.request.Request(
            TOKEN_URL,
            data=body,
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        new_data = {**data, **payload, "refreshed_at": datetime.now(timezone.utc).isoformat()}
        self.write(new_data)
        return new_data

    def access_token(self) -> str:
        data = self.read()
        if self.needs_refresh(data):
            data = self.refresh(data)
        token = str(data.get("access_token") or "")
        if not token:
            raise RuntimeError("access_token missing after refresh")
        return token


def flatten_contracts(symbol: str, exp_date_map: Any, option_type: str, snapshot_utc: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(exp_date_map, dict):
        return rows
    for exp_key, strikes in exp_date_map.items():
        if not isinstance(strikes, dict):
            continue
        for contracts in strikes.values():
            if not isinstance(contracts, list):
                continue
            for c in contracts:
                if not isinstance(c, dict):
                    continue
                bid = _safe_float(c.get("bid"))
                ask = _safe_float(c.get("ask"))
                mid = (bid + ask) / 2 if bid is not None and ask is not None else None
                spread_pct = ((ask - bid) / mid * 100) if mid and mid > 0 and bid is not None and ask is not None else None
                rows.append({
                    "snapshotUtc": snapshot_utc,
                    "underlying": symbol,
                    "optionType": option_type,
                    "contractSymbol": c.get("symbol"),
                    "description": c.get("description"),
                    "expirationDate": str(c.get("expirationDate") or str(exp_key).split(":")[0])[:10],
                    "dte": _safe_int(c.get("daysToExpiration")),
                    "strike": _safe_float(c.get("strikePrice")),
                    "bid": bid,
                    "ask": ask,
                    "last": _safe_float(c.get("last")),
                    "mark": _safe_float(c.get("mark")),
                    "mid": mid,
                    "spreadPct": spread_pct,
                    "bidSize": _safe_int(c.get("bidSize")),
                    "askSize": _safe_int(c.get("askSize")),
                    "volume": _safe_int(c.get("totalVolume")),
                    "openInterest": _safe_int(c.get("openInterest")),
                    "iv": _safe_float(c.get("volatility")),
                    "delta": _safe_float(c.get("delta")),
                    "gamma": _safe_float(c.get("gamma")),
                    "theta": _safe_float(c.get("theta")),
                    "vega": _safe_float(c.get("vega")),
                    "rho": _safe_float(c.get("rho")),
                    "inTheMoney": c.get("inTheMoney"),
                })
    return rows


def _get_with_token(store: TokenStore, url: str, *, attempts: int = 4) -> dict[str, Any]:
    retryable_statuses = {429, 500, 502, 503, 504}

    def request_with(token: str) -> dict[str, Any]:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))

    token = store.access_token()
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return request_with(token)
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 1:
                token = store.refresh(store.read())["access_token"]
                last_exc = exc
                continue
            if exc.code not in retryable_statuses or attempt == attempts:
                raise
            last_exc = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == attempts:
                raise
            last_exc = exc
        time.sleep(min(0.75 * (2 ** (attempt - 1)), 4.0))
    if last_exc:
        raise last_exc
    raise RuntimeError("Schwab request failed without response")


def fetch_chain(store: TokenStore, symbol: str, *, min_dte: int = 0, max_dte: int = 45, strike_count: int = 500) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    min_dte = max(0, int(min_dte))
    max_dte = max(min_dte, min(365, int(max_dte)))
    strike_count = max(1, min(500, int(strike_count)))
    query = urllib.parse.urlencode({
        "symbol": symbol,
        "contractType": "ALL",
        "strategy": "SINGLE",
        "includeUnderlyingQuote": "true",
        "optionType": "ALL",
        "fromDate": (today + timedelta(days=min_dte)).isoformat(),
        "toDate": (today + timedelta(days=max_dte)).isoformat(),
        "strikeCount": strike_count,
    })
    payload = _get_with_token(store, f"{CHAINS_URL}?{query}")
    snapshot_utc = datetime.now(timezone.utc).isoformat()
    underlying = payload.get("underlying") if isinstance(payload.get("underlying"), dict) else {}
    contracts = [
        *flatten_contracts(symbol, payload.get("callExpDateMap"), "CALL", snapshot_utc),
        *flatten_contracts(symbol, payload.get("putExpDateMap"), "PUT", snapshot_utc),
    ]
    expirations = sorted({row["expirationDate"] for row in contracts if row.get("expirationDate")})
    return {
        "symbol": symbol,
        "status": payload.get("status"),
        "strategy": payload.get("strategy"),
        "snapshotUtc": snapshot_utc,
        "underlying": {
            "symbol": symbol,
            "last": _safe_float(underlying.get("last")),
            "mark": _safe_float(underlying.get("mark")),
            "bid": _safe_float(underlying.get("bid")),
            "ask": _safe_float(underlying.get("ask")),
            "quoteTime": underlying.get("quoteTime"),
            "quoteTimeIso": _quote_time_iso(underlying.get("quoteTime")),
        },
        "summary": {
            "contracts": len(contracts),
            "calls": sum(1 for row in contracts if row.get("optionType") == "CALL"),
            "puts": sum(1 for row in contracts if row.get("optionType") == "PUT"),
            "expirations": len(expirations),
            "firstExpiration": expirations[0] if expirations else None,
            "lastExpiration": expirations[-1] if expirations else None,
        },
        "contracts": contracts,
    }


def fetch_price_history(store: TokenStore, symbol: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({
        "symbol": symbol,
        "periodType": "year",
        "period": 1,
        "frequencyType": "daily",
        "frequency": 1,
        "needExtendedHoursData": "false",
        "needPreviousClose": "false",
    })
    payload = _get_with_token(store, f"{PRICE_HISTORY_URL}?{query}")
    candles = payload.get("candles") if isinstance(payload, dict) else None
    if not isinstance(candles, list):
        return []
    rows: list[dict[str, Any]] = []
    for candle in candles:
        if not isinstance(candle, dict):
            continue
        close = _safe_float(candle.get("close"))
        high = _safe_float(candle.get("high"))
        low = _safe_float(candle.get("low"))
        if close is None or high is None or low is None or close <= 0:
            continue
        millis = _safe_float(candle.get("datetime"))
        day = datetime.fromtimestamp(millis / 1000, timezone.utc).date().isoformat() if millis else None
        rows.append({
            "date": day,
            "open": _safe_float(candle.get("open")) or close,
            "high": high,
            "low": low,
            "close": close,
            "volume": _safe_int(candle.get("volume")),
        })
    return rows


def _median(values: list[float]) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _atr14(rows: list[dict[str, Any]]) -> float | None:
    true_ranges: list[float] = []
    previous_close: float | None = None
    for row in rows:
        high = float(row["high"])
        low = float(row["low"])
        ranges = [high - low]
        if previous_close is not None:
            ranges.extend([abs(high - previous_close), abs(low - previous_close)])
        true_ranges.append(max(ranges))
        previous_close = float(row["close"])
    if len(true_ranges) < 14:
        return _median(true_ranges) if true_ranges else None
    return _median(true_ranges[-20:])


def _bounce_pct(rows: list[dict[str, Any]], index: int, price: float, level_type: str, lookahead: int = 10) -> float:
    future = rows[index + 1:index + lookahead + 1]
    if not future or price <= 0:
        return 0.0
    if level_type == "support":
        return max(0.0, (max(float(row["high"]) for row in future) / price - 1) * 100)
    return max(0.0, (price / min(float(row["low"]) for row in future) - 1) * 100)



def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _pct_distance(price: float, spot: float | None) -> float | None:
    if not spot:
        return None
    return (price / spot - 1) * 100


def _strict_local_extrema(rows: list[dict[str, Any]], column: str, mode: str) -> list[dict[str, Any]]:
    """Trendln-style 3-bar local extrema.

    This intentionally uses a narrow, explainable 3-bar pivot. Wider windows
    missed near support zones on high-volatility names such as SOXL/MU/SNDK.
    """
    points: list[dict[str, Any]] = []
    if len(rows) < 3:
        return points
    for index in range(1, len(rows) - 1):
        previous_value = float(rows[index - 1][column])
        value = float(rows[index][column])
        next_value = float(rows[index + 1][column])
        if mode == "min" and previous_value > value and next_value > value:
            points.append({"idx": index, "date": rows[index].get("date"), "price": value, "source": "pivot_low_3bar"})
        elif mode == "max" and previous_value < value and next_value < value:
            points.append({"idx": index, "date": rows[index].get("date"), "price": value, "source": "pivot_high_3bar"})
    return points


def _cluster_level_points(
    points: list[dict[str, Any]],
    *,
    tolerance: float,
    max_width: float,
    min_touches: int = 2,
) -> list[dict[str, Any]]:
    """Pure Python 1D clustering for pivot prices.

    Equivalent in spirit to Agglomerative/DBSCAN for one-dimensional price
    levels, but transparent and dependency-free.
    """
    if not points:
        return []
    clusters: list[list[dict[str, Any]]] = []
    group = [sorted(points, key=lambda item: float(item["price"]))[0]]
    for point in sorted(points, key=lambda item: float(item["price"]))[1:]:
        prices = [float(item["price"]) for item in group]
        center = _median(prices)
        new_prices = [*prices, float(point["price"])]
        if abs(float(point["price"]) - center) <= tolerance and max(new_prices) - min(new_prices) <= max_width:
            group.append(point)
        else:
            clusters.append(group)
            group = [point]
    clusters.append(group)

    out: list[dict[str, Any]] = []
    for group in clusters:
        if len(group) < min_touches:
            continue
        prices = [float(item["price"]) for item in group]
        ordered_by_time = sorted(group, key=lambda item: int(item["idx"]))
        out.append({
            "zoneLow": round(min(prices), 2),
            "zoneHigh": round(max(prices), 2),
            "primary": round(_median(prices), 2),
            "touchCount": len(group),
            "firstTouchedDate": ordered_by_time[0].get("date"),
            "lastTouchedDate": ordered_by_time[-1].get("date"),
            "firstIdx": int(ordered_by_time[0]["idx"]),
            "lastIdx": int(ordered_by_time[-1]["idx"]),
            "touchSpanBars": int(ordered_by_time[-1]["idx"]) - int(ordered_by_time[0]["idx"]),
            "members": [
                {"date": item.get("date"), "price": round(float(item["price"]), 2)}
                for item in ordered_by_time
            ],
            "sources": sorted({str(item.get("source") or "pivot") for item in group}),
        })
    return sorted(out, key=lambda item: float(item["primary"]))


def _nearest_strike(price: float, strikes: list[float] | None) -> tuple[float | None, float | None]:
    if not strikes:
        return None, None
    strike = min(strikes, key=lambda item: abs(float(item) - price))
    distance_pct = abs(float(strike) - price) / price * 100 if price else None
    return float(strike), distance_pct


def _confirmation_score(touch_count: int, span_bars: int, bars_since_last: int) -> int:
    # Simple, explainable confirmation score; not a trading edge score.
    score = 70 + min(18, max(0, touch_count - 2) * 6) + min(8, span_bars // 10)
    if bars_since_last > 126:
        score -= 12
    elif bars_since_last > 63:
        score -= 6
    return int(max(0, min(95, score)))


def _reference_label(window: int, level_type: str) -> tuple[str, str]:
    if level_type == "support":
        if window <= 3:
            return "short_dte_reference", "短線防守價"
        if window <= 10:
            return "pullback_reference", "回撤參考"
        if window <= 30:
            return "deep_pullback_reference", "深層參考"
        return "tail_reference", "尾部參考"
    if window <= 3:
        return "short_dte_resistance_reference", "短線壓力參考"
    if window <= 10:
        return "rebound_resistance_reference", "反彈壓力參考"
    if window <= 30:
        return "upper_range_reference", "上緣參考"
    return "tail_resistance_reference", "遠端壓力參考"


def _make_confirmed_level(
    zone: dict[str, Any],
    *,
    level_type: str,
    rows_len: int,
    daily_spot: float,
    quote_spot: float | None,
    reference_spot: float,
    strikes: list[float] | None,
) -> dict[str, Any]:
    price = float(zone["primary"])
    nearest_strike, nearest_strike_distance_pct = _nearest_strike(price, strikes)
    bars_since_last = rows_len - 1 - int(zone["lastIdx"])
    score = _confirmation_score(int(zone["touchCount"]), int(zone["touchSpanBars"]), bars_since_last)
    label = "確認支撐" if level_type == "support" else "確認壓力"
    distance_to_spot = _pct_distance(price, reference_spot) or 0.0
    return {
        "price": round(price, 2),
        "zoneLow": zone["zoneLow"],
        "zoneHigh": zone["zoneHigh"],
        "type": level_type,
        "kind": "confirmed_structure",
        "category": "confirmed",
        "confirmation": "confirmed",
        "confirmationLabel": label,
        "confirmationScore": score,
        # Backward-compatible fields used by the current HTML renderer.
        "strength": score,
        "quality": "confirmed",
        "touchCount": zone["touchCount"],
        "firstTouchedDate": zone["firstTouchedDate"],
        "lastTouchedDate": zone["lastTouchedDate"],
        "touchSpanBars": zone["touchSpanBars"],
        "barsSinceLastTouch": bars_since_last,
        "distanceToSpotPct": round(distance_to_spot, 2),
        "distanceToDailySpotPct": round(_pct_distance(price, daily_spot) or 0.0, 2),
        "distanceToQuoteSpotPct": round(_pct_distance(price, quote_spot), 2) if quote_spot else None,
        "nearestStrike": round(nearest_strike, 2) if nearest_strike is not None else None,
        "nearestStrikeDistancePct": round(nearest_strike_distance_pct, 2) if nearest_strike_distance_pct is not None else None,
        "sources": zone["sources"],
        "members": zone["members"],
        "reason": f"3-bar pivot clustered; touchCount {zone['touchCount']}, span {zone['touchSpanBars']} bars",
        "scoreBreakdown": {
            "method": "3-bar local extrema + 1D clustering",
            "touchCount": zone["touchCount"],
            "touchSpanBars": zone["touchSpanBars"],
            "barsSinceLastTouch": bars_since_last,
            "confirmationScore": score,
        },
        "calculationInputs": {
            "touchCount": zone["touchCount"],
            "spot": round(reference_spot, 2),
            "dailySpot": round(daily_spot, 2),
            "quoteSpot": round(quote_spot, 2) if quote_spot else None,
            "distanceToSpotPct": round(distance_to_spot, 2),
        },
    }


def _make_tactical_reference(
    *,
    rows: list[dict[str, Any]],
    window: int,
    level_type: str,
    daily_spot: float,
    quote_spot: float | None,
    reference_spot: float,
    strikes: list[float] | None,
) -> dict[str, Any] | None:
    if len(rows) < window:
        return None
    segment = rows[-window:]
    row = min(segment, key=lambda item: float(item["low"])) if level_type == "support" else max(segment, key=lambda item: float(item["high"]))
    price = float(row["low"] if level_type == "support" else row["high"])
    nearest_strike, nearest_strike_distance_pct = _nearest_strike(price, strikes)
    ref_use, label = _reference_label(window, level_type)
    priority = max(25, 58 - window // 2)
    distance_to_spot = _pct_distance(price, reference_spot) or 0.0
    return {
        "price": round(price, 2),
        "zoneLow": round(price, 2),
        "zoneHigh": round(price, 2),
        "type": level_type,
        "kind": "tactical_reference",
        "category": "tactical",
        "confirmation": "unconfirmed",
        "confirmationLabel": label,
        "confirmationScore": 0,
        "displayPriority": priority,
        # Backward compatibility; frontend now labels tactical separately.
        "strength": priority,
        "quality": "reference",
        "referenceUse": ref_use,
        "window": window,
        "touchCount": 1,
        "firstTouchedDate": row.get("date"),
        "lastTouchedDate": row.get("date"),
        "distanceToSpotPct": round(distance_to_spot, 2),
        "distanceToDailySpotPct": round(_pct_distance(price, daily_spot) or 0.0, 2),
        "distanceToQuoteSpotPct": round(_pct_distance(price, quote_spot), 2) if quote_spot else None,
        "nearestStrike": round(nearest_strike, 2) if nearest_strike is not None else None,
        "nearestStrikeDistancePct": round(nearest_strike_distance_pct, 2) if nearest_strike_distance_pct is not None else None,
        "sources": [f"rolling_{'low' if level_type == 'support' else 'high'}_{window}d"],
        "members": [{"date": row.get("date"), "price": round(price, 2)}],
        "reason": f"{window}D {'low' if level_type == 'support' else 'high'}; tactical reference only, not confirmed support/resistance",
        "calculationInputs": {
            "touchCount": 1,
            "spot": round(reference_spot, 2),
            "dailySpot": round(daily_spot, 2),
            "quoteSpot": round(quote_spot, 2) if quote_spot else None,
            "distanceToSpotPct": round(distance_to_spot, 2),
        },
    }


def calculate_levels(rows: list[dict[str, Any]], strikes: list[float] | None = None, quote_spot: float | None = None) -> dict[str, Any]:
    """Shared support/resistance calculation used by Bobble and SP analysis."""
    from app.services.support_resistance_service import calculate_operational_support_resistance_levels

    return calculate_operational_support_resistance_levels(rows, strikes=strikes, quote_spot=quote_spot)


def fetch_support_resistance(store: TokenStore, symbol: str, strikes: list[float] | None = None, quote_spot: float | None = None) -> dict[str, Any]:
    rows = fetch_price_history(store, symbol)
    levels = calculate_levels(rows, strikes=strikes, quote_spot=quote_spot)
    return {"symbol": symbol, "period": "1y", **levels}


def fetch_soxl_support_snapshot(database_name: str) -> dict[str, Any]:
    wrangler = shutil.which("wrangler")
    command = ([wrangler] if wrangler else ["npx", "--yes", "wrangler@latest"]) + [
        "d1",
        "execute",
        database_name,
        "--remote",
        "--json",
        "--command",
        (
            "SELECT symbol, as_of, spot, daily_support_json, intraday_support_json, "
            "merged_support_json, sell_put_buckets_json, created_at "
            "FROM soxl_support_snapshots ORDER BY as_of DESC LIMIT 1"
        ),
    ]
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "wrangler d1 execute failed").strip())
    stdout = completed.stdout.strip()
    json_start = stdout.find("[")
    if json_start > 0:
        stdout = stdout[json_start:]
    payload = json.loads(stdout)
    rows = payload[0].get("results", []) if payload else []
    if not rows:
        raise RuntimeError("soxl_support_snapshots is empty")
    row = rows[0]
    return {
        "symbol": row.get("symbol") or "SOXL",
        "asOf": row.get("as_of"),
        "spot": _safe_float(row.get("spot")),
        "dailySupport": json.loads(row.get("daily_support_json") or "{}"),
        "intradaySupport": json.loads(row.get("intraday_support_json") or "{}"),
        "mergedSupport": json.loads(row.get("merged_support_json") or "{}"),
        "sellPutSupportBuckets": json.loads(row.get("sell_put_buckets_json") or "{}").get("sellPutSupportBuckets", []),
        "createdAt": row.get("created_at"),
        "source": "d1_soxl_support_snapshots",
    }


def public_error_payload(error: str, exc: Exception) -> tuple[dict[str, str], int]:
    if isinstance(exc, urllib.error.HTTPError):
        url = getattr(exc, "url", "") or ""
        if exc.code == 401 or url.startswith(TOKEN_URL):
            return {
                "error": "schwab_auth_required",
                "message": "Schwab OAuth token 已失效或 refresh 失敗，請重新授權後再重試。",
            }, 401
        return {"error": error, "message": f"Schwab HTTP {exc.code}: {exc.reason}"}, 502
    return {"error": error, "message": f"{type(exc).__name__}: {exc}"}, 502


class Handler(BaseHTTPRequestHandler):
    store: TokenStore
    html_path: Path
    soxl_d1_database_name: str

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("access-control-allow-origin", "http://127.0.0.1:8787")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("access-control-allow-origin", "http://127.0.0.1:8787")
        self.send_header("access-control-allow-methods", "GET, OPTIONS")
        self.send_header("access-control-allow-headers", "content-type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/bobble"):
            body = self.html_path.read_bytes()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        params = urllib.parse.parse_qs(parsed.query)
        symbol = (params.get("symbol") or [""])[0].strip().upper()
        if parsed.path == "/api/options/live-chain":
            if not SYMBOL_RE.match(symbol):
                self.send_json({"error": "invalid_symbol"}, 400)
                return
            min_dte = _safe_int((params.get("minDte") or [0])[0])
            max_dte = _safe_int((params.get("maxDte") or [45])[0])
            strike_count = _safe_int((params.get("strikeCount") or [500])[0]) or 500
            try:
                self.send_json(fetch_chain(self.store, symbol, min_dte=min_dte, max_dte=max_dte, strike_count=strike_count))
            except Exception as exc:  # intentionally hides token details
                payload, status = public_error_payload("schwab_request_failed", exc)
                self.send_json(payload, status)
            return
        if parsed.path == "/api/support-resistance":
            if not SYMBOL_RE.match(symbol):
                self.send_json({"error": "invalid_symbol"}, 400)
                return
            strikes: list[float] = []
            for raw in (params.get("strikes") or [""])[0].split(","):
                parsed_strike = _safe_float(raw.strip())
                if parsed_strike and parsed_strike > 0:
                    strikes.append(parsed_strike)
            quote_spot = _safe_float((params.get("quoteSpot") or [""])[0])
            try:
                self.send_json(fetch_support_resistance(self.store, symbol, strikes=strikes or None, quote_spot=quote_spot))
            except Exception as exc:  # intentionally hides token details
                payload, status = public_error_payload("support_resistance_failed", exc)
                self.send_json(payload, status)
            return
        if parsed.path in ("/api/soxl/support-snapshot", "/api/v1/soxl/support-snapshot"):
            try:
                self.send_json(fetch_soxl_support_snapshot(self.soxl_d1_database_name))
            except Exception as exc:
                self.send_json({"error": "soxl_support_snapshot_failed", "message": f"{type(exc).__name__}: {exc}"}, 502)
            return
        self.send_json({"error": "not_found"}, 404)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Bobble Schwab proxy")
    parser.add_argument("--token-file", default="~/options/schwab_tokens.json")
    parser.add_argument("--html", default="data/option_chains/bobble_local_tokenfile.html")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--soxl-d1-database", default=os.environ.get("SOXL_D1_DATABASE_NAME", DEFAULT_SOXL_D1_DATABASE))
    args = parser.parse_args()

    Handler.store = TokenStore(Path(args.token_file))
    Handler.html_path = Path(args.html).resolve()
    Handler.soxl_d1_database_name = args.soxl_d1_database
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving Bobble at http://{args.host}:{args.port}/bobble")
    print(f"Using token file: {Handler.store.path}")
    server.serve_forever()


if __name__ == "__main__":
    main()
