"""Build artifact-native US scan metrics from foundation update + daily price bundles.

This script is intentionally self-contained and does not use Postgres, Redis, or
live market-data APIs. It derives static-page scan metrics from release
artifacts only, then publishes a compact metrics bundle that static-site builds
can merge into scan rows.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

MARKET = "US"
SCHEMA_VERSION = "scan-metrics-bundle-v1"
MANIFEST_SCHEMA_VERSION = "scan-metrics-manifest-v1"


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


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def _foundation_rows(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if bundle.get("market") != MARKET:
        raise ValueError(f"foundation update market must be {MARKET}, got {bundle.get('market')!r}")
    rows = ((bundle.get("snapshot") or {}).get("rows") or [])
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = row.get("normalized_payload") or {}
        symbol = str(row.get("symbol") or payload.get("symbol") or "").upper().strip()
        if symbol:
            result[symbol] = {**payload, "symbol": symbol, "exchange": row.get("exchange") or payload.get("exchange")}
    return dict(sorted(result.items()))


def _daily_rows(bundle: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    if bundle.get("market") != MARKET:
        raise ValueError(f"daily price market must be {MARKET}, got {bundle.get('market')!r}")
    result: dict[str, list[dict[str, Any]]] = {}
    for row in bundle.get("rows") or []:
        symbol = str(row.get("symbol") or "").upper().strip()
        prices = row.get("prices") or []
        cleaned = []
        for item in prices:
            close = _num(item.get("close"))
            if not item.get("date") or close is None:
                continue
            cleaned.append(
                {
                    "date": str(item.get("date")),
                    "open": _num(item.get("open")),
                    "high": _num(item.get("high")),
                    "low": _num(item.get("low")),
                    "close": close,
                    "volume": _num(item.get("volume")) or 0.0,
                }
            )
        if symbol and cleaned:
            result[symbol] = sorted(cleaned, key=lambda item: item["date"])
    return result


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return mean(values[-window:])


def _return(values: list[float], lookback: int) -> float | None:
    if len(values) <= lookback or values[-lookback - 1] == 0:
        return None
    return ((values[-1] - values[-lookback - 1]) / values[-lookback - 1]) * 100.0


def _daily_returns(values: list[float]) -> list[float]:
    result = []
    for prev, cur in zip(values, values[1:]):
        if prev:
            result.append((cur - prev) / prev)
    return result


def _percentile(values: dict[str, float | None], *, high_is_good: bool = True) -> dict[str, float | None]:
    usable = [(symbol, value) for symbol, value in values.items() if value is not None and math.isfinite(value)]
    if not usable:
        return {symbol: None for symbol in values}
    usable.sort(key=lambda item: item[1])
    n = len(usable)
    ranks: dict[str, float] = {}
    for index, (symbol, _) in enumerate(usable):
        pct = 100.0 if n == 1 else (index / (n - 1)) * 99.0 + 1.0
        ranks[symbol] = pct if high_is_good else 101.0 - pct
    return {symbol: _round(ranks.get(symbol), 1) for symbol in values}


def _linear_slope(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    n = len(values)
    x_mean = (n - 1) / 2
    y_mean = mean(values)
    denom = sum((i - x_mean) ** 2 for i in range(n))
    if denom == 0:
        return None
    return sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values)) / denom


def _beta(stock_returns: list[float], benchmark_returns: list[float]) -> float | None:
    n = min(len(stock_returns), len(benchmark_returns), 252)
    if n < 30:
        return None
    s = stock_returns[-n:]
    b = benchmark_returns[-n:]
    b_mean = mean(b)
    s_mean = mean(s)
    var_b = sum((x - b_mean) ** 2 for x in b)
    if var_b == 0:
        return None
    cov = sum((x - s_mean) * (y - b_mean) for x, y in zip(s, b))
    return cov / var_b


def _adr_percent(rows: list[dict[str, Any]], window: int = 20) -> float | None:
    usable = []
    for row in rows[-window:]:
        high = _num(row.get("high"))
        low = _num(row.get("low"))
        close = _num(row.get("close"))
        if high is not None and low is not None and close not in (None, 0):
            usable.append(((high - low) / close) * 100.0)
    return mean(usable) if usable else None


def _atr_percent(rows: list[dict[str, Any]], window: int = 14) -> float | None:
    if len(rows) < window + 1:
        return None
    trs = []
    for prev, cur in zip(rows[-window - 1:-1], rows[-window:]):
        high = cur.get("high") or cur["close"]
        low = cur.get("low") or cur["close"]
        prev_close = prev["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    close = rows[-1]["close"]
    return (mean(trs) / close) * 100.0 if close else None


def _bb_width_percentile(closes: list[float], window: int = 20, lookback: int = 252) -> float | None:
    if len(closes) < window + 5:
        return None
    widths = []
    start = max(window, len(closes) - lookback)
    for end in range(start, len(closes) + 1):
        sample = closes[end - window:end]
        mid = mean(sample)
        if mid == 0:
            continue
        widths.append((4 * pstdev(sample) / mid) * 100.0)
    if not widths:
        return None
    current = widths[-1]
    below = sum(1 for value in widths if value <= current)
    return (below / len(widths)) * 100.0


def _stage(close: float, sma50: float | None, sma150: float | None, sma200: float | None) -> int | None:
    if sma50 is None or sma150 is None or sma200 is None:
        return None
    if close > sma50 > sma150 > sma200:
        return 2
    if close < sma50 < sma150 < sma200:
        return 4
    if close > sma200 and sma50 > sma150:
        return 1
    return 3


def _minervini_score(closes: list[float], rs_rating: float | None) -> tuple[float | None, bool | None]:
    if len(closes) < 200:
        return None, None
    close = closes[-1]
    sma50 = _sma(closes, 50)
    sma150 = _sma(closes, 150)
    sma200 = _sma(closes, 200)
    sma200_old = _sma(closes[:-20], 200) if len(closes) >= 220 else None
    high52 = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    low52 = min(closes[-252:]) if len(closes) >= 252 else min(closes)
    checks = [
        close > (sma150 or math.inf) and close > (sma200 or math.inf),
        (sma150 or 0) > (sma200 or math.inf),
        sma200_old is not None and (sma200 or 0) > sma200_old,
        (sma50 or 0) > (sma150 or math.inf) and (sma50 or 0) > (sma200 or math.inf),
        close > (sma50 or math.inf),
        close >= low52 * 1.30,
        close >= high52 * 0.75,
        (rs_rating or 0) >= 70,
    ]
    passed = sum(bool(item) for item in checks)
    return round((passed / len(checks)) * 100.0, 1), passed == len(checks)


def _rs_line_new_high(rows: list[dict[str, Any]], benchmark_rows: list[dict[str, Any]], *, window: int = 252) -> bool | None:
    benchmark_by_date = {str(row.get("date")): row.get("close") for row in benchmark_rows if row.get("date") and row.get("close")}
    ratios = []
    for row in rows[-window:]:
        bench = benchmark_by_date.get(str(row.get("date")))
        if bench not in (None, 0):
            ratios.append(row["close"] / bench)
    if len(ratios) < 20:
        return None
    return ratios[-1] >= max(ratios)


def _rating_label(score: float | None, *, metrics_available: bool) -> str:
    if not metrics_available or score is None:
        return "Insufficient Data"
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "E"


def _setup_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [row["close"] for row in rows]
    volumes = [row.get("volume") or 0.0 for row in rows]
    if not closes:
        return {}
    close = closes[-1]
    recent_high = max(closes[-55:]) if len(closes) >= 5 else close
    pivot = recent_high
    distance_to_pivot = ((close - pivot) / pivot) * 100.0 if pivot else None
    bb_pctile = _bb_width_percentile(closes)
    avg50 = mean(volumes[-50:]) if len(volumes) >= 50 else (mean(volumes) if volumes else None)
    volume_vs_50d = volumes[-1] / avg50 if avg50 else None
    rs_new_high_window = None  # filled after RS series is known by caller
    atr_pct = _atr_percent(rows)
    setup_score_parts = [
        max(0.0, 100.0 - abs(distance_to_pivot or 100.0) * 5.0) if distance_to_pivot is not None else None,
        max(0.0, 100.0 - (bb_pctile or 100.0)) if bb_pctile is not None else None,
        min(100.0, (volume_vs_50d or 0.0) * 50.0) if volume_vs_50d is not None else None,
        max(0.0, 100.0 - (atr_pct or 100.0) * 10.0) if atr_pct is not None else None,
    ]
    usable = [value for value in setup_score_parts if value is not None]
    pattern = None
    if bb_pctile is not None and bb_pctile <= 20 and distance_to_pivot is not None and -12 <= distance_to_pivot <= 3:
        pattern = "Squeeze Pivot"
    elif distance_to_pivot is not None and -5 <= distance_to_pivot <= 2:
        pattern = "Near Pivot"
    elif volume_vs_50d is not None and volume_vs_50d >= 1.5 and close >= max(closes[-10:]):
        pattern = "Volume Breakout"
    return {
        "se_setup_score": _round(mean(usable), 1) if usable else None,
        "se_pattern_primary": pattern,
        "se_distance_to_pivot_pct": _round(distance_to_pivot, 1),
        "se_bb_width_pctile_252": _round(bb_pctile, 0),
        "se_volume_vs_50d": _round(volume_vs_50d, 2),
        "se_pivot_price": _round(pivot, 2),
        "se_rs_line_new_high": rs_new_high_window,
        "vcp_score": _round(max(0.0, 100.0 - (bb_pctile or 100.0)), 1) if bb_pctile is not None else None,
        "vcp_pivot": _round(pivot, 2),
        "vcp_detected": bool(bb_pctile is not None and bb_pctile <= 25 and distance_to_pivot is not None and distance_to_pivot >= -15),
        "vcp_ready_for_breakout": bool(distance_to_pivot is not None and -5 <= distance_to_pivot <= 2 and (volume_vs_50d or 0) >= 0.8),
    }


def build_scan_metrics_artifact(
    *,
    foundation_update: Path,
    daily_price: Path,
    output_dir: Path,
    min_symbol_coverage: float = 0.8,
) -> dict[str, Any]:
    foundation = _read_json(foundation_update)
    daily = _read_json(daily_price)
    foundation_by_symbol = _foundation_rows(foundation)
    prices_by_symbol = _daily_rows(daily)
    symbols = sorted(foundation_by_symbol)
    spy_rows = prices_by_symbol.get("SPY") or []
    spy_closes = [row["close"] for row in spy_rows]
    spy_returns = _daily_returns(spy_closes)

    returns_1m: dict[str, float | None] = {}
    returns_3m: dict[str, float | None] = {}
    returns_12m: dict[str, float | None] = {}
    raw_rs: dict[str, float | None] = {}
    rows: list[dict[str, Any]] = []

    interim: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        price_rows = prices_by_symbol.get(symbol) or []
        closes = [row["close"] for row in price_rows]
        if len(closes) < 2:
            returns_1m[symbol] = returns_3m[symbol] = returns_12m[symbol] = raw_rs[symbol] = None
            interim[symbol] = {"has_prices": False}
            continue
        r1 = _return(closes, 21)
        r3 = _return(closes, 63)
        r12 = _return(closes, min(252, len(closes) - 1))
        spy_r12 = _return(spy_closes, min(252, len(spy_closes) - 1)) if spy_closes else None
        returns_1m[symbol] = r1
        returns_3m[symbol] = r3
        returns_12m[symbol] = r12
        raw_rs[symbol] = (r12 - spy_r12) if r12 is not None and spy_r12 is not None else r12
        interim[symbol] = {"has_prices": True, "rows": price_rows, "closes": closes}

    rs_rating = _percentile(raw_rs, high_is_good=True)
    rs_1m = _percentile(returns_1m, high_is_good=True)
    rs_3m = _percentile(returns_3m, high_is_good=True)
    rs_12m = _percentile(returns_12m, high_is_good=True)

    for symbol in symbols:
        payload = foundation_by_symbol[symbol]
        info = interim[symbol]
        price_rows = info.get("rows") or []
        closes = info.get("closes") or []
        close = closes[-1] if closes else None
        beta = _beta(_daily_returns(closes), spy_returns) if closes and spy_returns else _num(payload.get("beta"))
        beta_adj_rs = None
        if rs_rating.get(symbol) is not None and beta not in (None, 0):
            beta_adj_rs = float(rs_rating[symbol]) / abs(float(beta))
        min_score, passes_template = _minervini_score(closes, rs_rating.get(symbol)) if closes else (None, None)
        setup = _setup_metrics(price_rows) if price_rows else {}
        if price_rows and spy_rows:
            setup["se_rs_line_new_high"] = _rs_line_new_high(price_rows, spy_rows)
        volumes = [row.get("volume") or 0.0 for row in price_rows]
        avg50 = mean(volumes[-50:]) if len(volumes) >= 50 else (mean(volumes) if volumes else None)
        volume_vs_50 = volumes[-1] / avg50 if avg50 else None
        volume_breakthrough_score = min(100.0, (volume_vs_50 or 0.0) * 50.0) if volume_vs_50 is not None else None
        sma50 = _sma(closes, 50) if closes else None
        sma150 = _sma(closes, 150) if closes else None
        sma200 = _sma(closes, 200) if closes else None
        stage = _stage(close, sma50, sma150, sma200) if close is not None else None
        eps_growth = _num(payload.get("eps_growth_qq"))
        sales_growth = _num(payload.get("sales_growth_qq"))
        eps_rating = None
        if eps_growth is not None:
            eps_rating = max(1.0, min(99.0, 50.0 + eps_growth / 2.0))
        canslim_parts = [rs_rating.get(symbol), eps_rating, min_score]
        if sales_growth is not None:
            canslim_parts.append(max(1.0, min(99.0, 50.0 + sales_growth / 2.0)))
        canslim_usable = [float(value) for value in canslim_parts if value is not None]
        canslim_score = mean(canslim_usable) if canslim_usable else None
        ipo_score = None
        if payload.get("ipo_date") or payload.get("first_trade_date"):
            ipo_score = mean([value for value in [rs_rating.get(symbol), min_score, canslim_score] if value is not None]) if any(v is not None for v in [rs_rating.get(symbol), min_score, canslim_score]) else None
        custom_parts = [rs_rating.get(symbol), min_score, setup.get("se_setup_score"), volume_breakthrough_score]
        custom_usable = [float(value) for value in custom_parts if value is not None]
        custom_score = mean(custom_usable) if custom_usable else None
        composite_parts = [canslim_score, min_score, setup.get("se_setup_score"), rs_rating.get(symbol), custom_score]
        composite_usable = [float(value) for value in composite_parts if value is not None]
        composite = mean(composite_usable) if composite_usable else None
        adr_percent = _adr_percent(price_rows) if price_rows else None
        rating_score = composite
        rating = _rating_label(rating_score, metrics_available=bool(price_rows))
        rows.append(
            {
                "symbol": symbol,
                "metrics_available": bool(price_rows),
                "composite_score": _round(composite, 1),
                "rating_score": _round(rating_score, 1),
                "rating": rating,
                "minervini_score": _round(min_score, 1),
                "canslim_score": _round(canslim_score, 1),
                "ipo_score": _round(ipo_score, 1),
                "custom_score": _round(custom_score, 1),
                "volume_breakthrough_score": _round(volume_breakthrough_score, 1),
                "rs_rating": rs_rating.get(symbol),
                "rs_rating_1m": rs_1m.get(symbol),
                "rs_rating_3m": rs_3m.get(symbol),
                "rs_rating_12m": rs_12m.get(symbol),
                "adr_percent": _round(adr_percent, 2),
                "beta": _round(beta, 2),
                "beta_adj_rs": _round(beta_adj_rs, 1),
                "eps_rating": _round(eps_rating, 0),
                "stage": stage,
                "passes_template": passes_template,
                "ma_alignment": bool(close is not None and sma50 is not None and sma150 is not None and sma200 is not None and close > sma50 > sma150 > sma200),
                "vcp_detected": setup.get("vcp_detected"),
                "vcp_score": setup.get("vcp_score"),
                "vcp_pivot": setup.get("vcp_pivot"),
                "vcp_ready_for_breakout": setup.get("vcp_ready_for_breakout"),
                "se_setup_score": setup.get("se_setup_score"),
                "se_pattern_primary": setup.get("se_pattern_primary"),
                "se_distance_to_pivot_pct": setup.get("se_distance_to_pivot_pct"),
                "se_bb_width_pctile_252": setup.get("se_bb_width_pctile_252"),
                "se_volume_vs_50d": setup.get("se_volume_vs_50d"),
                "se_rs_line_new_high": setup.get("se_rs_line_new_high"),
                "se_pivot_price": setup.get("se_pivot_price"),
            }
        )

    covered = sum(1 for row in rows if row["metrics_available"])
    coverage = covered / len(symbols) if symbols else 1.0
    if coverage < min_symbol_coverage:
        raise ValueError(f"Scan metrics coverage {coverage:.2%} below minimum {min_symbol_coverage:.2%}")
    as_of = str(daily.get("as_of_date") or foundation.get("as_of_date") or datetime.now(timezone.utc).date().isoformat())
    generated_at = _utc_now()
    source_revision = f"scan_metrics_us:artifact:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    bundle_name = f"scan-metrics-us-{as_of.replace('-', '')}.json.gz"
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / bundle_name
    field_coverage = {
        key: round(sum(row.get(key) is not None for row in rows) / len(rows), 6) if rows else 1.0
        for key in [
            "composite_score", "minervini_score", "canslim_score", "custom_score",
            "volume_breakthrough_score", "rs_rating", "rs_rating_1m", "rs_rating_3m",
            "rs_rating_12m", "adr_percent", "rating_score", "beta_adj_rs", "eps_rating", "stage", "se_setup_score",
            "se_distance_to_pivot_pct", "se_bb_width_pctile_252", "se_volume_vs_50d", "se_pivot_price", "se_rs_line_new_high",
        ]
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "market": MARKET,
        "generated_at": generated_at,
        "as_of_date": as_of,
        "source_revision": source_revision,
        "symbol_count": len(rows),
        "symbol_universe_count": len(symbols),
        "covered_symbol_count": covered,
        "symbol_coverage": round(coverage, 6),
        "min_symbol_coverage": min_symbol_coverage,
        "field_coverage": field_coverage,
        "rows": rows,
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
        "symbol_count": len(rows),
        "symbol_universe_count": len(symbols),
        "covered_symbol_count": covered,
        "symbol_coverage": round(coverage, 6),
        "min_symbol_coverage": min_symbol_coverage,
        "field_coverage": field_coverage,
    }
    manifest_path = output_dir / "scan-metrics-latest-us.json"
    _write_json(manifest_path, manifest)
    return {**manifest, "bundle_path": str(bundle_path), "manifest_path": str(manifest_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foundation-update", required=True, type=Path)
    parser.add_argument("--daily-price", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--min-symbol-coverage", type=float, default=0.8)
    args = parser.parse_args()
    summary = build_scan_metrics_artifact(
        foundation_update=args.foundation_update,
        daily_price=args.daily_price,
        output_dir=args.output_dir,
        min_symbol_coverage=args.min_symbol_coverage,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
