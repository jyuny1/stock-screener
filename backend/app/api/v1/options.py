"""Option-chain API endpoints backed by Schwab Market Data."""
from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query

from ...services.schwab_token_service import SchwabTokenService

SCHWAB_MARKETDATA_BASE_URL = "https://api.schwabapi.com/marketdata/v1"

router = APIRouter()


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _safe_int(value: Any) -> int:
    parsed = _safe_float(value)
    return int(parsed) if parsed is not None else 0


def _date_key(value: Any) -> str | None:
    if not value:
        return None
    return str(value)[:10]


def _quote_time_iso(value: Any) -> str | None:
    millis = _safe_float(value)
    if millis is None:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000, UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _flatten_contracts(symbol: str, exp_date_map: Any, option_type: str, *, snapshot_utc: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(exp_date_map, dict):
        return rows
    for exp_key, strikes in exp_date_map.items():
        if not isinstance(strikes, dict):
            continue
        for contracts in strikes.values():
            if not isinstance(contracts, list):
                continue
            for contract in contracts:
                if not isinstance(contract, dict):
                    continue
                bid = _safe_float(contract.get("bid"))
                ask = _safe_float(contract.get("ask"))
                mid = (bid + ask) / 2 if bid is not None and ask is not None else None
                spread_pct = ((ask - bid) / mid * 100) if mid and mid > 0 and ask is not None and bid is not None else None
                strike = _safe_float(contract.get("strikePrice"))
                rows.append(
                    {
                        "snapshotUtc": snapshot_utc,
                        "underlying": symbol,
                        "optionType": option_type,
                        "contractSymbol": contract.get("symbol"),
                        "description": contract.get("description"),
                        "expirationDate": _date_key(contract.get("expirationDate")) or _date_key(str(exp_key).split(":")[0]),
                        "dte": _safe_int(contract.get("daysToExpiration")),
                        "strike": strike,
                        "bid": bid,
                        "ask": ask,
                        "last": _safe_float(contract.get("last")),
                        "mark": _safe_float(contract.get("mark")),
                        "mid": mid,
                        "spreadPct": spread_pct,
                        "bidSize": _safe_int(contract.get("bidSize")),
                        "askSize": _safe_int(contract.get("askSize")),
                        "volume": _safe_int(contract.get("totalVolume")),
                        "openInterest": _safe_int(contract.get("openInterest")),
                        "iv": _safe_float(contract.get("volatility")),
                        "delta": _safe_float(contract.get("delta")),
                        "gamma": _safe_float(contract.get("gamma")),
                        "theta": _safe_float(contract.get("theta")),
                        "vega": _safe_float(contract.get("vega")),
                        "rho": _safe_float(contract.get("rho")),
                        "theoreticalOptionValue": _safe_float(contract.get("theoreticalOptionValue")),
                        "inTheMoney": contract.get("inTheMoney"),
                    }
                )
    return rows


def _get_access_token() -> str:
    import os

    token = os.environ.get("SCHWAB_ACCESS_TOKEN")
    if token:
        return token
    pair = SchwabTokenService.from_env().refresh_from_env()
    os.environ["SCHWAB_ACCESS_TOKEN"] = pair.access_token
    os.environ["SCHWAB_REFRESH_TOKEN"] = pair.new_refresh_token
    return pair.access_token


def _fetch_schwab_chain(symbol: str, *, include_underlying_quote: bool = True) -> dict[str, Any]:
    token = _get_access_token()
    params = {
        "symbol": symbol,
        "contractType": "ALL",
        "strategy": "SINGLE",
        "includeUnderlyingQuote": str(include_underlying_quote).lower(),
        "optionType": "ALL",
    }
    response = requests.get(
        f"{SCHWAB_MARKETDATA_BASE_URL}/chains",
        params=params,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    if response.status_code == 401:
        # Best effort refresh for expired access tokens when refresh credentials exist.
        try:
            pair = SchwabTokenService.from_env().refresh_from_env()
        except Exception:
            response.raise_for_status()
        import os

        os.environ["SCHWAB_ACCESS_TOKEN"] = pair.access_token
        os.environ["SCHWAB_REFRESH_TOKEN"] = pair.new_refresh_token
        response = requests.get(
            f"{SCHWAB_MARKETDATA_BASE_URL}/chains",
            params=params,
            headers={"Authorization": f"Bearer {pair.access_token}", "Accept": "application/json"},
            timeout=30,
        )
    response.raise_for_status()
    return response.json()


@router.get("/{symbol}/chain")
def get_option_chain(
    symbol: str,
    include_underlying_quote: bool = Query(True, alias="includeUnderlyingQuote"),
) -> dict[str, Any]:
    """Return a flattened current Schwab option chain for one symbol."""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol or len(normalized_symbol) > 12 or not normalized_symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid symbol")

    snapshot_utc = datetime.now(UTC).isoformat()
    try:
        payload = _fetch_schwab_chain(normalized_symbol, include_underlying_quote=include_underlying_quote)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = "Schwab option-chain request failed"
        if status_code == 404:
            detail = f"No Schwab option chain found for {normalized_symbol}"
        elif status_code == 401:
            detail = "Schwab authorization failed"
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Schwab option-chain request failed: {type(exc).__name__}") from exc

    underlying = payload.get("underlying") if isinstance(payload.get("underlying"), dict) else {}
    contracts = [
        *_flatten_contracts(normalized_symbol, payload.get("callExpDateMap"), "CALL", snapshot_utc=snapshot_utc),
        *_flatten_contracts(normalized_symbol, payload.get("putExpDateMap"), "PUT", snapshot_utc=snapshot_utc),
    ]
    expirations = sorted({row["expirationDate"] for row in contracts if row.get("expirationDate")})
    return {
        "symbol": normalized_symbol,
        "status": payload.get("status"),
        "strategy": payload.get("strategy"),
        "snapshotUtc": snapshot_utc,
        "underlying": {
            "symbol": normalized_symbol,
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
