"""Build a SOXL price-history D1 import SQL file from Schwab REST API.

The script is intentionally standalone: it only fetches SOXL daily and
1-minute OHLCV candles and writes SQL that can be imported with
``wrangler d1 execute --file``. It does not compute support levels.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request
from zoneinfo import ZoneInfo

SCHWAB_PRICE_HISTORY_URL = "https://api.schwabapi.com/marketdata/v1/pricehistory"
SCHEMA_VERSION = "soxl-price-d1-v1"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return "NULL"
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fetch_price_history(access_token: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    query = parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = request.Request(
        f"{SCHWAB_PRICE_HISTORY_URL}?{query}",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    with request.urlopen(req, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    candles = payload.get("candles")
    if not isinstance(candles, list):
        raise RuntimeError(f"Schwab pricehistory response missing candles: {payload}")
    return [row for row in candles if isinstance(row, dict)]


def _normalize_daily(candles: list[dict[str, Any]], symbol: str, created_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candle in candles:
        ts = _int(candle.get("datetime"))
        if ts is None:
            continue
        dt = datetime.fromtimestamp(ts / 1000, ET)
        row = {
            "symbol": symbol,
            "trading_date": dt.date().isoformat(),
            "open": _float(candle.get("open")),
            "high": _float(candle.get("high")),
            "low": _float(candle.get("low")),
            "close": _float(candle.get("close")),
            "volume": _int(candle.get("volume")) or 0,
            "provider": "schwab",
            "created_at": created_at,
        }
        if all(row[k] is not None for k in ("open", "high", "low", "close")):
            rows.append(row)
    rows.sort(key=lambda r: r["trading_date"])
    return rows


def _normalize_intraday(candles: list[dict[str, Any]], symbol: str, created_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candle in candles:
        ts = _int(candle.get("datetime"))
        if ts is None:
            continue
        dt = datetime.fromtimestamp(ts / 1000, ET)
        row = {
            "symbol": symbol,
            "ts": ts,
            "trading_date": dt.date().isoformat(),
            "datetime_et": dt.replace(microsecond=0).isoformat(),
            "open": _float(candle.get("open")),
            "high": _float(candle.get("high")),
            "low": _float(candle.get("low")),
            "close": _float(candle.get("close")),
            "volume": _int(candle.get("volume")) or 0,
            "session": "regular",
            "provider": "schwab",
            "created_at": created_at,
        }
        if all(row[k] is not None for k in ("open", "high", "low", "close")):
            grouped.setdefault(row["trading_date"], []).append(row)
    for day_rows in grouped.values():
        day_rows.sort(key=lambda r: r["ts"])
        for row in day_rows:
            rows.append(row)
    rows.sort(key=lambda r: r["ts"])
    return rows


def _schema_sql() -> list[str]:
    return [
        "CREATE TABLE IF NOT EXISTS soxl_intraday_candles (symbol TEXT NOT NULL DEFAULT 'SOXL', ts INTEGER NOT NULL, trading_date TEXT NOT NULL, datetime_et TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL, session TEXT NOT NULL DEFAULT 'regular', provider TEXT NOT NULL DEFAULT 'schwab', created_at TEXT NOT NULL, PRIMARY KEY (symbol, ts))",
        "CREATE INDEX IF NOT EXISTS idx_soxl_intraday_date ON soxl_intraday_candles(trading_date, ts)",
        "CREATE TABLE IF NOT EXISTS soxl_daily_candles (symbol TEXT NOT NULL DEFAULT 'SOXL', trading_date TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL, provider TEXT NOT NULL DEFAULT 'schwab', created_at TEXT NOT NULL, PRIMARY KEY (symbol, trading_date))",
        "CREATE TABLE IF NOT EXISTS soxl_price_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    ]


def _insert_sql(table: str, row: dict[str, Any], columns: list[str]) -> str:
    values = ", ".join(_sql_literal(row.get(column)) for column in columns)
    return f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({values})"


def build_sql(symbol: str, daily: list[dict[str, Any]], intraday: list[dict[str, Any]], retention_days: int, created_at: str) -> str:
    latest_date = daily[-1]["trading_date"] if daily else datetime.now(ET).date().isoformat()
    cutoff = (datetime.fromisoformat(latest_date).date() - timedelta(days=retention_days - 1)).isoformat()
    # Wrangler remote D1 imports reject explicit BEGIN/COMMIT statements.
    statements = [*_schema_sql()]
    statements.append(f"DELETE FROM soxl_intraday_candles WHERE trading_date < {_sql_literal(cutoff)}")
    daily_cols = ["symbol", "trading_date", "open", "high", "low", "close", "volume", "provider", "created_at"]
    intraday_cols = ["symbol", "ts", "trading_date", "datetime_et", "open", "high", "low", "close", "volume", "session", "provider", "created_at"]
    statements.extend(_insert_sql("soxl_daily_candles", row, daily_cols) for row in daily)
    statements.extend(_insert_sql("soxl_intraday_candles", row, intraday_cols) for row in intraday)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "generated_at": created_at,
        "latest_daily_date": latest_date,
        "latest_intraday_date": intraday[-1]["trading_date"] if intraday else "",
        "daily_rows_imported": str(len(daily)),
        "intraday_rows_imported": str(len(intraday)),
        "intraday_retention_days": str(retention_days),
        "provider": "schwab",
    }
    for key, value in metadata.items():
        statements.append(f"INSERT OR REPLACE INTO soxl_price_metadata (key, value) VALUES ({_sql_literal(key)}, {_sql_literal(value)})")
    return ";\n".join(statements) + ";\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SOXL D1 price import SQL from Schwab pricehistory")
    parser.add_argument("--symbol", default="SOXL")
    parser.add_argument("--intraday-period-days", type=int, default=10, choices=[1, 2, 3, 4, 5, 10])
    parser.add_argument("--daily-period-years", type=int, default=5, choices=[1, 2, 3, 5, 10, 15, 20])
    parser.add_argument("--retention-days", type=int, default=180)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    access_token = os.environ.get("SCHWAB_ACCESS_TOKEN")
    if not access_token:
        raise RuntimeError("Missing SCHWAB_ACCESS_TOKEN")
    symbol = args.symbol.upper()
    created_at = _utc_now()
    intraday_raw = _fetch_price_history(access_token, {
        "symbol": symbol,
        "periodType": "day",
        "period": args.intraday_period_days,
        "frequencyType": "minute",
        "frequency": 1,
        "needExtendedHoursData": "false",
        "needPreviousClose": "true",
    })
    daily_raw = _fetch_price_history(access_token, {
        "symbol": symbol,
        "periodType": "year",
        "period": args.daily_period_years,
        "frequencyType": "daily",
        "frequency": 1,
        "needExtendedHoursData": "false",
        "needPreviousClose": "true",
    })
    intraday = _normalize_intraday(intraday_raw, symbol, created_at)
    daily = _normalize_daily(daily_raw, symbol, created_at)
    if not daily:
        raise RuntimeError("No daily candles returned")
    if not intraday:
        raise RuntimeError("No intraday candles returned")
    sql = build_sql(symbol, daily, intraday, args.retention_days, created_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(sql, encoding="utf-8")
    print(json.dumps({
        "symbol": symbol,
        "output": str(args.output),
        "daily_rows": len(daily),
        "intraday_rows": len(intraday),
        "latest_daily_date": daily[-1]["trading_date"],
        "latest_intraday_date": intraday[-1]["trading_date"],
        "retention_days": args.retention_days,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
