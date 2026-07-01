"""Support/resistance API endpoints for Bobble option-chain overlays."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from ...services.support_resistance_service import calculate_support_resistance_levels
from .stocks import _load_price_history

router = APIRouter()


def _parse_strikes(raw: str | None) -> list[float] | None:
    if not raw:
        return None
    strikes: list[float] = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            strikes.append(float(value))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid strike value: {value}") from exc
    return strikes or None


@router.get("/{symbol}")
def get_support_resistance(
    symbol: str,
    period: Annotated[str, Query(description="Cached price-history period, e.g. 6mo, 1y, 2y, 5y")] = "1y",
    min_history_bars: Annotated[int, Query(alias="minHistoryBars", ge=30, le=1500)] = 120,
    merge_percent: Annotated[float, Query(alias="mergePercent", ge=0.05, le=5.0)] = 0.5,
    merge_atr_multiplier: Annotated[float, Query(alias="mergeAtrMultiplier", ge=0.05, le=5.0)] = 0.5,
    zigzag_min_reversal_pct: Annotated[float, Query(alias="zigzagMinReversalPct", ge=0.1, le=50.0)] = 3.0,
    zigzag_atr_multiplier: Annotated[float, Query(alias="zigzagAtrMultiplier", ge=0.1, le=20.0)] = 1.5,
    min_strength: Annotated[int, Query(alias="minStrength", ge=0, le=100)] = 40,
    max_levels_per_side: Annotated[int, Query(alias="maxLevelsPerSide", ge=1, le=25)] = 8,
    strikes: Annotated[str | None, Query(description="Comma-separated option strikes for nearest-strike annotation")] = None,
) -> dict:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise HTTPException(status_code=422, detail="Symbol is required")

    history = _load_price_history(normalized_symbol, period=period)
    result = calculate_support_resistance_levels(
        history,
        min_history_bars=min_history_bars,
        merge_percent=merge_percent,
        merge_atr_multiplier=merge_atr_multiplier,
        zigzag_min_reversal_pct=zigzag_min_reversal_pct,
        zigzag_atr_multiplier=zigzag_atr_multiplier,
        min_strength=min_strength,
        max_levels_per_side=max_levels_per_side,
        nearest_strikes=_parse_strikes(strikes),
    )
    return {"symbol": normalized_symbol, **result}
