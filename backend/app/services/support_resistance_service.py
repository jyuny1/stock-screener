"""ATR-aware support/resistance level detection for option-chain overlays.

The implementation intentionally does not vendor or depend on
``day0market/support_resistance``.  It keeps the useful idea (price pivots
clustered into horizontal levels) but adapts it for this project:

* multiple pivot sources (local extrema, percent/ATR zig-zag, volume zones)
* volatility-aware one-dimensional clustering
* deterministic strength scoring suitable for API responses and charts
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd

LevelType = Literal["support", "resistance"]


@dataclass(frozen=True)
class _Candidate:
    price: float
    level_type: LevelType
    source: str
    index: int
    date: str | None
    volume: float
    weight: float
    bounce_pct: float


@dataclass(frozen=True)
class _ScoredCluster:
    price: float
    level_type: LevelType
    touch_count: int
    last_touched_date: str | None
    sources: list[str]
    zone_low: float
    zone_high: float
    raw_volume: float
    avg_bounce_pct: float
    touch_score: float
    recency_score: float
    bounce_score: float
    clean_level_score: float
    body_cut_count: int
    wick_cut_count: int


DEFAULT_PIVOT_WINDOWS = (11, 21, 63)
DEFAULT_MIN_HISTORY_BARS = 120


def calculate_support_resistance_levels(
    history: Sequence[dict[str, Any]],
    *,
    pivot_windows: Sequence[int] = DEFAULT_PIVOT_WINDOWS,
    min_history_bars: int = DEFAULT_MIN_HISTORY_BARS,
    merge_percent: float = 0.5,
    merge_atr_multiplier: float = 0.5,
    zigzag_min_reversal_pct: float = 3.0,
    zigzag_atr_multiplier: float = 1.5,
    min_strength: int = 40,
    max_levels_per_side: int = 8,
    nearest_strikes: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Return support/resistance levels from OHLCV history.

    ``history`` accepts the JSON shape returned by ``/stocks/{symbol}/history``
    (lowercase OHLCV fields).  The response uses frontend-friendly camelCase
    keys because BobblePage consumes it directly.
    """

    df = _normalize_history(history)
    warnings: list[str] = []
    bar_count = int(len(df))

    if bar_count == 0:
        return _empty_response("insufficient_history", warnings=["empty_history"])

    as_of = _date_at(df, bar_count - 1)
    spot = _safe_float(df["close"].iloc[-1])
    atr = _latest_atr(df)
    atr_pct = (atr / spot * 100.0) if spot and spot > 0 else None

    if bar_count < 30:
        return _empty_response(
            "insufficient_history",
            spot=spot,
            as_of=as_of,
            bar_count=bar_count,
            atr=atr,
            atr_pct=atr_pct,
            warnings=[*warnings, f"history_bars_lt_30:{bar_count}"],
        )

    status = "ok"
    if bar_count < min_history_bars:
        status = "degraded"
        warnings.append(f"history_bars_lt_min:{bar_count}<{min_history_bars}")

    tolerance = _cluster_tolerance(spot or float(df["close"].median()), atr, merge_percent, merge_atr_multiplier)
    candidates = [
        *_local_extreme_candidates(df, pivot_windows),
        *_zigzag_candidates(
            df,
            atr_pct=atr_pct,
            min_reversal_pct=zigzag_min_reversal_pct,
            atr_multiplier=zigzag_atr_multiplier,
        ),
        *_volume_zone_candidates(df, spot=spot, tolerance=tolerance),
    ]

    if not candidates:
        return _empty_response(
            "degraded",
            spot=spot,
            as_of=as_of,
            bar_count=bar_count,
            atr=atr,
            atr_pct=atr_pct,
            warnings=[*warnings, "no_level_candidates"],
        )

    clusters = [
        *_score_clusters(
            df,
            _cluster_candidates([c for c in candidates if c.level_type == "support"], atr, merge_percent, merge_atr_multiplier),
            spot=spot,
            atr=atr,
            atr_pct=atr_pct,
        ),
        *_score_clusters(
            df,
            _cluster_candidates([c for c in candidates if c.level_type == "resistance"], atr, merge_percent, merge_atr_multiplier),
            spot=spot,
            atr=atr,
            atr_pct=atr_pct,
        ),
    ]

    max_raw_volume = max((cluster.raw_volume for cluster in clusters), default=0.0)
    levels = [
        _cluster_to_payload(
            cluster,
            spot=spot,
            max_raw_volume=max_raw_volume,
            nearest_strikes=nearest_strikes,
        )
        for cluster in clusters
    ]
    levels = [level for level in levels if level["strength"] >= min_strength]

    support_levels = _trim_side(levels, "support", max_levels_per_side)
    resistance_levels = _trim_side(levels, "resistance", max_levels_per_side)
    combined_levels = sorted([*support_levels, *resistance_levels], key=lambda item: item["strength"], reverse=True)

    if not combined_levels:
        status = "degraded"
        warnings.append("no_levels_after_strength_filter")

    return {
        "status": status,
        "spot": _round_or_none(spot),
        "asOf": as_of,
        "barCount": bar_count,
        "atr14": _round_or_none(atr),
        "atrPct": _round_or_none(atr_pct),
        "levels": combined_levels,
        "supportLevels": support_levels,
        "resistanceLevels": resistance_levels,
        "config": {
            "pivotWindows": list(pivot_windows),
            "minHistoryBars": min_history_bars,
            "mergePercent": merge_percent,
            "mergeAtrMultiplier": merge_atr_multiplier,
            "zigzagMinReversalPct": zigzag_min_reversal_pct,
            "zigzagAtrMultiplier": zigzag_atr_multiplier,
            "minStrength": min_strength,
            "maxLevelsPerSide": max_levels_per_side,
        },
        "warnings": warnings,
    }


def calculate_operational_support_resistance_levels(
    history: Sequence[dict[str, Any]],
    *,
    strikes: Sequence[float] | None = None,
    quote_spot: float | None = None,
) -> dict[str, Any]:
    """Return Bobble/SP-analysis compatible support and resistance levels.

    This is the shared version of the local Bobble page algorithm.  It keeps a
    deliberately explainable model for short-dated option analysis:

    * confirmed structures: 3-bar local extrema clustered in one dimension;
    * tactical references: recent rolling lows/highs for short-DTE strike checks;
    * operational filtering: supports below spot and resistances above spot.

    The output shape is stable for the HTML overlay and for sell-put analysis
    reports.  Tactical references are explicitly marked as unconfirmed so they
    are not mistaken for real support/resistance.
    """

    df = _normalize_history(history)
    rows = _df_to_ohlcv_rows(df)
    if len(rows) < 30:
        return {
            "status": "insufficient_history",
            "levels": [],
            "supportLevels": [],
            "resistanceLevels": [],
            "confirmedStructures": [],
            "historicalStructures": [],
            "tacticalReferences": [],
            "operationalTacticalReferences": [],
            "operationalFilter": {"supportMinDistancePct": -45.0, "resistanceMaxDistancePct": 45.0},
            "barCount": len(rows),
            "warnings": ["history_bars_lt_30"],
        }

    daily_spot = float(rows[-1]["close"])
    reference_spot = float(quote_spot or daily_spot)
    atr = _operational_atr(rows) or max(daily_spot * 0.005, 0.01)
    atr_pct = atr / reference_spot * 100 if reference_spot else 0.0
    ranges = [float(row["high"]) - float(row["low"]) for row in rows[-20:]]
    adr20 = _median(ranges) if ranges else atr
    adr20_pct = adr20 / reference_spot * 100 if reference_spot else 0.0
    recent_20 = rows[-20:]
    recent_60 = rows[-60:] if len(rows) >= 60 else rows
    return_20 = reference_spot / float(recent_20[0]["close"]) - 1 if recent_20 else 0.0
    return_60 = reference_spot / float(recent_60[0]["close"]) - 1 if recent_60 else 0.0
    high_velocity = adr20_pct >= 5.0 or atr_pct >= 8.0 or abs(return_20) >= 0.35 or abs(return_60) >= 1.0
    tolerance = max(0.01, min(0.55 * adr20, 0.04 * reference_spot))
    max_width = max(tolerance, min(1.2 * adr20, 0.07 * reference_spot))
    warnings: list[str] = []
    if high_velocity:
        warnings.append("high_velocity_asset")
    if quote_spot and daily_spot:
        quote_daily_gap_pct = abs(reference_spot / daily_spot - 1) * 100
        if quote_daily_gap_pct >= max(3.0, adr20_pct):
            warnings.append("quote_daily_spot_gap")

    support_points = _strict_3bar_extrema(rows, "low", "min")
    resistance_points = _strict_3bar_extrema(rows, "high", "max")
    support_zones = _cluster_operational_points(support_points, tolerance=tolerance, max_width=max_width, min_touches=2)
    resistance_zones = _cluster_operational_points(resistance_points, tolerance=tolerance, max_width=max_width, min_touches=2)

    confirmed: list[dict[str, Any]] = []
    for zone in support_zones:
        confirmed.append(_make_operational_confirmed_level(zone, level_type="support", rows_len=len(rows), daily_spot=daily_spot, quote_spot=quote_spot, reference_spot=reference_spot, strikes=strikes))
    for zone in resistance_zones:
        confirmed.append(_make_operational_confirmed_level(zone, level_type="resistance", rows_len=len(rows), daily_spot=daily_spot, quote_spot=quote_spot, reference_spot=reference_spot, strikes=strikes))

    tactical: list[dict[str, Any]] = []
    for window in (2, 3, 5, 10, 20, 30, 45, 60):
        for level_type in ("support", "resistance"):
            ref = _make_operational_tactical_reference(rows=rows, window=window, level_type=level_type, daily_spot=daily_spot, quote_spot=quote_spot, reference_spot=reference_spot, strikes=strikes)
            if ref is not None:
                tactical.append(ref)

    support_min_distance_pct = -45.0
    resistance_max_distance_pct = 45.0

    def sort_key(level: dict[str, Any]) -> tuple[int, float, float]:
        confirmed_rank = 0 if level.get("kind") == "confirmed_structure" else 1
        distance = abs(float(level.get("distanceToSpotPct") or 0.0))
        score = float(level.get("confirmationScore") or level.get("displayPriority") or 0.0)
        return (confirmed_rank, distance, -score)

    def is_operational(level: dict[str, Any]) -> bool:
        distance = float(level.get("distanceToSpotPct") or 0.0)
        if level.get("type") == "support":
            return support_min_distance_pct <= distance <= 0.0
        if level.get("type") == "resistance":
            return 0.0 <= distance <= resistance_max_distance_pct
        return False

    all_confirmed = _dedupe_operational_levels(confirmed, sort_key=sort_key)
    all_tactical = _dedupe_operational_levels(tactical, sort_key=sort_key)
    operational_confirmed = [level for level in all_confirmed if is_operational(level)]
    historical_structures = [level for level in all_confirmed if not is_operational(level)]
    operational_tactical = [level for level in all_tactical if is_operational(level)]

    levels = sorted([*operational_confirmed, *operational_tactical], key=sort_key)
    support_levels = [level for level in levels if level["type"] == "support"][:12]
    resistance_levels = [level for level in levels if level["type"] == "resistance"][:8]

    status = "ok" if operational_confirmed else "no_operational_confirmed_structure"
    if not operational_confirmed:
        warnings.append("no_operational_confirmed_support_resistance")
    if historical_structures:
        warnings.append("historical_structures_hidden")

    return {
        "status": status,
        "spot": round(reference_spot, 2),
        "dailySpot": round(daily_spot, 2),
        "quoteSpot": round(quote_spot, 2) if quote_spot else None,
        "asOf": rows[-1].get("date"),
        "barCount": len(rows),
        "atr14": round(atr, 2),
        "atrPct": round(atr_pct, 2),
        "adr20": round(adr20, 2),
        "adr20Pct": round(adr20_pct, 2),
        "highVelocity": high_velocity,
        "clusterTolerance": round(tolerance, 2),
        "clusterMaxWidth": round(max_width, 2),
        "levels": [*support_levels, *resistance_levels],
        "supportLevels": support_levels,
        "resistanceLevels": resistance_levels,
        "confirmedStructures": operational_confirmed,
        "historicalStructures": historical_structures,
        "tacticalReferences": all_tactical,
        "operationalTacticalReferences": operational_tactical,
        "operationalFilter": {"supportMinDistancePct": support_min_distance_pct, "resistanceMaxDistancePct": resistance_max_distance_pct},
        "pivotCounts": {"support": len(support_points), "resistance": len(resistance_points)},
        "warnings": warnings,
    }


def calculate_intraday_tactical_support_levels(
    intraday_history: Sequence[dict[str, Any]],
    *,
    quote_spot: float | None = None,
    daily_atr: float | None = None,
    trading_days: int = 10,
) -> dict[str, Any]:
    """Return tactical support zones from recent 1-minute OHLCV rows.

    This model is deliberately separate from the daily support model.  Minute
    bars are used only for tactical zones: pivot-low clusters, session/opening
    lows, last-hour lows, and volume-at-price nodes.  ATR is used as a filter
    to downgrade current-price noise and overly broad zones.
    """

    rows = _normalize_intraday_history(intraday_history)
    if not rows:
        return {
            "status": "insufficient_intraday_history",
            "barCount": 0,
            "zones": [],
            "warnings": ["empty_intraday_history"],
        }

    dates = sorted({str(row["date"]) for row in rows})
    keep_dates = set(dates[-trading_days:]) if trading_days > 0 else set(dates)
    rows = [row for row in rows if row["date"] in keep_dates]
    if len(rows) < 120:
        return {
            "status": "insufficient_intraday_history",
            "barCount": len(rows),
            "zones": [],
            "warnings": [f"intraday_bars_lt_120:{len(rows)}"],
        }

    for idx, row in enumerate(rows):
        row["idx"] = idx
    spot = float(quote_spot or rows[-1]["close"])
    minute_tr = _intraday_true_ranges(rows)
    median_1m_tr = _median(minute_tr)
    p90_1m_tr = _percentile(minute_tr, 0.90)
    atr = float(daily_atr or max(median_1m_tr * 26.0, spot * 0.04, 0.01))
    base_tolerance = max(1.0, spot * 0.0075, median_1m_tr * 2.0)
    max_zone_width = max(atr * 0.35, p90_1m_tr * 5.0)

    points = [
        *_intraday_pivot_points(rows),
        *_intraday_session_points(rows),
        *_intraday_volume_points(rows, spot=spot, bucket_size=max(0.5, round(max(median_1m_tr, spot * 0.003) / 0.5) * 0.5)),
    ]
    clusters = _cluster_intraday_points(points, tolerance=base_tolerance, spot=spot)
    zones = [
        _intraday_cluster_to_zone(cluster, rows_len=len(rows), spot=spot, daily_atr=atr, max_zone_width=max_zone_width)
        for cluster in clusters
    ]
    zones = [zone for zone in zones if zone is not None]
    zones = sorted(zones, key=lambda item: (-float(item["score"]), abs(float(item["distanceToSpotPct"]))))

    latest_date = rows[-1]["date"]
    latest_day = [row for row in rows if row["date"] == latest_date]
    return {
        "status": "ok" if zones else "no_intraday_zones",
        "spot": round(spot, 2),
        "asOf": latest_date,
        "barCount": len(rows),
        "dateCount": len(set(row["date"] for row in rows)),
        "firstDate": rows[0]["date"],
        "lastDate": latest_date,
        "dailyAtrUsed": round(atr, 2),
        "dailyAtrPct": round(atr / spot * 100.0, 2) if spot else None,
        "median1mTrueRange": round(median_1m_tr, 3),
        "p90_1mTrueRange": round(p90_1m_tr, 3),
        "clusterTolerance": round(base_tolerance, 2),
        "maxZoneWidth": round(max_zone_width, 2),
        "latestDayLow": round(min(float(row["low"]) for row in latest_day), 2) if latest_day else None,
        "latestDayHigh": round(max(float(row["high"]) for row in latest_day), 2) if latest_day else None,
        "zones": zones[:12],
        "warnings": [],
    }


def merge_daily_intraday_support_context(
    daily_support: dict[str, Any],
    intraday_support: dict[str, Any],
    *,
    quote_spot: float | None = None,
) -> dict[str, Any]:
    """Merge daily structural levels and intraday tactical zones."""

    spot = float(quote_spot or intraday_support.get("spot") or daily_support.get("spot") or 0.0)
    daily_levels = [
        level for level in daily_support.get("supportLevels", [])
        if level.get("kind") == "confirmed_structure" and _safe_float(level.get("price")) is not None
    ]
    intraday_zones = [
        zone for zone in intraday_support.get("zones", [])
        if _safe_float(zone.get("price")) is not None
    ]
    merged: list[dict[str, Any]] = []
    for zone in intraday_zones:
        z_low = _safe_float(zone.get("zoneLow")) or _safe_float(zone.get("price")) or 0.0
        z_high = _safe_float(zone.get("zoneHigh")) or _safe_float(zone.get("price")) or z_low
        overlapping_daily = [
            level for level in daily_levels
            if z_low <= float(level["price"]) <= z_high
            or abs(float(level["price"]) - float(zone["price"])) <= max(float(intraday_support.get("clusterTolerance") or 0.0), 1.0)
        ]
        role = _zone_role(zone, spot=spot, has_daily_overlap=bool(overlapping_daily))
        merged.append({
            **zone,
            "role": role,
            "dailyConfluence": [
                {
                    "price": level.get("price"),
                    "kind": level.get("kind"),
                    "confirmationScore": level.get("confirmationScore"),
                    "lastTouchedDate": level.get("lastTouchedDate"),
                }
                for level in overlapping_daily[:3]
            ],
        })
    return {
        "status": "ok" if merged else "no_merged_support_zones",
        "spot": round(spot, 2) if spot else None,
        "daily": daily_support,
        "intraday": intraday_support,
        "mergedZones": merged,
    }


def classify_sell_put_support_buckets(support_context: dict[str, Any]) -> dict[str, Any]:
    """Compress merged support zones into Sell Put decision buckets."""

    zones = support_context.get("mergedZones", [])
    buckets: list[dict[str, Any]] = []
    for zone in zones:
        role = str(zone.get("role") or "")
        classification = _sell_put_bucket_classification(role, zone)
        buckets.append({
            "range": f"{zone['zoneLow']:.2f}-{zone['zoneHigh']:.2f}",
            "center": zone["price"],
            "classification": classification,
            "role": role,
            "distanceToSpotPct": zone.get("distanceToSpotPct"),
            "score": zone.get("score"),
            "reason": _sell_put_bucket_reason(classification, role, zone),
        })
    priority = {"avoid": 0, "watch": 1, "conditional_sell": 2, "conservative_sell": 3, "tail_reference": 4}
    buckets = sorted(buckets, key=lambda item: (priority.get(str(item["classification"]), 9), -float(item.get("score") or 0)))
    return {
        "status": "ok" if buckets else "no_sell_put_buckets",
        "spot": support_context.get("spot"),
        "sellPutSupportBuckets": buckets,
    }


def annotate_sell_put_support_context(
    contract: dict[str, Any],
    support_payload: dict[str, Any],
    *,
    strike_key: str = "strike",
    bid_key: str = "bid",
) -> dict[str, Any]:
    """Attach support-aware context to one Sell Put candidate.

    The function is intentionally data-shape agnostic so D1 rows, Schwab live
    chain rows, and report renderers can reuse it.  It only consumes the shared
    operational support/resistance payload and the contract strike/bid.
    """

    strike = _safe_float(contract.get(strike_key))
    bid = _safe_float(contract.get(bid_key)) or 0.0
    spot = _safe_float(support_payload.get("spot"))
    breakeven = strike - bid if strike is not None else None
    confirmed_supports = [
        level for level in support_payload.get("supportLevels", [])
        if level.get("kind") == "confirmed_structure" and _safe_float(level.get("price")) is not None
    ]
    tactical_supports = [
        level for level in support_payload.get("supportLevels", [])
        if level.get("kind") == "tactical_reference" and _safe_float(level.get("price")) is not None
    ]

    nearest_confirmed = _nearest_level_to_price(strike, confirmed_supports)
    nearest_tactical = _nearest_level_to_price(strike, tactical_supports)
    strike_relation = _level_relation(strike, nearest_confirmed)
    breakeven_relation = _level_relation(breakeven, nearest_confirmed)
    support_score = _sell_put_support_score(strike_relation, breakeven_relation, support_payload)

    flags: list[str] = []
    if support_payload.get("highVelocity"):
        flags.append("high_velocity_asset")
    if not confirmed_supports:
        flags.append("no_confirmed_support")
    elif strike_relation.get("relation") == "above":
        flags.append("strike_above_confirmed_support")
    if breakeven_relation.get("relation") == "above":
        flags.append("breakeven_above_confirmed_support")

    return {
        "supportAsOf": support_payload.get("asOf"),
        "supportSpot": spot,
        "nearestConfirmedSupport": nearest_confirmed,
        "nearestTacticalSupport": nearest_tactical,
        "strikeVsConfirmedSupport": strike_relation,
        "breakevenVsConfirmedSupport": breakeven_relation,
        "supportScore": support_score,
        "supportRiskFlags": flags,
    }


def _normalize_intraday_history(history: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in history:
        try:
            raw_dt = str(row.get("datetime_et") or row.get("datetime") or row.get("date") or "")
            date = str(row.get("date") or raw_dt[:10])
            time = raw_dt[11:16] if len(raw_dt) >= 16 else str(row.get("time") or "")[:5]
            rows.append({
                "date": date,
                "time": time,
                "datetime": raw_dt,
                "open": float(row.get("open")),
                "high": float(row.get("high")),
                "low": float(row.get("low")),
                "close": float(row.get("close")),
                "volume": float(row.get("volume") or 0.0),
            })
        except (TypeError, ValueError):
            continue
    return sorted(rows, key=lambda item: (item["date"], item["time"], item["datetime"]))


def _intraday_true_ranges(rows: Sequence[dict[str, Any]]) -> list[float]:
    previous_close: float | None = None
    out: list[float] = []
    for row in rows:
        high = float(row["high"])
        low = float(row["low"])
        values = [high - low]
        if previous_close is not None:
            values.extend([abs(high - previous_close), abs(low - previous_close)])
        out.append(max(values))
        previous_close = float(row["close"])
    return out


def _percentile(values: Sequence[float], q: float) -> float:
    clean = sorted(float(value) for value in values if _safe_float(value) is not None)
    if not clean:
        return 0.0
    index = min(len(clean) - 1, max(0, int((len(clean) - 1) * q)))
    return clean[index]


def _intraday_pivot_points(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for half_window, source in ((2, "pivot_low_5m"), (7, "pivot_low_15m"), (14, "pivot_low_29m")):
        if len(rows) < half_window * 2 + 1:
            continue
        for index in range(half_window, len(rows) - half_window):
            low = float(rows[index]["low"])
            segment = [float(rows[i]["low"]) for i in range(index - half_window, index + half_window + 1)]
            if low == min(segment) and segment.count(low) == 1:
                points.append(_intraday_point(rows[index], price=low, source=source, idx=index))
    return points


def _intraday_session_points(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    dates = sorted({str(row["date"]) for row in rows})
    for date in dates:
        day = [row for row in rows if row["date"] == date]
        segments = [
            ("session_low", day),
            ("opening_range_low", [row for row in day if "09:30" <= str(row.get("time") or "") < "10:00"]),
            ("last_hour_low", [row for row in day if "15:00" <= str(row.get("time") or "") <= "16:00"]),
        ]
        for source, segment in segments:
            if not segment:
                continue
            row = min(segment, key=lambda item: float(item["low"]))
            points.append(_intraday_point(row, price=float(row["low"]), source=source, idx=int(row.get("idx") or 0)))
    return points


def _intraday_volume_points(rows: Sequence[dict[str, Any]], *, spot: float, bucket_size: float) -> list[dict[str, Any]]:
    volume_by_bucket: dict[float, float] = {}
    for row in rows:
        typical = (float(row["high"]) + float(row["low"]) + float(row["close"])) / 3.0
        bucket = round(typical / bucket_size) * bucket_size
        volume_by_bucket[bucket] = volume_by_bucket.get(bucket, 0.0) + float(row.get("volume") or 0.0)
    points: list[dict[str, Any]] = []
    for price, volume in sorted(volume_by_bucket.items(), key=lambda item: item[1], reverse=True)[:12]:
        if price <= spot * 1.02:
            row = rows[-1]
            points.append({
                "price": float(price),
                "idx": len(rows) - 1,
                "date": row.get("date"),
                "time": row.get("time"),
                "source": "volume_zone",
                "volume": volume,
            })
    return points


def _intraday_point(row: dict[str, Any], *, price: float, source: str, idx: int) -> dict[str, Any]:
    return {
        "price": float(price),
        "idx": idx,
        "date": row.get("date"),
        "time": row.get("time"),
        "source": source,
        "volume": float(row.get("volume") or 0.0),
    }


def _cluster_intraday_points(points: Sequence[dict[str, Any]], *, tolerance: float, spot: float) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    for point in sorted(points, key=lambda item: float(item["price"])):
        if float(point["price"]) > spot * 1.03:
            continue
        if not clusters:
            clusters.append([point])
            continue
        center = _median([float(item["price"]) for item in clusters[-1]])
        if abs(float(point["price"]) - center) <= tolerance:
            clusters[-1].append(point)
        else:
            clusters.append([point])
    return clusters


def _intraday_cluster_to_zone(
    cluster: Sequence[dict[str, Any]],
    *,
    rows_len: int,
    spot: float,
    daily_atr: float,
    max_zone_width: float,
) -> dict[str, Any] | None:
    if not cluster:
        return None
    prices = [float(item["price"]) for item in cluster]
    center = _median(prices)
    zone_low = min(prices)
    zone_high = max(prices)
    sources = sorted({str(item.get("source") or "unknown") for item in cluster})
    touches = len({(str(item.get("date")), str(item.get("source"))) for item in cluster})
    recency = max(int(item.get("idx") or 0) for item in cluster) / max(1, rows_len - 1)
    distance_pct = (center / spot - 1.0) * 100.0 if spot else 0.0
    score = min(35.0, touches * 5.0) + min(25.0, len(sources) * 7.0) + 15.0 * recency + max(0.0, 20.0 - abs(distance_pct)) * 0.8
    if "volume_zone" in sources:
        score += 10.0
    else:
        score -= 4.0
    if "session_low" in sources:
        score += 8.0
    flags: list[str] = []
    width = zone_high - zone_low
    if abs(center - spot) < daily_atr * 0.08:
        flags.append("inside_noise_band")
        score -= 8.0
    if width > max_zone_width:
        flags.append("wide_atr_band")
        score -= 10.0
    if "volume_zone" not in sources:
        flags.append("no_volume_profile_confirmation")
    return {
        "price": round(center, 2),
        "zoneLow": round(zone_low, 2),
        "zoneHigh": round(zone_high, 2),
        "type": "support",
        "kind": "intraday_tactical_zone",
        "score": round(max(0.0, min(120.0, score)), 1),
        "strength": round(max(0.0, min(100.0, score)), 1),
        "touchCount": touches,
        "sources": sources,
        "distanceToSpotPct": round(distance_pct, 2),
        "distanceAtr": round((center - spot) / daily_atr, 2) if daily_atr else None,
        "atrWidth": round(width / daily_atr, 2) if daily_atr else None,
        "lastTouchedDateTime": max(f"{item.get('date')} {item.get('time')}" for item in cluster),
        "flags": flags,
        "reason": "1-minute pivot/session/volume cluster with ATR filter",
    }


def _zone_role(zone: dict[str, Any], *, spot: float, has_daily_overlap: bool) -> str:
    flags = set(zone.get("flags") or [])
    distance = float(zone.get("distanceToSpotPct") or 0.0)
    if "inside_noise_band" in flags or abs(distance) <= 1.0:
        return "noise_band"
    if distance > 0:
        return "failed_support_or_resistance"
    if distance >= -5.0:
        return "first_tactical_support"
    if has_daily_overlap:
        return "main_pullback_support"
    if distance >= -12.0:
        return "secondary_tactical_support"
    if distance >= -22.0:
        return "deep_support"
    return "tail_support"


def _sell_put_bucket_classification(role: str, zone: dict[str, Any]) -> str:
    if role in {"noise_band", "failed_support_or_resistance"}:
        return "avoid"
    if role == "first_tactical_support":
        return "watch"
    if role in {"secondary_tactical_support", "main_pullback_support"}:
        return "conditional_sell"
    if role == "deep_support":
        return "conservative_sell"
    return "tail_reference"


def _sell_put_bucket_reason(classification: str, role: str, zone: dict[str, Any]) -> str:
    if classification == "avoid":
        return "太接近現價或已轉壓力，不適合作為 Sell Put 安全邊際"
    if classification == "watch":
        return "第一戰術支撐，仍偏近，需等價格確認守住"
    if classification == "conditional_sell":
        return "支撐距離較合理；仍需搭配權利金、DTE、Delta 與流動性"
    if classification == "conservative_sell":
        return "深層支撐區，較符合高波動 ETF 的緩衝需求"
    return "尾部參考區，可能較保守但權利金需另行確認"


def _empty_response(
    status: str,
    *,
    spot: float | None = None,
    as_of: str | None = None,
    bar_count: int = 0,
    atr: float | None = None,
    atr_pct: float | None = None,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "status": status,
        "spot": _round_or_none(spot),
        "asOf": as_of,
        "barCount": bar_count,
        "atr14": _round_or_none(atr),
        "atrPct": _round_or_none(atr_pct),
        "levels": [],
        "supportLevels": [],
        "resistanceLevels": [],
        "config": {},
        "warnings": list(warnings),
    }


def _df_to_ohlcv_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "date": pd.to_datetime(row["date"]).strftime("%Y-%m-%d"),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0) or 0),
            }
        )
    return rows


def _median(values: Sequence[float]) -> float:
    clean = sorted(float(value) for value in values if _safe_float(value) is not None)
    if not clean:
        return 0.0
    midpoint = len(clean) // 2
    if len(clean) % 2:
        return clean[midpoint]
    return (clean[midpoint - 1] + clean[midpoint]) / 2.0


def _operational_atr(rows: Sequence[dict[str, Any]]) -> float | None:
    previous_close: float | None = None
    true_ranges: list[float] = []
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


def _strict_3bar_extrema(rows: Sequence[dict[str, Any]], column: str, mode: str) -> list[dict[str, Any]]:
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


def _cluster_operational_points(
    points: Sequence[dict[str, Any]],
    *,
    tolerance: float,
    max_width: float,
    min_touches: int,
) -> list[dict[str, Any]]:
    if not points:
        return []
    clusters: list[list[dict[str, Any]]] = []
    sorted_points = sorted(points, key=lambda item: float(item["price"]))
    group = [sorted_points[0]]
    for point in sorted_points[1:]:
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
        out.append(
            {
                "zoneLow": round(min(prices), 2),
                "zoneHigh": round(max(prices), 2),
                "primary": round(_median(prices), 2),
                "touchCount": len(group),
                "firstTouchedDate": ordered_by_time[0].get("date"),
                "lastTouchedDate": ordered_by_time[-1].get("date"),
                "firstIdx": int(ordered_by_time[0]["idx"]),
                "lastIdx": int(ordered_by_time[-1]["idx"]),
                "touchSpanBars": int(ordered_by_time[-1]["idx"]) - int(ordered_by_time[0]["idx"]),
                "members": [{"date": item.get("date"), "price": round(float(item["price"]), 2)} for item in ordered_by_time],
                "sources": sorted({str(item.get("source") or "pivot") for item in group}),
            }
        )
    return sorted(out, key=lambda item: float(item["primary"]))


def _confirmation_score(touch_count: int, span_bars: int, bars_since_last: int) -> int:
    score = 70 + min(18, max(0, touch_count - 2) * 6) + min(8, span_bars // 10)
    if bars_since_last > 126:
        score -= 12
    elif bars_since_last > 63:
        score -= 6
    return int(max(0, min(95, score)))


def _pct_distance(price: float, spot: float | None) -> float | None:
    if not spot:
        return None
    return (price / spot - 1) * 100


def _make_operational_confirmed_level(
    zone: dict[str, Any],
    *,
    level_type: LevelType,
    rows_len: int,
    daily_spot: float,
    quote_spot: float | None,
    reference_spot: float,
    strikes: Sequence[float] | None,
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


def _reference_label(window: int, level_type: LevelType) -> tuple[str, str]:
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


def _make_operational_tactical_reference(
    *,
    rows: Sequence[dict[str, Any]],
    window: int,
    level_type: LevelType,
    daily_spot: float,
    quote_spot: float | None,
    reference_spot: float,
    strikes: Sequence[float] | None,
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


def _dedupe_operational_levels(items: Sequence[dict[str, Any]], *, sort_key: Any) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, float]] = set()
    out: list[dict[str, Any]] = []
    for item in sorted(items, key=sort_key):
        key = (str(item.get("type")), str(item.get("kind")), round(float(item.get("price") or 0.0), 2))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _nearest_level_to_price(price: float | None, levels: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    if price is None or not levels:
        return None

    def distance(level: dict[str, Any]) -> float:
        zone_low = _safe_float(level.get("zoneLow")) or _safe_float(level.get("price")) or 0.0
        zone_high = _safe_float(level.get("zoneHigh")) or _safe_float(level.get("price")) or zone_low
        if zone_low <= price <= zone_high:
            return 0.0
        return min(abs(price - zone_low), abs(price - zone_high))

    level = min(levels, key=distance)
    zone_low = _safe_float(level.get("zoneLow")) or _safe_float(level.get("price"))
    zone_high = _safe_float(level.get("zoneHigh")) or _safe_float(level.get("price"))
    primary = _safe_float(level.get("price"))
    if zone_low is None or zone_high is None or primary is None:
        return None
    return {
        "price": round(primary, 2),
        "zoneLow": round(zone_low, 2),
        "zoneHigh": round(zone_high, 2),
        "confirmationScore": level.get("confirmationScore"),
        "touchCount": level.get("touchCount"),
        "lastTouchedDate": level.get("lastTouchedDate"),
        "kind": level.get("kind"),
        "confirmationLabel": level.get("confirmationLabel"),
    }


def _level_relation(price: float | None, level: dict[str, Any] | None) -> dict[str, Any]:
    if price is None or not level:
        return {"relation": "none", "distanceAbs": None, "distancePctOfPrice": None}
    zone_low = _safe_float(level.get("zoneLow"))
    zone_high = _safe_float(level.get("zoneHigh"))
    primary = _safe_float(level.get("price"))
    if zone_low is None or zone_high is None or primary is None:
        return {"relation": "none", "distanceAbs": None, "distancePctOfPrice": None}
    if zone_low <= price <= zone_high:
        relation = "inside"
        distance = 0.0
    elif price > zone_high:
        relation = "above"
        distance = price - zone_high
    else:
        relation = "below"
        distance = zone_low - price
    return {
        "relation": relation,
        "distanceAbs": round(distance, 2),
        "distancePctOfPrice": round(distance / price * 100, 2) if price else None,
        "distanceToPrimaryPct": round((price / primary - 1) * 100, 2) if primary else None,
    }


def _sell_put_support_score(
    strike_relation: dict[str, Any],
    breakeven_relation: dict[str, Any],
    support_payload: dict[str, Any],
) -> int:
    if strike_relation.get("relation") == "none":
        base = 35
    elif strike_relation.get("relation") == "below":
        base = 82
    elif strike_relation.get("relation") == "inside":
        base = 65
    else:
        base = 45

    if breakeven_relation.get("relation") == "below":
        base += 8
    elif breakeven_relation.get("relation") == "above":
        base -= 10

    if support_payload.get("highVelocity"):
        base -= 8
    return int(max(0, min(100, base)))


def _normalize_history(history: Sequence[dict[str, Any]]) -> pd.DataFrame:
    if not history:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(history).copy()
    df.columns = [str(column).strip().lower() for column in df.columns]
    required = {"date", "high", "low", "close"}
    if not required.issubset(df.columns):
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    if "open" not in df.columns:
        df["open"] = df["close"]
    if "volume" not in df.columns:
        df["volume"] = 0

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=["date", "high", "low", "close"])
    df = df[df["close"] > 0]
    df["volume"] = df["volume"].fillna(0).clip(lower=0)
    df = df.sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]]


def _latest_atr(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None

    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().dropna()
    if not atr.empty:
        value = _safe_float(atr.tail(20).median())
        if value and value > 0:
            return value

    fallback = _safe_float((df["high"] - df["low"]).tail(20).median())
    return fallback if fallback and fallback > 0 else None


def _cluster_tolerance(
    price: float,
    atr: float | None,
    merge_percent: float,
    merge_atr_multiplier: float,
) -> float:
    pct_tolerance = abs(price) * merge_percent / 100.0
    atr_tolerance = (atr or 0.0) * merge_atr_multiplier
    return max(pct_tolerance, atr_tolerance, 0.01)


def _local_extreme_candidates(df: pd.DataFrame, windows: Sequence[int]) -> list[_Candidate]:
    lows = df["low"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    candidates: list[_Candidate] = []

    for raw_window in windows:
        window = max(3, int(raw_window))
        if window % 2 == 0:
            window += 1
        if len(df) < window:
            continue

        half = window // 2
        weight = 1.0 + min(log(window) / log(63), 1.0) * 0.8
        support_source = f"local_low_{window}d"
        resistance_source = f"local_high_{window}d"

        for index in range(half, len(df) - half):
            low_segment = lows[index - half:index + half + 1]
            high_segment = highs[index - half:index + half + 1]
            low_price = lows[index]
            high_price = highs[index]

            if isfinite(low_price) and low_price <= float(np.nanmin(low_segment)):
                candidates.append(_make_candidate(df, index, low_price, "support", support_source, weight))
            if isfinite(high_price) and high_price >= float(np.nanmax(high_segment)):
                candidates.append(_make_candidate(df, index, high_price, "resistance", resistance_source, weight))

    return candidates


def _zigzag_candidates(
    df: pd.DataFrame,
    *,
    atr_pct: float | None,
    min_reversal_pct: float,
    atr_multiplier: float,
) -> list[_Candidate]:
    closes = df["close"].to_numpy(dtype=float)
    if len(closes) < 5:
        return []

    reversal_pct = max(min_reversal_pct, (atr_pct or 0.0) * atr_multiplier)
    reversal = reversal_pct / 100.0
    if reversal <= 0:
        return []

    candidates: list[_Candidate] = []
    high_idx = low_idx = 0
    high_price = low_price = closes[0]
    trend: Literal["up", "down"] | None = None
    extreme_idx = 0
    extreme_price = closes[0]

    for index, price in enumerate(closes[1:], start=1):
        if not isfinite(price) or price <= 0:
            continue

        if trend is None:
            if price > high_price:
                high_price = price
                high_idx = index
            if price < low_price:
                low_price = price
                low_idx = index
            if low_price > 0 and high_price / low_price - 1.0 >= reversal:
                if low_idx < high_idx:
                    candidates.append(_make_candidate(df, low_idx, float(df["low"].iloc[low_idx]), "support", "zigzag_low", 1.8))
                    trend = "up"
                    extreme_idx = high_idx
                    extreme_price = high_price
                else:
                    candidates.append(_make_candidate(df, high_idx, float(df["high"].iloc[high_idx]), "resistance", "zigzag_high", 1.8))
                    trend = "down"
                    extreme_idx = low_idx
                    extreme_price = low_price
            continue

        if trend == "up":
            if price >= extreme_price:
                extreme_price = price
                extreme_idx = index
            elif extreme_price > 0 and (extreme_price - price) / extreme_price >= reversal:
                candidates.append(_make_candidate(df, extreme_idx, float(df["high"].iloc[extreme_idx]), "resistance", "zigzag_high", 1.8))
                trend = "down"
                extreme_idx = index
                extreme_price = price
        else:
            if price <= extreme_price:
                extreme_price = price
                extreme_idx = index
            elif extreme_price > 0 and (price - extreme_price) / extreme_price >= reversal:
                candidates.append(_make_candidate(df, extreme_idx, float(df["low"].iloc[extreme_idx]), "support", "zigzag_low", 1.8))
                trend = "up"
                extreme_idx = index
                extreme_price = price

    return candidates


def _volume_zone_candidates(df: pd.DataFrame, *, spot: float | None, tolerance: float, max_zones: int = 8) -> list[_Candidate]:
    if not spot or spot <= 0 or df["volume"].sum() <= 0:
        return []

    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    bucket_size = max(tolerance, spot * 0.005, 0.01)
    buckets = (typical / bucket_size).round() * bucket_size
    volume_by_bucket = df.groupby(buckets)["volume"].sum().sort_values(ascending=False)
    if volume_by_bucket.empty:
        return []

    threshold = float(volume_by_bucket.quantile(0.75))
    max_volume = float(volume_by_bucket.iloc[0]) or 1.0
    candidates: list[_Candidate] = []

    for bucket, volume in volume_by_bucket[volume_by_bucket >= threshold].head(max_zones).items():
        price = _safe_float(bucket)
        if not price or price <= 0:
            continue
        level_type: LevelType = "support" if price <= spot else "resistance"
        weight = 0.8 + (float(volume) / max_volume) * 1.2
        candidates.append(
            _Candidate(
                price=price,
                level_type=level_type,
                source="volume_zone",
                index=len(df) - 1,
                date=_date_at(df, len(df) - 1),
                volume=float(volume),
                weight=weight,
                bounce_pct=0.0,
            )
        )

    return candidates


def _make_candidate(
    df: pd.DataFrame,
    index: int,
    price: float,
    level_type: LevelType,
    source: str,
    weight: float,
) -> _Candidate:
    return _Candidate(
        price=float(price),
        level_type=level_type,
        source=source,
        index=index,
        date=_date_at(df, index),
        volume=float(df["volume"].iloc[index]) if "volume" in df else 0.0,
        weight=weight,
        bounce_pct=_bounce_pct(df, index, float(price), level_type),
    )


def _bounce_pct(df: pd.DataFrame, index: int, price: float, level_type: LevelType, lookahead: int = 10) -> float:
    if price <= 0:
        return 0.0
    future = df.iloc[index + 1:index + lookahead + 1]
    if future.empty:
        return 0.0
    if level_type == "support":
        future_high = _safe_float(future["high"].max())
        return max(0.0, ((future_high or price) / price - 1.0) * 100.0)
    future_low = _safe_float(future["low"].min())
    return max(0.0, (price / (future_low or price) - 1.0) * 100.0)


def _cluster_candidates(
    candidates: Sequence[_Candidate],
    atr: float | None,
    merge_percent: float,
    merge_atr_multiplier: float,
) -> list[list[_Candidate]]:
    clusters: list[list[_Candidate]] = []
    for candidate in sorted(candidates, key=lambda item: item.price):
        if not clusters:
            clusters.append([candidate])
            continue

        anchor = _weighted_median([item.price for item in clusters[-1]], [item.weight for item in clusters[-1]])
        tolerance = _cluster_tolerance(anchor, atr, merge_percent, merge_atr_multiplier)
        if abs(candidate.price - anchor) <= tolerance:
            clusters[-1].append(candidate)
        else:
            clusters.append([candidate])

    return clusters


def _score_clusters(
    df: pd.DataFrame,
    clusters: Sequence[Sequence[_Candidate]],
    *,
    spot: float | None,
    atr: float | None,
    atr_pct: float | None,
) -> list[_ScoredCluster]:
    scored: list[_ScoredCluster] = []
    for cluster in clusters:
        if not cluster:
            continue
        prices = [candidate.price for candidate in cluster]
        weights = [candidate.weight for candidate in cluster]
        price = _weighted_median(prices, weights)
        level_type = cluster[0].level_type
        non_volume_candidates = [candidate for candidate in cluster if candidate.source != "volume_zone"]
        touch_dates = {candidate.date for candidate in non_volume_candidates if candidate.date}
        touch_count = max(1, len(touch_dates) if touch_dates else len(non_volume_candidates))
        last_touched_date = max(touch_dates, default=None)
        sources = sorted({candidate.source for candidate in cluster})
        raw_volume = sum(candidate.volume for candidate in cluster)
        avg_bounce_pct = _weighted_average([candidate.bounce_pct for candidate in cluster], weights)
        touch_score = min(100.0, touch_count * 20.0)
        recency_score = _recency_score(last_touched_date, _date_at(df, len(df) - 1))
        bounce_denominator = max((atr_pct or 0.0) * 2.0, 3.0)
        bounce_score = min(100.0, avg_bounce_pct / bounce_denominator * 100.0) if bounce_denominator else 0.0
        clean_level_score, body_cut_count, wick_cut_count = _clean_level_score(df, price)

        scored.append(
            _ScoredCluster(
                price=price,
                level_type=level_type,
                touch_count=touch_count,
                last_touched_date=last_touched_date,
                sources=sources,
                zone_low=min(prices),
                zone_high=max(prices),
                raw_volume=raw_volume,
                avg_bounce_pct=avg_bounce_pct,
                touch_score=touch_score,
                recency_score=recency_score,
                bounce_score=bounce_score,
                clean_level_score=clean_level_score,
                body_cut_count=body_cut_count,
                wick_cut_count=wick_cut_count,
            )
        )
    return scored


def _cluster_to_payload(
    cluster: _ScoredCluster,
    *,
    spot: float | None,
    max_raw_volume: float,
    nearest_strikes: Sequence[float] | None,
) -> dict[str, Any]:
    volume_score = min(100.0, cluster.raw_volume / max_raw_volume * 100.0) if max_raw_volume > 0 else 0.0
    strength = (
        cluster.touch_score * 0.35
        + cluster.recency_score * 0.25
        + volume_score * 0.20
        + cluster.bounce_score * 0.10
        + cluster.clean_level_score * 0.10
    )
    nearest_strike, nearest_strike_distance_pct = _nearest_strike(cluster.price, nearest_strikes)
    distance_to_spot_pct = ((cluster.price / spot - 1.0) * 100.0) if spot and spot > 0 else None

    return {
        "price": _round_or_none(cluster.price),
        "type": cluster.level_type,
        "strength": int(round(max(0.0, min(100.0, strength)))),
        "quality": _quality(strength),
        "touchCount": cluster.touch_count,
        "lastTouchedDate": cluster.last_touched_date,
        "distanceToSpotPct": _round_or_none(distance_to_spot_pct),
        "nearestStrike": _round_or_none(nearest_strike),
        "nearestStrikeDistancePct": _round_or_none(nearest_strike_distance_pct),
        "sources": cluster.sources,
        "zoneLow": _round_or_none(cluster.zone_low),
        "zoneHigh": _round_or_none(cluster.zone_high),
        "scoreBreakdown": {
            "touch": int(round(cluster.touch_score)),
            "recency": int(round(cluster.recency_score)),
            "volume": int(round(volume_score)),
            "bounce": int(round(cluster.bounce_score)),
            "cleanLevel": int(round(cluster.clean_level_score)),
        },
        "diagnostics": {
            "avgBouncePct": _round_or_none(cluster.avg_bounce_pct),
            "bodyCutCount": cluster.body_cut_count,
            "wickCutCount": cluster.wick_cut_count,
        },
    }


def _trim_side(levels: Sequence[dict[str, Any]], level_type: LevelType, max_levels: int) -> list[dict[str, Any]]:
    side = [level for level in levels if level.get("type") == level_type]
    side = sorted(side, key=lambda item: item["strength"], reverse=True)
    return side[:max(0, max_levels)]


def _clean_level_score(df: pd.DataFrame, price: float) -> tuple[float, int, int]:
    body_cuts = (
        (df[["open", "close"]].max(axis=1) > price)
        & (df[["open", "close"]].min(axis=1) < price)
    )
    wick_cuts = (~body_cuts) & (df["high"] > price) & (df["low"] < price)
    body_count = int(body_cuts.sum())
    wick_count = int(wick_cuts.sum())
    score = max(0.0, 100.0 - body_count * 8.0 - wick_count * 2.0)
    return score, body_count, wick_count


def _recency_score(last_touched_date: str | None, as_of: str | None) -> float:
    if not last_touched_date or not as_of:
        return 0.0
    touched = pd.to_datetime(last_touched_date, errors="coerce")
    current = pd.to_datetime(as_of, errors="coerce")
    if pd.isna(touched) or pd.isna(current):
        return 0.0
    age_days = max(0, int((current - touched).days))
    if age_days <= 21:
        return 100.0
    if age_days <= 63:
        return 80.0
    if age_days <= 126:
        return 60.0
    if age_days <= 252:
        return 35.0
    return 10.0


def _nearest_strike(price: float, strikes: Sequence[float] | None) -> tuple[float | None, float | None]:
    clean_strikes = [float(strike) for strike in strikes or [] if _safe_float(strike) and float(strike) > 0]
    if not clean_strikes or price <= 0:
        return None, None
    nearest = min(clean_strikes, key=lambda strike: abs(strike - price))
    return nearest, abs(nearest - price) / price * 100.0


def _weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    pairs = sorted((float(value), max(0.0, float(weight))) for value, weight in zip(values, weights, strict=False))
    if not pairs:
        return 0.0
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        return float(np.median([value for value, _ in pairs]))
    midpoint = total / 2.0
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= midpoint:
            return value
    return pairs[-1][0]


def _weighted_average(values: Sequence[float], weights: Sequence[float]) -> float:
    total_weight = sum(max(0.0, float(weight)) for weight in weights)
    if total_weight <= 0:
        return float(np.mean(values)) if values else 0.0
    return sum(float(value) * max(0.0, float(weight)) for value, weight in zip(values, weights, strict=False)) / total_weight


def _quality(strength: float) -> str:
    if strength >= 75:
        return "strong"
    if strength >= 60:
        return "medium"
    return "weak"


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _round_or_none(value: float | None, digits: int = 2) -> float | None:
    if value is None or not isfinite(float(value)):
        return None
    return round(float(value), digits)


def _date_at(df: pd.DataFrame, index: int) -> str | None:
    if df.empty or index < 0 or index >= len(df):
        return None
    value = pd.to_datetime(df["date"].iloc[index], errors="coerce")
    if pd.isna(value):
        return None
    return value.strftime("%Y-%m-%d")
