"""Build a US foundation-update artifact without Postgres.

Artifact-native foundation update:
- reads the optionable-symbols artifact for the canonical US universe;
- reuses a prior foundation/legacy weekly-reference bundle when available;
- fetches only missing or stale symbols from yfinance;
- writes foundation-update-latest-us.json and foundation-update-us-YYYYMMDD.json.gz.

The output keeps the existing static-site contract:
``snapshot.rows[].normalized_payload``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yfinance as yf

try:
    from app.services.yf_session import get_session
except Exception:  # pragma: no cover
    get_session = None  # type: ignore[assignment]

MARKET = "US"
SCHEMA_VERSION = "foundation-update-bundle-v1"
MANIFEST_SCHEMA_VERSION = "foundation-update-manifest-v1"
DEFAULT_STALE_DAYS = 7


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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


def _num(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _symbol_for_yahoo(symbol: str) -> str:
    return symbol.replace(".", "-")


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _optionable_universe(optionable: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if optionable.get("market") != MARKET:
        raise ValueError(f"optionable market must be {MARKET}, got {optionable.get('market')!r}")
    symbols = [str(symbol).upper().strip() for symbol in optionable.get("symbols") or [] if str(symbol).strip()]
    metadata = optionable.get("symbol_metadata") or {}
    return sorted(dict.fromkeys(symbols)), {str(k).upper(): v for k, v in metadata.items() if isinstance(v, dict)}


def _prior_payloads(prior: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not prior:
        return {}
    result: dict[str, dict[str, Any]] = {}
    rows = ((prior.get("snapshot") or {}).get("rows") or prior.get("rows") or [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("normalized_payload") or row
        if not isinstance(payload, dict):
            continue
        symbol = str(row.get("symbol") or payload.get("symbol") or "").upper().strip()
        if symbol:
            result[symbol] = dict(payload, symbol=symbol, exchange=row.get("exchange") or payload.get("exchange"))
    return result


def _base_payload(symbol: str, metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = metadata or {}
    return {
        "symbol": symbol,
        "market": MARKET,
        "exchange": metadata.get("mic") or metadata.get("exchange"),
        "exchange_name": metadata.get("exchange"),
        "company_name": metadata.get("name") or symbol,
        "name": metadata.get("name") or symbol,
        "currency": "USD",
        "security_type": "ETF" if metadata.get("is_etf") else "stock",
        "is_etf": bool(metadata.get("is_etf")),
        "foundation_provider": "nasdaqtrader",
        "foundation_updated_at": _utc_now(),
    }


def _has_minimum_identity(payload: dict[str, Any]) -> bool:
    return bool(payload.get("symbol") and payload.get("company_name") and payload.get("exchange"))


def _is_stale(payload: dict[str, Any], *, stale_days: int) -> bool:
    updated = _parse_date(payload.get("foundation_updated_at") or payload.get("provider_updated_at"))
    if updated is None:
        # Legacy weekly-reference artifacts did not carry foundation timestamps.
        # Treat rows with useful provider fields as reusable during the migration
        # so the first artifact-native run only fetches genuinely missing symbols.
        return not any(payload.get(key) not in (None, "") for key in ("market_cap", "sector", "industry", "avg_volume"))
    return updated < (datetime.now(timezone.utc).date() - timedelta(days=stale_days))


def _get_fast_info_values(ticker: Any) -> dict[str, Any]:
    try:
        fast = getattr(ticker, "fast_info", None)
    except Exception:
        return {}
    if fast is None:
        return {}
    result = {}
    for target, attr in [
        ("market_cap", "market_cap"),
        ("shares_outstanding", "shares"),
        ("current_price", "last_price"),
    ]:
        try:
            result[target] = _num(getattr(fast, attr, None))
        except Exception:
            pass
    return {k: v for k, v in result.items() if v is not None}


def _fetch_symbol(symbol: str, base: dict[str, Any]) -> dict[str, Any]:
    yahoo_symbol = _symbol_for_yahoo(symbol)
    session = None
    if get_session is not None:
        try:
            session = get_session()
        except Exception:
            session = None
    try:
        ticker = yf.Ticker(yahoo_symbol, session=session) if session is not None else yf.Ticker(yahoo_symbol)
    except TypeError:
        ticker = yf.Ticker(yahoo_symbol)

    info: dict[str, Any] = {}
    error = None
    try:
        raw = ticker.info or {}
        if isinstance(raw, dict):
            info = raw
    except Exception as exc:  # provider variability
        error = str(exc)

    fast_values = _get_fast_info_values(ticker)
    payload = dict(base)
    long_name = info.get("longName") or info.get("shortName") or payload.get("company_name")
    sector = info.get("sector")
    industry = info.get("industry")
    quote_type = str(info.get("quoteType") or "").upper()
    payload.update(
        {
            "company_name": long_name,
            "name": long_name,
            "currency": info.get("currency") or payload.get("currency") or "USD",
            "sector": sector,
            "industry": industry,
            "ibd_industry_group": industry,
            "market_cap": _num(info.get("marketCap")) or fast_values.get("market_cap"),
            "market_cap_usd": _num(info.get("marketCap")) or fast_values.get("market_cap"),
            "shares_outstanding": _num(info.get("sharesOutstanding")) or fast_values.get("shares_outstanding"),
            "beta": _num(info.get("beta")),
            "pe_ratio": _num(info.get("trailingPE")),
            "forward_pe": _num(info.get("forwardPE")),
            "price_to_sales": _num(info.get("priceToSalesTrailing12Months")),
            "price_to_book": _num(info.get("priceToBook")),
            "avg_volume": _num(info.get("averageVolume")) or _num(info.get("averageDailyVolume10Day")),
            "eps_growth_qq": _num(info.get("earningsQuarterlyGrowth")) * 100 if _num(info.get("earningsQuarterlyGrowth")) is not None else None,
            "sales_growth_qq": _num(info.get("revenueGrowth")) * 100 if _num(info.get("revenueGrowth")) is not None else None,
            "eps_growth_ttm": None,
            "sales_growth_ttm": None,
            "ipo_date": None,
            "first_trade_date": None,
            "security_type": "ETF" if quote_type == "ETF" or payload.get("is_etf") else "stock",
            "is_etf": bool(quote_type == "ETF" or payload.get("is_etf")),
            "foundation_provider": "yfinance",
            "foundation_status": "complete" if info or fast_values else "metadata_only",
            "foundation_error": error,
            "foundation_updated_at": _utc_now(),
        }
    )
    # Keep metadata identity if yfinance returns empty/odd values.
    if not payload.get("company_name"):
        payload["company_name"] = base.get("company_name") or symbol
    if not payload.get("exchange"):
        payload["exchange"] = base.get("exchange")
    payload["field_availability"] = _field_availability(payload)
    return payload


def _field_availability(payload: dict[str, Any]) -> dict[str, bool]:
    return {
        "identity": bool(payload.get("symbol") and payload.get("company_name") and payload.get("exchange")),
        "classification": bool(payload.get("sector") or payload.get("industry")),
        "fundamentals": any(payload.get(k) is not None for k in ("market_cap", "shares_outstanding", "beta", "pe_ratio")),
        "growth": any(payload.get(k) is not None for k in ("eps_growth_qq", "sales_growth_qq", "eps_growth_ttm", "sales_growth_ttm")),
        "ipo": bool(payload.get("ipo_date") or payload.get("first_trade_date")),
    }


def _field_coverage(rows: list[dict[str, Any]]) -> dict[str, float]:
    fields = [
        "company_name", "exchange", "sector", "industry", "ibd_industry_group", "market_cap",
        "shares_outstanding", "beta", "pe_ratio", "forward_pe", "price_to_sales", "price_to_book",
        "avg_volume", "eps_growth_qq", "sales_growth_qq", "ipo_date",
    ]
    if not rows:
        return {field: 1.0 for field in fields}
    return {
        field: round(sum(row.get(field) not in (None, "") for row in rows) / len(rows), 6)
        for field in fields
    }


def build_foundation_update_artifact(
    *,
    optionable_symbols: Path,
    output_dir: Path,
    prior_foundation: Path | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
    batch_sleep_seconds: float = 0.25,
    min_symbol_coverage: float = 0.98,
    min_identity_coverage: float = 0.95,
    min_market_cap_coverage: float = 0.50,
) -> dict[str, Any]:
    optionable = _read_json(optionable_symbols)
    symbols, metadata_by_symbol = _optionable_universe(optionable)
    prior = _read_json(prior_foundation) if prior_foundation else None
    prior_by_symbol = _prior_payloads(prior)
    rows_by_symbol: dict[str, dict[str, Any]] = {}

    missing_or_stale: list[str] = []
    for symbol in symbols:
        base = _base_payload(symbol, metadata_by_symbol.get(symbol))
        prior_payload = prior_by_symbol.get(symbol)
        if prior_payload:
            merged = {**base, **prior_payload, "symbol": symbol, "market": MARKET}
            merged.setdefault("foundation_updated_at", _utc_now())
            merged["field_availability"] = _field_availability(merged)
            rows_by_symbol[symbol] = merged
            if _is_stale(merged, stale_days=stale_days):
                missing_or_stale.append(symbol)
        else:
            rows_by_symbol[symbol] = {**base, "field_availability": _field_availability(base)}
            missing_or_stale.append(symbol)

    failures: dict[str, str] = {}
    for index, symbol in enumerate(missing_or_stale, start=1):
        base = _base_payload(symbol, metadata_by_symbol.get(symbol))
        try:
            rows_by_symbol[symbol] = _fetch_symbol(symbol, base)
        except Exception as exc:  # provider variability
            failures[symbol] = str(exc)
            fallback = rows_by_symbol.get(symbol) or base
            fallback = {**fallback, "foundation_status": "metadata_only", "foundation_error": str(exc), "foundation_updated_at": _utc_now()}
            fallback["field_availability"] = _field_availability(fallback)
            rows_by_symbol[symbol] = fallback
        if index % 50 == 0 or index == len(missing_or_stale):
            print(f"[foundation] fetched={index}/{len(missing_or_stale)} total={len(symbols)} failures={len(failures)}", flush=True)
        if batch_sleep_seconds > 0 and index < len(missing_or_stale):
            time.sleep(batch_sleep_seconds)

    normalized_rows = [rows_by_symbol[symbol] for symbol in symbols]
    identity_covered = sum(_has_minimum_identity(row) for row in normalized_rows)
    symbol_coverage = identity_covered / len(symbols) if symbols else 1.0
    field_coverage = _field_coverage(normalized_rows)
    if symbol_coverage < min_symbol_coverage or symbol_coverage < min_identity_coverage:
        raise ValueError(f"Foundation identity coverage {symbol_coverage:.2%} below required threshold")
    if field_coverage.get("market_cap", 0) < min_market_cap_coverage:
        raise ValueError(
            f"Foundation market_cap coverage {field_coverage.get('market_cap', 0):.2%} below required "
            f"{min_market_cap_coverage:.2%}"
        )

    generated_at = _utc_now()
    as_of = _today()
    source_revision = f"foundation_update_us:artifact:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    snapshot_rows = [
        {
            "symbol": row["symbol"],
            "exchange": row.get("exchange"),
            "normalized_payload": row,
        }
        for row in normalized_rows
    ]
    coverage = {
        "universe_mode": "US_OPTIONABLE",
        "active_symbols": len(symbols),
        "attempted_symbols": len(symbols),
        "covered_active_symbols": identity_covered,
        "missing_active_symbols": len(symbols) - identity_covered,
        "snapshot_symbols": len(snapshot_rows),
        "partial_run": False,
        "source_revision": source_revision,
    }
    bundle_name = f"foundation-update-us-{as_of.replace('-', '')}.json.gz"
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / bundle_name
    payload = {
        "schema_version": SCHEMA_VERSION,
        "market": MARKET,
        "generated_at": generated_at,
        "as_of_date": as_of,
        "source_revision": source_revision,
        "universe_source_revision": optionable.get("source") or optionable.get("as_of"),
        "universe_symbol_count": len(symbols),
        "symbol_count": len(snapshot_rows),
        "covered_symbol_count": identity_covered,
        "symbol_coverage": round(symbol_coverage, 6),
        "field_coverage": field_coverage,
        "coverage": coverage,
        "failures": failures,
        "snapshot": {"rows": snapshot_rows},
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
        "universe_source_revision": payload["universe_source_revision"],
        "universe_symbol_count": len(symbols),
        "symbol_count": len(snapshot_rows),
        "covered_symbol_count": identity_covered,
        "symbol_coverage": round(symbol_coverage, 6),
        "field_coverage": field_coverage,
        "coverage": coverage,
        "failure_count": len(failures),
        "fetched_symbol_count": len(missing_or_stale),
        "stale_days": stale_days,
    }
    manifest_path = output_dir / "foundation-update-latest-us.json"
    _write_json(manifest_path, manifest)
    return {**manifest, "bundle_path": str(bundle_path), "manifest_path": str(manifest_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--optionable-symbols", required=True, type=Path)
    parser.add_argument("--prior-foundation", type=Path, default=None)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    parser.add_argument("--batch-sleep-seconds", type=float, default=0.25)
    parser.add_argument("--min-symbol-coverage", type=float, default=0.98)
    parser.add_argument("--min-identity-coverage", type=float, default=0.95)
    parser.add_argument("--min-market-cap-coverage", type=float, default=0.50)
    args = parser.parse_args()
    summary = build_foundation_update_artifact(
        optionable_symbols=args.optionable_symbols,
        prior_foundation=args.prior_foundation if args.prior_foundation and args.prior_foundation.exists() else None,
        output_dir=args.output_dir,
        stale_days=args.stale_days,
        batch_sleep_seconds=args.batch_sleep_seconds,
        min_symbol_coverage=args.min_symbol_coverage,
        min_identity_coverage=args.min_identity_coverage,
        min_market_cap_coverage=args.min_market_cap_coverage,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
