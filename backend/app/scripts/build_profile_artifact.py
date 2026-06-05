"""Build supplemental artifact-native US profile bundles.

Artifacts produced by this module fill static scan fields that should not be
computed or fetched inside the Static Site workflow:
- group-rank-data: industry/ETF group surrogate rank from scan metrics.
- listing-profile-data: IPO/listing date profile from foundation/provider cache.
- etf-profile-data: ETF net-assets/AUM profile from foundation/provider cache.

All outputs use manifest + gzipped bundle contracts with sha256 checksums.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None  # type: ignore[assignment]

try:
    from app.services.yf_session import get_session
except Exception:  # pragma: no cover
    get_session = None  # type: ignore[assignment]

MARKET = "US"
SCHEMAS = {
    "group-rank": ("group-rank-bundle-v1", "group-rank-manifest-v1", "group-rank"),
    "listing-profile": ("listing-profile-bundle-v1", "listing-profile-manifest-v1", "listing-profile"),
    "etf-profile": ("etf-profile-bundle-v1", "etf-profile-manifest-v1", "etf-profile"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
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


def _foundation_rows(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if bundle.get("market") != MARKET:
        raise ValueError(f"foundation market must be {MARKET}, got {bundle.get('market')!r}")
    result: dict[str, dict[str, Any]] = {}
    rows = ((bundle.get("snapshot") or {}).get("rows") or [])
    for row in rows:
        payload = row.get("normalized_payload") or {}
        symbol = str(row.get("symbol") or payload.get("symbol") or "").upper().strip()
        if symbol:
            result[symbol] = {**payload, "symbol": symbol, "exchange": row.get("exchange") or payload.get("exchange")}
    return dict(sorted(result.items()))


def _scan_rows(bundle: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not bundle:
        return {}
    return {str(row.get("symbol") or "").upper(): row for row in bundle.get("rows") or [] if row.get("symbol")}


def _prior_rows(bundle: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not bundle:
        return {}
    return {str(row.get("symbol") or "").upper(): row for row in bundle.get("rows") or [] if row.get("symbol")}


def _yahoo_info(symbol: str) -> dict[str, Any]:
    if yf is None:
        return {}
    session = None
    if get_session is not None:
        try:
            session = get_session()
        except Exception:
            session = None
    ticker_symbol = _symbol_for_yahoo(symbol)
    try:
        ticker = yf.Ticker(ticker_symbol, session=session) if session is not None else yf.Ticker(ticker_symbol)
    except TypeError:
        ticker = yf.Ticker(ticker_symbol)
    try:
        raw = ticker.info or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _first_trade_date(info: dict[str, Any]) -> str | None:
    raw = info.get("firstTradeDateEpochUtc") or info.get("firstTradeDateMilliseconds")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 10_000_000_000:
        value = value / 1000.0
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def _group_name(payload: dict[str, Any]) -> str:
    if payload.get("is_etf") or str(payload.get("security_type") or "").upper() == "ETF":
        return "ETF"
    return str(payload.get("ibd_industry_group") or payload.get("industry") or payload.get("sector") or "Unclassified")


def build_group_rank_artifact(*, foundation_update: Path, scan_metrics: Path, output_dir: Path) -> dict[str, Any]:
    foundation = _foundation_rows(_read_json(foundation_update) or {})
    metrics = _scan_rows(_read_json(scan_metrics))
    groups: dict[str, list[dict[str, Any]]] = {}
    for symbol, payload in foundation.items():
        metric = metrics.get(symbol, {})
        group = _group_name(payload)
        score = _num(metric.get("composite_score")) or _num(metric.get("rs_rating")) or 0
        groups.setdefault(group, []).append({"symbol": symbol, "score": float(score)})

    group_scores = []
    for group, items in groups.items():
        group_scores.append((group, mean([item["score"] for item in items]) if items else 0.0, len(items)))
    group_scores.sort(key=lambda item: (-item[1], item[0]))
    rank_by_group = {group: index + 1 for index, (group, _, _) in enumerate(group_scores)}
    score_by_group = {group: score for group, score, _ in group_scores}
    count_by_group = {group: count for group, _, count in group_scores}

    rows = []
    for symbol, payload in foundation.items():
        group = _group_name(payload)
        rows.append({
            "symbol": symbol,
            "group_name": group,
            "ibd_industry_group": group,
            "ibd_group_rank": rank_by_group[group],
            "group_rank": rank_by_group[group],
            "group_score": round(score_by_group[group], 2),
            "group_symbol_count": count_by_group[group],
            "source": "artifact_surrogate:industry_rs_composite",
        })
    return _publish(kind="group-rank", rows=rows, output_dir=output_dir)


def build_listing_profile_artifact(
    *,
    foundation_update: Path,
    output_dir: Path,
    prior_profile: Path | None = None,
    batch_sleep_seconds: float = 0.0,
) -> dict[str, Any]:
    foundation = _foundation_rows(_read_json(foundation_update) or {})
    prior = _prior_rows(_read_json(prior_profile))
    rows = []
    for index, (symbol, payload) in enumerate(foundation.items(), 1):
        existing = prior.get(symbol) or {}
        listing_date = payload.get("ipo_date") or payload.get("first_trade_date") or existing.get("ipo_date") or existing.get("listing_date")
        source = "foundation" if listing_date else existing.get("source")
        if not listing_date:
            info = _yahoo_info(symbol)
            listing_date = _first_trade_date(info)
            source = "yfinance:firstTradeDateEpochUtc" if listing_date else "missing"
            if batch_sleep_seconds > 0 and index < len(foundation):
                time.sleep(batch_sleep_seconds)
        rows.append({
            "symbol": symbol,
            "ipo_date": listing_date,
            "listing_date": listing_date,
            "source": source,
        })
        if index % 250 == 0:
            print(f"[listing-profile] processed={index}/{len(foundation)} covered={sum(1 for row in rows if row.get('listing_date'))}", flush=True)
    return _publish(kind="listing-profile", rows=rows, output_dir=output_dir)


def build_etf_profile_artifact(
    *,
    foundation_update: Path,
    output_dir: Path,
    prior_profile: Path | None = None,
    batch_sleep_seconds: float = 0.0,
) -> dict[str, Any]:
    foundation = _foundation_rows(_read_json(foundation_update) or {})
    prior = _prior_rows(_read_json(prior_profile))
    etfs = {s: p for s, p in foundation.items() if p.get("is_etf") or str(p.get("security_type") or "").upper() == "ETF"}
    rows = []
    for index, (symbol, payload) in enumerate(etfs.items(), 1):
        existing = prior.get(symbol) or {}
        net_assets = _num(payload.get("net_assets")) or _num(payload.get("total_assets")) or _num(payload.get("market_cap")) or _num(existing.get("net_assets"))
        expense_ratio = _num(payload.get("expense_ratio")) or _num(existing.get("expense_ratio"))
        issuer = payload.get("fund_family") or existing.get("issuer")
        source = "foundation" if net_assets else existing.get("source")
        if net_assets is None or expense_ratio is None or not issuer:
            info = _yahoo_info(symbol)
            net_assets = net_assets or _num(info.get("totalAssets")) or _num(info.get("marketCap"))
            expense_ratio = expense_ratio or _num(info.get("annualReportExpenseRatio"))
            issuer = issuer or info.get("fundFamily") or info.get("legalType")
            source = "yfinance:fundProfile" if (net_assets or expense_ratio or issuer) else "missing"
            if batch_sleep_seconds > 0 and index < len(etfs):
                time.sleep(batch_sleep_seconds)
        rows.append({
            "symbol": symbol,
            "net_assets": net_assets,
            "aum": net_assets,
            "expense_ratio": expense_ratio,
            "issuer": issuer,
            "asset_class": payload.get("sector") or "ETF",
            "source": source,
        })
        if index % 250 == 0:
            print(f"[etf-profile] processed={index}/{len(etfs)} covered={sum(1 for row in rows if row.get('net_assets'))}", flush=True)
    return _publish(kind="etf-profile", rows=rows, output_dir=output_dir, universe_count=len(foundation))


def _publish(*, kind: str, rows: list[dict[str, Any]], output_dir: Path, universe_count: int | None = None) -> dict[str, Any]:
    schema, manifest_schema, prefix = SCHEMAS[kind]
    generated_at = _utc_now()
    as_of = datetime.now(timezone.utc).date().isoformat()
    bundle_name = f"{prefix}-us-{as_of.replace('-', '')}.json.gz"
    payload = {
        "schema_version": schema,
        "market": MARKET,
        "generated_at": generated_at,
        "as_of_date": as_of,
        "symbol_count": len(rows),
        "symbol_universe_count": universe_count or len(rows),
        "rows": rows,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / bundle_name
    _write_gzip_json(bundle_path, payload)
    sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    field_keys = sorted({key for row in rows for key in row if key not in {"symbol"}})
    field_coverage = {key: round(sum(row.get(key) not in (None, "") for row in rows) / len(rows), 6) if rows else 1.0 for key in field_keys}
    manifest = {
        "schema_version": manifest_schema,
        "market": MARKET,
        "generated_at": generated_at,
        "as_of_date": as_of,
        "bundle_asset_name": bundle_name,
        "sha256": sha256,
        "symbol_count": len(rows),
        "symbol_universe_count": universe_count or len(rows),
        "field_coverage": field_coverage,
    }
    manifest_path = output_dir / f"{prefix}-latest-us.json"
    _write_json(manifest_path, manifest)
    return {**manifest, "bundle_path": str(bundle_path), "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build supplemental US profile artifacts")
    parser.add_argument("--mode", choices=["group-rank", "listing-profile", "etf-profile"], required=True)
    parser.add_argument("--foundation-update", type=Path, required=True)
    parser.add_argument("--scan-metrics", type=Path)
    parser.add_argument("--prior-profile", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch-sleep-seconds", type=float, default=0.0)
    args = parser.parse_args()
    if args.mode == "group-rank":
        if not args.scan_metrics:
            raise SystemExit("--scan-metrics is required for group-rank")
        summary = build_group_rank_artifact(foundation_update=args.foundation_update, scan_metrics=args.scan_metrics, output_dir=args.output_dir)
    elif args.mode == "listing-profile":
        summary = build_listing_profile_artifact(foundation_update=args.foundation_update, output_dir=args.output_dir, prior_profile=args.prior_profile, batch_sleep_seconds=args.batch_sleep_seconds)
    else:
        summary = build_etf_profile_artifact(foundation_update=args.foundation_update, output_dir=args.output_dir, prior_profile=args.prior_profile, batch_sleep_seconds=args.batch_sleep_seconds)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
