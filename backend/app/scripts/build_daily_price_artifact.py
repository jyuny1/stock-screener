"""Build a US daily-price artifact directly from foundation-update symbols.

Artifact-native daily price publisher:
- reads the foundation update bundle for the target US universe;
- optionally reuses a prior daily-price artifact;
- fetches missing/stale Yahoo daily bars in batches;
- writes daily-price-latest-us.json and daily-price-us-YYYYMMDD.json.gz.

No Postgres, SQLAlchemy, Redis, or static-site DB import is used.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

try:  # optional but useful on GitHub runners for Yahoo stability
    from app.services.yf_session import get_session
except Exception:  # pragma: no cover - optional import path
    get_session = None  # type: ignore[assignment]

SCHEMA_VERSION = "daily-price-bundle-v1"
MANIFEST_SCHEMA_VERSION = "daily-price-manifest-v1"
BAR_PERIOD = "2y"
MARKET = "US"


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


def _write_gzip_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, default=str)


def _finite(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _weekly_symbols(weekly_bundle: dict[str, Any]) -> dict[str, str | None]:
    if weekly_bundle.get("market") != MARKET:
        raise ValueError(f"foundation update market must be {MARKET}, got {weekly_bundle.get('market')!r}")
    coverage = weekly_bundle.get("coverage") or {}
    if coverage.get("universe_mode") != "US_OPTIONABLE":
        raise ValueError(f"foundation update must use US_OPTIONABLE, got {coverage.get('universe_mode')!r}")
    snapshot = weekly_bundle.get("snapshot") or {}
    rows = snapshot.get("rows") if isinstance(snapshot, dict) else None
    if not isinstance(rows, list):
        raise ValueError("foundation update snapshot.rows must be a list")
    symbols: dict[str, str | None] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("normalized_payload") or {}
        symbol = str(row.get("symbol") or payload.get("symbol") or "").upper().strip()
        if symbol:
            symbols[symbol] = row.get("exchange") or payload.get("exchange")
    return dict(sorted(symbols.items()))


def _prior_rows(prior_daily: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    if not prior_daily:
        return {}
    rows: dict[str, list[dict[str, Any]]] = {}
    for row in prior_daily.get("rows") or []:
        symbol = str(row.get("symbol") or "").upper().strip()
        prices = row.get("prices") or []
        if symbol and isinstance(prices, list) and prices:
            rows[symbol] = [_normalize_price(item) for item in prices if isinstance(item, dict)]
    return rows


def _normalize_price(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": str(row.get("date")),
        "open": _finite(row.get("open")),
        "high": _finite(row.get("high")),
        "low": _finite(row.get("low")),
        "close": _finite(row.get("close")),
        "adj_close": _finite(row.get("adj_close", row.get("adjclose", row.get("Adj Close")))),
        "volume": _finite(row.get("volume")),
    }


def _latest_date(rows: list[dict[str, Any]] | None) -> str | None:
    if not rows:
        return None
    dates = [str(row.get("date")) for row in rows if row.get("date")]
    return max(dates) if dates else None


def _rows_from_frame(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    cleaned = frame.reset_index()
    rows: list[dict[str, Any]] = []
    for _, item in cleaned.iterrows():
        raw_date = item.get("Date") or item.get("Datetime")
        if raw_date is None or pd.isna(raw_date):
            continue
        ts = pd.to_datetime(raw_date)
        row = {
            "date": ts.date().isoformat(),
            "open": _finite(item.get("Open")),
            "high": _finite(item.get("High")),
            "low": _finite(item.get("Low")),
            "close": _finite(item.get("Close")),
            "adj_close": _finite(item.get("Adj Close")),
            "volume": _finite(item.get("Volume")),
        }
        if all(row.get(key) is not None for key in ("open", "high", "low", "close", "volume")):
            rows.append(row)
    # Deduplicate by date after Yahoo occasionally returns duplicate index rows.
    by_date = {row["date"]: row for row in rows}
    return [by_date[key] for key in sorted(by_date)]


def _download_batch(symbols: list[str], *, period: str) -> dict[str, list[dict[str, Any]]]:
    if not symbols:
        return {}
    kwargs: dict[str, Any] = {
        "tickers": symbols,
        "period": period,
        "group_by": "ticker",
        "threads": False,
        "progress": False,
        "auto_adjust": False,
        "actions": False,
    }
    if get_session is not None:
        try:
            session = get_session()
            if session is not None:
                kwargs["session"] = session
        except Exception:
            pass
    raw = yf.download(**kwargs)
    result: dict[str, list[dict[str, Any]]] = {}
    if raw is None or raw.empty:
        return {symbol: [] for symbol in symbols}
    if len(symbols) == 1:
        result[symbols[0]] = _rows_from_frame(raw.copy())
        return result
    first_level = set(str(item) for item in raw.columns.get_level_values(0))
    for symbol in symbols:
        if symbol not in first_level:
            result[symbol] = []
            continue
        try:
            frame = raw[symbol].dropna(how="all")
        except Exception:
            result[symbol] = []
            continue
        result[symbol] = _rows_from_frame(frame)
    return result


def build_daily_price_artifact(
    *,
    foundation_update: Path,
    output_dir: Path,
    prior_daily: Path | None = None,
    batch_size: int = 100,
    batch_sleep_seconds: float = 1.0,
    min_symbol_coverage: float = 0.8,
    fetch_mode: str = "missing",
) -> dict[str, Any]:
    weekly = _read_json(foundation_update)
    symbols_by_exchange = _weekly_symbols(weekly)
    target_symbols = sorted(symbols_by_exchange)
    prior = _read_json(prior_daily) if prior_daily else None
    rows_by_symbol = _prior_rows(prior)

    if fetch_mode not in {"missing", "all"}:
        raise ValueError(f"Unsupported fetch_mode: {fetch_mode!r}")
    symbols_to_fetch = (
        target_symbols
        if fetch_mode == "all"
        else [symbol for symbol in target_symbols if not rows_by_symbol.get(symbol)]
    )

    failures: dict[str, str] = {}
    for start in range(0, len(symbols_to_fetch), max(1, int(batch_size))):
        batch = symbols_to_fetch[start:start + max(1, int(batch_size))]
        fetched = _download_batch(batch, period=BAR_PERIOD)
        for symbol in batch:
            rows = fetched.get(symbol) or []
            if rows:
                rows_by_symbol[symbol] = rows
                failures.pop(symbol, None)
            else:
                failures[symbol] = "empty_price_history"
        print(
            f"[prices] fetched={min(start + len(batch), len(symbols_to_fetch))}/{len(symbols_to_fetch)} "
            f"target={len(target_symbols)} covered={sum(1 for s in target_symbols if rows_by_symbol.get(s))} "
            f"failures={len(failures)} mode={fetch_mode}",
            flush=True,
        )
        if batch_sleep_seconds > 0 and start + len(batch) < len(symbols_to_fetch):
            time.sleep(batch_sleep_seconds)

    bundle_rows = [
        {
            "symbol": symbol,
            "exchange": symbols_by_exchange.get(symbol),
            "prices": rows_by_symbol[symbol],
        }
        for symbol in target_symbols
        if rows_by_symbol.get(symbol)
    ]
    if not bundle_rows:
        raise ValueError("No daily price rows were fetched")
    latest_dates = [_latest_date(row["prices"]) for row in bundle_rows]
    as_of = max(item for item in latest_dates if item)
    stale_symbols = [
        row["symbol"]
        for row in bundle_rows
        if _latest_date(row["prices"]) != as_of
    ]
    missing_symbols = [symbol for symbol in target_symbols if not rows_by_symbol.get(symbol)]
    covered = len(bundle_rows)
    coverage = covered / len(target_symbols) if target_symbols else 1.0
    if coverage < min_symbol_coverage:
        raise ValueError(f"Daily price coverage {coverage:.2%} below minimum {min_symbol_coverage:.2%}")

    generated_at = _utc_now()
    source_revision = f"daily_prices_us:artifact:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = f"daily-price-us-{as_of.replace('-', '')}.json.gz"
    bundle_path = output_dir / bundle_name
    payload = {
        "schema_version": SCHEMA_VERSION,
        "market": MARKET,
        "generated_at": generated_at,
        "as_of_date": as_of,
        "source_revision": source_revision,
        "bar_period": BAR_PERIOD,
        "symbol_count": covered,
        "missing_symbol_count": len(missing_symbols),
        "stale_symbol_count": len(stale_symbols),
        "allow_stale_complete": True,
        "symbol_universe_count": len(target_symbols),
        "covered_symbol_count": covered,
        "symbol_coverage": round(coverage, 6),
        "min_symbol_coverage": min_symbol_coverage,
        "fetch_mode": fetch_mode,
        "fetched_symbol_count": len(symbols_to_fetch),
        "failures": dict(sorted(failures.items())),
        "rows": bundle_rows,
    }
    _write_gzip_json(bundle_path, payload)
    sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "market": MARKET,
        "generated_at": generated_at,
        "as_of_date": as_of,
        "source_revision": source_revision,
        "bundle_asset_name": bundle_name,
        "sha256": sha256,
        "bar_period": BAR_PERIOD,
        "symbol_count": covered,
        "missing_symbol_count": len(missing_symbols),
        "stale_symbol_count": len(stale_symbols),
        "allow_stale_complete": True,
        "symbol_universe_count": len(target_symbols),
        "covered_symbol_count": covered,
        "symbol_coverage": round(coverage, 6),
        "min_symbol_coverage": min_symbol_coverage,
        "fetch_mode": fetch_mode,
        "fetched_symbol_count": len(symbols_to_fetch),
    }
    manifest_path = output_dir / "daily-price-latest-us.json"
    _write_json(manifest_path, manifest)
    return {
        "bundle_path": str(bundle_path),
        "manifest_path": str(manifest_path),
        "bundle_asset_name": bundle_name,
        "as_of_date": as_of,
        "symbol_count": covered,
        "symbol_universe_count": len(target_symbols),
        "missing_symbol_count": len(missing_symbols),
        "stale_symbol_count": len(stale_symbols),
        "symbol_coverage": round(coverage, 6),
        "sha256": sha256,
        "fetch_mode": fetch_mode,
        "fetched_symbol_count": len(symbols_to_fetch),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foundation-update", required=True, type=Path)
    parser.add_argument("--prior-daily", type=Path, default=None)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--batch-sleep-seconds", type=float, default=1.0)
    parser.add_argument("--min-symbol-coverage", type=float, default=0.8)
    parser.add_argument(
        "--fetch-mode",
        choices=("missing", "all"),
        default="missing",
        help="Fetch only symbols absent from the prior artifact, or refetch all symbols.",
    )
    args = parser.parse_args()
    summary = build_daily_price_artifact(
        foundation_update=args.foundation_update,
        prior_daily=args.prior_daily if args.prior_daily and args.prior_daily.exists() else None,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        batch_sleep_seconds=args.batch_sleep_seconds,
        min_symbol_coverage=args.min_symbol_coverage,
        fetch_mode=args.fetch_mode,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
