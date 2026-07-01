"""Build a SOXL price-history D1 import SQL file from Schwab REST API.

The script fetches SOXL daily and 1-minute OHLCV candles, computes the latest
support snapshot with the shared support-resistance service, and writes SQL
that can be imported with ``wrangler d1 execute --file``.
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
# Keep a conservative margin below D1/SQLite remote import statement limits so
# the GitHub Action fails early with actionable context instead of a generic
# Wrangler ``SQLITE_TOOBIG`` error.
MAX_SQL_STATEMENT_BYTES = 512 * 1024
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


def _keep_latest_trading_dates(rows: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    dates = sorted({str(row["trading_date"]) for row in rows})
    keep = set(dates[-days:]) if days > 0 else set(dates)
    return [row for row in rows if row["trading_date"] in keep]


def _append_intraday_daily_bar_if_needed(
    daily: list[dict[str, Any]],
    intraday: list[dict[str, Any]],
    *,
    symbol: str,
    created_at: str,
) -> list[dict[str, Any]]:
    """Use the latest intraday session as a provisional daily bar when Schwab daily lags.

    The scheduled job runs shortly after the US close. Schwab's daily endpoint can
    still report yesterday while the 1-minute endpoint already has the completed
    current regular session. Without this guard the D1 support snapshot is marked
    with today's intraday ``as_of`` but its daily support model is missing today's
    OHLCV bar.
    """

    if not daily or not intraday:
        return daily
    latest_daily_date = str(daily[-1]["trading_date"])
    latest_intraday_date = str(intraday[-1]["trading_date"])
    if latest_intraday_date <= latest_daily_date:
        return daily
    session_rows = [row for row in intraday if str(row["trading_date"]) == latest_intraday_date]
    if not session_rows:
        return daily
    synthetic = {
        "symbol": symbol,
        "trading_date": latest_intraday_date,
        "open": session_rows[0]["open"],
        "high": max(float(row["high"]) for row in session_rows),
        "low": min(float(row["low"]) for row in session_rows),
        "close": session_rows[-1]["close"],
        "volume": sum(int(row.get("volume") or 0) for row in session_rows),
        "provider": "schwab_intraday_provisional_daily",
        "created_at": created_at,
    }
    return [*daily, synthetic]


_DAILY_SUPPORT_KEYS = {
    "status",
    "spot",
    "dailySpot",
    "quoteSpot",
    "asOf",
    "barCount",
    "atr14",
    "atrPct",
    "adr20",
    "adr20Pct",
    "highVelocity",
    "clusterTolerance",
    "clusterMaxWidth",
    "levels",
    "supportLevels",
    "resistanceLevels",
    "operationalFilter",
    "pivotCounts",
    "warnings",
}

_INTRADAY_SUPPORT_KEYS = {
    "status",
    "spot",
    "asOf",
    "barCount",
    "dateCount",
    "firstDate",
    "lastDate",
    "dailyAtrUsed",
    "dailyAtrPct",
    "median1mTrueRange",
    "p90_1mTrueRange",
    "clusterTolerance",
    "maxZoneWidth",
    "latestDayLow",
    "latestDayHigh",
    "zones",
    "warnings",
}


def _compact_dict(payload: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {key: payload[key] for key in keys if key in payload}


def _compact_support_payloads(
    daily_support: dict[str, Any],
    intraday_support: dict[str, Any],
    merged_support: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Drop duplicate/verbose support internals before writing D1 SQL literals.

    D1 remote imports can reject very large individual SQL statements with
    ``SQLITE_TOOBIG``. The raw merged payload embeds the full daily and intraday
    payloads again, and the daily payload also carries hidden historical/tactical
    structures that are not needed by the SOXL snapshot endpoint. Keeping only
    endpoint-facing fields makes the snapshot deterministic and import-safe.
    """

    compact_daily = _compact_dict(daily_support, _DAILY_SUPPORT_KEYS)
    compact_intraday = _compact_dict(intraday_support, _INTRADAY_SUPPORT_KEYS)
    compact_merged = {
        "status": merged_support.get("status"),
        "spot": merged_support.get("spot"),
        "mergedZones": merged_support.get("mergedZones", []),
    }
    return compact_daily, compact_intraday, compact_merged


def _schema_sql() -> list[str]:
    return [
        "CREATE TABLE IF NOT EXISTS soxl_intraday_candles (symbol TEXT NOT NULL DEFAULT 'SOXL', ts INTEGER NOT NULL, trading_date TEXT NOT NULL, datetime_et TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL, session TEXT NOT NULL DEFAULT 'regular', provider TEXT NOT NULL DEFAULT 'schwab', created_at TEXT NOT NULL, PRIMARY KEY (symbol, ts))",
        "CREATE INDEX IF NOT EXISTS idx_soxl_intraday_date ON soxl_intraday_candles(trading_date, ts)",
        "CREATE TABLE IF NOT EXISTS soxl_daily_candles (symbol TEXT NOT NULL DEFAULT 'SOXL', trading_date TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL, provider TEXT NOT NULL DEFAULT 'schwab', created_at TEXT NOT NULL, PRIMARY KEY (symbol, trading_date))",
        "CREATE TABLE IF NOT EXISTS soxl_support_snapshots (symbol TEXT NOT NULL DEFAULT 'SOXL', as_of TEXT NOT NULL, spot REAL NOT NULL, daily_support_json TEXT NOT NULL, intraday_support_json TEXT NOT NULL, merged_support_json TEXT NOT NULL, sell_put_buckets_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (symbol, as_of))",
        "CREATE INDEX IF NOT EXISTS idx_soxl_support_as_of ON soxl_support_snapshots(as_of)",
        "CREATE TABLE IF NOT EXISTS soxl_price_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    ]


def _insert_sql(table: str, row: dict[str, Any], columns: list[str]) -> str:
    values = ", ".join(_sql_literal(row.get(column)) for column in columns)
    return f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({values})"


def _statement_label(statement: str) -> str:
    upper = statement.upper()
    if upper.startswith("INSERT OR REPLACE INTO "):
        return statement.split(" ", 5)[4]
    if upper.startswith("CREATE TABLE IF NOT EXISTS "):
        return statement.split(" ", 6)[5]
    if upper.startswith("DELETE FROM "):
        return statement.split(" ", 3)[2]
    return statement[:80]


def _validate_statement_sizes(statements: list[str], *, max_bytes: int = MAX_SQL_STATEMENT_BYTES) -> None:
    oversized: list[tuple[int, int, str]] = []
    for index, statement in enumerate(statements, start=1):
        size = len((statement + ";").encode("utf-8"))
        if size > max_bytes:
            oversized.append((index, size, _statement_label(statement)))
    if oversized:
        detail = ", ".join(
            f"No. {index} {label}={size} bytes" for index, size, label in oversized[:5]
        )
        raise RuntimeError(
            f"SOXL D1 import SQL has statement(s) above {max_bytes} bytes: {detail}"
        )


def _service_daily_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": row["trading_date"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
        for row in rows
    ]


def _service_intraday_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": row["trading_date"],
            "datetime_et": row["datetime_et"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
        for row in rows
    ]


def build_support_snapshot(symbol: str, daily: list[dict[str, Any]], intraday: list[dict[str, Any]], created_at: str) -> dict[str, Any]:
    from app.services.support_resistance_service import (
        calculate_intraday_tactical_support_levels,
        calculate_operational_support_resistance_levels,
        classify_sell_put_support_buckets,
        merge_daily_intraday_support_context,
    )

    quote_spot = float(intraday[-1]["close"])
    daily_support = calculate_operational_support_resistance_levels(
        _service_daily_rows(daily[-252:]),
        quote_spot=quote_spot,
    )
    intraday_support = calculate_intraday_tactical_support_levels(
        _service_intraday_rows(intraday),
        quote_spot=quote_spot,
        daily_atr=daily_support.get("atr14"),
        trading_days=len({row["trading_date"] for row in intraday}),
    )
    merged_support = merge_daily_intraday_support_context(
        daily_support,
        intraday_support,
        quote_spot=quote_spot,
    )
    daily_support, intraday_support, merged_support = _compact_support_payloads(
        daily_support,
        intraday_support,
        merged_support,
    )
    sell_put_buckets = classify_sell_put_support_buckets(merged_support)
    as_of = str(intraday[-1]["trading_date"])
    return {
        "symbol": symbol,
        "as_of": as_of,
        "spot": quote_spot,
        "daily_support_json": json.dumps(daily_support, ensure_ascii=False, separators=(",", ":")),
        "intraday_support_json": json.dumps(intraday_support, ensure_ascii=False, separators=(",", ":")),
        "merged_support_json": json.dumps(merged_support, ensure_ascii=False, separators=(",", ":")),
        "sell_put_buckets_json": json.dumps(sell_put_buckets, ensure_ascii=False, separators=(",", ":")),
        "created_at": created_at,
    }


def build_sql(symbol: str, daily: list[dict[str, Any]], intraday: list[dict[str, Any]], retention_days: int, created_at: str, support_snapshot: dict[str, Any] | None = None) -> str:
    latest_date = daily[-1]["trading_date"] if daily else datetime.now(ET).date().isoformat()
    cutoff = (datetime.fromisoformat(latest_date).date() - timedelta(days=retention_days - 1)).isoformat()
    # Wrangler remote D1 imports reject explicit BEGIN/COMMIT statements.
    statements = [*_schema_sql()]
    statements.append(f"DELETE FROM soxl_intraday_candles WHERE trading_date < {_sql_literal(cutoff)}")
    daily_cols = ["symbol", "trading_date", "open", "high", "low", "close", "volume", "provider", "created_at"]
    intraday_cols = ["symbol", "ts", "trading_date", "datetime_et", "open", "high", "low", "close", "volume", "session", "provider", "created_at"]
    support_cols = ["symbol", "as_of", "spot", "daily_support_json", "intraday_support_json", "merged_support_json", "sell_put_buckets_json", "created_at"]
    statements.extend(_insert_sql("soxl_daily_candles", row, daily_cols) for row in daily)
    statements.extend(_insert_sql("soxl_intraday_candles", row, intraday_cols) for row in intraday)
    if support_snapshot is not None:
        statements.append(_insert_sql("soxl_support_snapshots", support_snapshot, support_cols))
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "generated_at": created_at,
        "latest_daily_date": latest_date,
        "latest_intraday_date": intraday[-1]["trading_date"] if intraday else "",
        "daily_rows_imported": str(len(daily)),
        "intraday_rows_imported": str(len(intraday)),
        "intraday_retention_days": str(retention_days),
        "latest_support_as_of": str(support_snapshot.get("as_of") if support_snapshot else ""),
        "provider": "schwab",
    }
    for key, value in metadata.items():
        statements.append(f"INSERT OR REPLACE INTO soxl_price_metadata (key, value) VALUES ({_sql_literal(key)}, {_sql_literal(value)})")
    _validate_statement_sizes(statements)
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
    # Schwab's period=10 minute query can lag on some runs. Use explicit
    # start/end epoch milliseconds so the current/most recent regular session
    # is included whenever Schwab pricehistory has published it.
    now_et = datetime.now(ET)
    intraday_start_et = (now_et - timedelta(days=max(14, args.intraday_period_days * 3))).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    intraday_raw = _fetch_price_history(access_token, {
        "symbol": symbol,
        "periodType": "day",
        "frequencyType": "minute",
        "frequency": 1,
        "startDate": int(intraday_start_et.timestamp() * 1000),
        "endDate": int(now_et.timestamp() * 1000),
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
    intraday = _keep_latest_trading_dates(
        _normalize_intraday(intraday_raw, symbol, created_at),
        args.intraday_period_days,
    )
    daily = _append_intraday_daily_bar_if_needed(
        _normalize_daily(daily_raw, symbol, created_at),
        intraday,
        symbol=symbol,
        created_at=created_at,
    )
    if not daily:
        raise RuntimeError("No daily candles returned")
    if not intraday:
        raise RuntimeError("No intraday candles returned")
    support_snapshot = build_support_snapshot(symbol, daily, intraday, created_at)
    sql = build_sql(symbol, daily, intraday, args.retention_days, created_at, support_snapshot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(sql, encoding="utf-8")
    print(json.dumps({
        "symbol": symbol,
        "output": str(args.output),
        "daily_rows": len(daily),
        "intraday_rows": len(intraday),
        "latest_daily_date": daily[-1]["trading_date"],
        "latest_intraday_date": intraday[-1]["trading_date"],
        "latest_support_as_of": support_snapshot["as_of"],
        "retention_days": args.retention_days,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
