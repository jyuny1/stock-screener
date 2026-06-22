"""Build US static-site data directly from release artifacts without Postgres.

This exporter is intentionally artifact-native: it reads the published weekly
reference bundle plus an optional daily-price bundle and emits the JSON files
consumed by ``frontend/src/static``. It does not import into Postgres and does
not call external market-data providers.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import math
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from urllib import error, parse, request
from pathlib import Path
from typing import Any

from app.services.preset_screens import PRESET_SCREENS

STATIC_SITE_SCHEMA_VERSION = "static-site-v2"
SCAN_BUNDLE_SCHEMA_VERSION = "static-scan-v1"
SCAN_CHUNK_SIZE = 1000
DEFAULT_MARKET = "US"
DEFAULT_MARKET_DISPLAY = "United States"
SCHWAB_MARKETDATA_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
OPTION_PCR_MIN_DTE = 0
OPTION_PCR_MAX_DTE = 90
OPTION_CHAIN_TRACKING_TOP_N = 500
OPTION_CHAIN_TRACKING_RETENTION_DAYS = 90
OPTION_CHAIN_MAX_FETCH_SYMBOLS = 500
OPTION_PCR_REQUEST_INTERVAL_SECONDS = 0.5


def _log(message: str, **fields: Any) -> None:
    payload = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    suffix = f" {payload}" if payload else ""
    print(f"[static-site] {message}{suffix}", flush=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _git_push_hash() -> str | None:
    for name in ("GIT_PUSH", "GITHUB_SHA", "CF_PAGES_COMMIT_SHA", "VERCEL_GIT_COMMIT_SHA"):
        value = os.environ.get(name)
        if value:
            return value
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _require_schwab_auth_material() -> None:
    if os.environ.get("SCHWAB_ACCESS_TOKEN"):
        return
    missing = [
        name
        for name in ("SCHWAB_CLIENT_ID", "SCHWAB_CLIENT_SECRET", "SCHWAB_REFRESH_TOKEN")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(
            "Option PCR enrichment requires Schwab auth. Missing environment variables: "
            + ", ".join(missing)
        )


def _schwab_access_token() -> str:
    token = os.environ.get("SCHWAB_ACCESS_TOKEN")
    if token:
        return token
    return _refresh_schwab_access_token()


def _refresh_schwab_access_token() -> str:
    _require_schwab_auth_material()
    client_id = os.environ["SCHWAB_CLIENT_ID"]
    client_secret = os.environ["SCHWAB_CLIENT_SECRET"]
    refresh_token = os.environ["SCHWAB_REFRESH_TOKEN"]
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    body = parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode("utf-8")
    req = request.Request(
        SCHWAB_TOKEN_URL,
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    access_token = str(payload.get("access_token") or "")
    new_refresh_token = str(payload.get("refresh_token") or "")
    if not access_token:
        raise RuntimeError("Schwab token refresh response did not include access_token")
    os.environ["SCHWAB_ACCESS_TOKEN"] = access_token
    if new_refresh_token:
        os.environ["SCHWAB_REFRESH_TOKEN"] = new_refresh_token
    return access_token


def _flatten_option_contracts(exp_date_map: Any) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    if not isinstance(exp_date_map, dict):
        return contracts
    for strikes in exp_date_map.values():
        if not isinstance(strikes, dict):
            continue
        for contract_list in strikes.values():
            if isinstance(contract_list, list):
                contracts.extend(c for c in contract_list if isinstance(c, dict))
    return contracts


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return number


def _text_value(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _first_contract_text(contract: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _text_value(contract.get(key))
        if value:
            return value
    return None


def _first_contract_number(contract: dict[str, Any], *keys: str) -> float | int | None:
    for key in keys:
        value = _float_value(contract.get(key))
        if value is not None:
            return value
    return None


def _date_key(value: Any) -> str | None:
    text = _text_value(value)
    return text[:10] if text else None


def _calculated_dte(snapshot_date: str | None, expiration_date: str | None) -> int | None:
    if not snapshot_date or not expiration_date:
        return None
    try:
        snapshot = datetime.fromisoformat(snapshot_date[:10]).date()
        expiration = datetime.fromisoformat(expiration_date[:10]).date()
    except ValueError:
        return None
    return (expiration - snapshot).days


def _dollar_volume(row: dict[str, Any]) -> float:
    adv_usd = _float_value(row.get("adv_usd"))
    if adv_usd is not None and adv_usd > 0:
        return float(adv_usd)
    price = _float_value(row.get("current_price"))
    volume = _float_value(row.get("volume"))
    if price is None or volume is None or price <= 0 or volume <= 0:
        return 0.0
    return float(price) * float(volume)


def _read_previous_option_tracking_pool() -> dict[str, dict[str, Any]]:
    base_url = (os.environ.get("STATIC_DATA_BASE_URL") or "").rstrip("/")
    if not base_url:
        return {}
    url = f"{base_url}/markets/us/options/option-chain-tracking-pool.json"
    try:
        req = request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - first run or unavailable pool is expected
        print(f"Option chain tracking pool unavailable: {exc}")
        return {}
    raw_rows = payload.get("rows") or []
    if isinstance(raw_rows, dict):
        items = raw_rows.values()
    elif isinstance(raw_rows, list):
        items = raw_rows
    else:
        return {}
    pool: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper().strip()
        if symbol:
            pool[symbol] = dict(item, symbol=symbol)
    return pool


def _build_option_tracking_pool(
    rows: list[dict[str, Any]],
    *,
    generated_at: str,
    as_of_date: str,
    top_n: int = OPTION_CHAIN_TRACKING_TOP_N,
    retention_days: int = OPTION_CHAIN_TRACKING_RETENTION_DAYS,
    max_symbols: int = OPTION_CHAIN_MAX_FETCH_SYMBOLS,
) -> dict[str, Any]:
    today_key = str(as_of_date)[:10]
    active_until = (datetime.fromisoformat(today_key).date() + timedelta(days=retention_days - 1)).isoformat()
    previous = _read_previous_option_tracking_pool()
    liquidity_by_symbol = {
        str(row.get("symbol") or "").upper().strip(): _dollar_volume(row)
        for row in rows
        if row.get("symbol")
    }
    ranked_today = sorted(
        ((symbol, value) for symbol, value in liquidity_by_symbol.items() if value > 0),
        key=lambda item: (-item[1], item[0]),
    )[:top_n]
    today_rank = {symbol: index + 1 for index, (symbol, _value) in enumerate(ranked_today)}

    candidates: dict[str, dict[str, Any]] = {}
    for symbol, prior in previous.items():
        if str(prior.get("active_until") or "") >= today_key:
            candidates[symbol] = dict(prior)

    for symbol, dollar_value in ranked_today:
        prior = candidates.get(symbol) or previous.get(symbol) or {}
        first_tracked = str(prior.get("first_tracked_date") or today_key)
        max_dollar = max(float(prior.get("max_dollar_volume") or 0), float(dollar_value or 0))
        candidates[symbol] = {
            "symbol": symbol,
            "first_tracked_date": first_tracked,
            "last_ranked_date": today_key,
            "last_seen_date": today_key,
            "active_until": active_until,
            "reason": "dollar_volume_top500",
            "priority": today_rank[symbol],
            "dollar_volume": dollar_value,
            "max_dollar_volume": max_dollar,
            "updated_at": generated_at,
        }

    for symbol, entry in list(candidates.items()):
        if symbol not in today_rank:
            current_liquidity = liquidity_by_symbol.get(symbol, 0.0)
            entry["dollar_volume"] = current_liquidity
            entry["max_dollar_volume"] = max(float(entry.get("max_dollar_volume") or 0), current_liquidity)
            entry["priority"] = int(entry.get("priority") or 1_000_000)
            entry["updated_at"] = generated_at

    selected = sorted(
        candidates.values(),
        key=lambda item: (-float(item.get("max_dollar_volume") or 0), int(item.get("priority") or 1_000_000), str(item.get("symbol") or "")),
    )[:max_symbols]
    active_symbols = [str(item["symbol"]) for item in selected]
    return {
        "schema_version": "option-chain-tracking-pool-v1",
        "generated_at": generated_at,
        "as_of_date": today_key,
        "retention_days": retention_days,
        "seed_top_n": top_n,
        "max_symbols": max_symbols,
        "active_count": len(active_symbols),
        "active_symbols": active_symbols,
        "rows": selected,
    }


def _write_option_tracking_pool(output_dir: Path, payload: dict[str, Any]) -> None:
    _write_json(output_dir / "markets/us/options/option-chain-tracking-pool.json", payload)


def _normalize_option_contract(
    symbol: str,
    contract: dict[str, Any],
    *,
    option_type: str,
    asof: str,
    snapshot_date: str,
) -> dict[str, Any]:
    expiration_date = _date_key(_first_contract_text(contract, "expirationDate", "expiration"))
    schwab_dte = _first_contract_number(contract, "daysToExpiration", "dte")
    dte_at_snapshot = _calculated_dte(snapshot_date, expiration_date)
    volume = _int_value(contract.get("totalVolume", contract.get("volume")))
    open_interest = _int_value(contract.get("openInterest"))
    strike = _first_contract_number(contract, "strikePrice", "strike")
    bid = _first_contract_number(contract, "bidPrice", "bid")
    ask = _first_contract_number(contract, "askPrice", "ask")
    mid = (bid + ask) / 2 if bid is not None and ask is not None else None
    theta = _first_contract_number(contract, "theta")
    normalized = {
        "symbol": symbol,
        "underlying_symbol": symbol,
        "option_type": option_type.upper(),
        "contract_symbol": _first_contract_text(contract, "symbol", "contractSymbol"),
        "expiration": expiration_date,
        "expiration_date": expiration_date,
        "dte": dte_at_snapshot,
        "dte_at_snapshot": dte_at_snapshot,
        "schwab_dte": schwab_dte,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "last": _first_contract_number(contract, "lastPrice", "last"),
        "mark": _first_contract_number(contract, "markPrice", "mark"),
        "volume": volume,
        "open_interest": open_interest,
        "iv": _first_contract_number(contract, "volatility", "impliedVolatility", "iv"),
        "delta": _first_contract_number(contract, "delta"),
        "theta": theta,
        "theta_yield_pct": (abs(theta) / strike * 100) if theta is not None and strike and strike > 0 else None,
        "spread_pct": ((ask - bid) / mid * 100) if mid and mid > 0 and bid is not None and ask is not None else None,
        "roc_pct": (bid / strike * 100) if bid is not None and strike and strike > 0 else None,
        "asof": asof,
    }
    if normalized["option_type"] == "PUT":
        normalized["put_volume"] = volume
        normalized["put_oi"] = open_interest
    return normalized


def _fetch_option_pcr(symbol: str, *, access_token: str, today: datetime | None = None) -> dict[str, Any]:
    now = today or datetime.now(timezone.utc)
    from_date = (now.date() + timedelta(days=OPTION_PCR_MIN_DTE)).isoformat()
    to_date = (now.date() + timedelta(days=OPTION_PCR_MAX_DTE)).isoformat()
    query = parse.urlencode({
        "symbol": symbol,
        "contractType": "ALL",
        "strategy": "SINGLE",
        "fromDate": from_date,
        "toDate": to_date,
        "includeUnderlyingQuote": "false",
        "optionType": "ALL",
    })
    req = request.Request(
        f"{SCHWAB_MARKETDATA_BASE_URL}/chains?{query}",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    with request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    puts = _flatten_option_contracts(payload.get("putExpDateMap"))
    calls = _flatten_option_contracts(payload.get("callExpDateMap"))
    put_volume = sum(_int_value(contract.get("totalVolume")) for contract in puts)
    call_volume = sum(_int_value(contract.get("totalVolume")) for contract in calls)
    expirations = {
        str(contract.get("expirationDate"))[:10]
        for contract in [*puts, *calls]
        if contract.get("expirationDate")
    }
    asof = datetime.now(timezone.utc).isoformat()
    snapshot_date = now.date().isoformat()
    put_contracts = [
        _normalize_option_contract(symbol, contract, option_type="PUT", asof=asof, snapshot_date=snapshot_date)
        for contract in puts
    ]
    call_contracts = [
        _normalize_option_contract(symbol, contract, option_type="CALL", asof=asof, snapshot_date=snapshot_date)
        for contract in calls
    ]
    return {
        "option_pcr_volume_14_28dte": (put_volume / call_volume) if call_volume > 0 else None,
        "option_put_volume_14_28dte": put_volume,
        "option_call_volume_14_28dte": call_volume,
        "option_put_oi_14_28dte": sum(_int_value(contract.get("openInterest")) for contract in puts),
        "option_call_oi_14_28dte": sum(_int_value(contract.get("openInterest")) for contract in calls),
        "option_pcr_volume_14_28dte_expirations": len(expirations),
        "option_pcr_volume_14_28dte_contracts": len(puts) + len(calls),
        "option_pcr_volume_14_28dte_min_dte": OPTION_PCR_MIN_DTE,
        "option_pcr_volume_14_28dte_max_dte": OPTION_PCR_MAX_DTE,
        "option_pcr_volume_14_28dte_asof": asof,
        "option_pcr_volume_14_28dte_provider": "schwab",
        "option_put_contracts_14_28dte_count": len(put_contracts),
        "option_call_contracts_14_28dte_count": len(call_contracts),
        "_option_put_contracts_14_28dte": put_contracts,
        "_option_contracts_14_28dte": [*put_contracts, *call_contracts],
    }


def _option_pcr_error_fields(message: str) -> dict[str, Any]:
    return {
        "option_pcr_volume_14_28dte_error": message[:200],
        "option_pcr_volume_14_28dte_provider": "schwab",
        "option_pcr_volume_14_28dte_min_dte": OPTION_PCR_MIN_DTE,
        "option_pcr_volume_14_28dte_max_dte": OPTION_PCR_MAX_DTE,
    }


def _enrich_rows_with_option_pcr(rows: list[dict[str, Any]], *, tracked_symbols: list[str]) -> int:
    rows_by_symbol = {str(row.get("symbol") or "").upper().strip(): row for row in rows if row.get("symbol")}
    target_rows = [rows_by_symbol[symbol] for symbol in tracked_symbols if symbol in rows_by_symbol]
    started_at = time.monotonic()
    _log("Option PCR enrichment starting", rows=len(target_rows), tracked_symbols=len(tracked_symbols))
    try:
        _require_schwab_auth_material()
        access_token = _schwab_access_token()
    except Exception as exc:  # noqa: BLE001 - static export must not fail on optional enrichment
        message = f"Option PCR enrichment skipped: {exc}"
        for row in target_rows:
            if row.get("option_pcr_volume_14_28dte") is None:
                row.update(_option_pcr_error_fields(message))
        _log(message, rows=len(target_rows))
        return 0

    updated = 0
    errors = 0
    skipped = 0
    for index, row in enumerate(target_rows, start=1):
        if row.get("option_pcr_volume_14_28dte") is not None and row.get("_option_contracts_14_28dte") is not None:
            skipped += 1
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            skipped += 1
            continue
        if index == 1 or index % 25 == 0 or index == len(target_rows):
            _log("Option PCR enrichment progress", index=index, total=len(target_rows), symbol=symbol, updated=updated, errors=errors, elapsed_seconds=round(time.monotonic() - started_at, 1))
        try:
            row.update(_fetch_option_pcr(symbol, access_token=access_token))
            updated += 1
        except error.HTTPError as exc:
            if exc.code in (401, 403):
                try:
                    access_token = _refresh_schwab_access_token()
                    row.update(_fetch_option_pcr(symbol, access_token=access_token))
                    updated += 1
                    if OPTION_PCR_REQUEST_INTERVAL_SECONDS and index < len(target_rows) - 1:
                        time.sleep(OPTION_PCR_REQUEST_INTERVAL_SECONDS)
                    continue
                except error.HTTPError as retry_exc:
                    exc = retry_exc
                except Exception as refresh_exc:  # noqa: BLE001 - per-row enrichment is optional
                    row.update(_option_pcr_error_fields(str(refresh_exc)))
                    errors += 1
                    continue
            row.update(_option_pcr_error_fields(f"HTTP {exc.code}"))
            errors += 1
        except Exception as exc:  # noqa: BLE001 - best-effort static enrichment
            row.update(_option_pcr_error_fields(str(exc)))
            errors += 1
        if OPTION_PCR_REQUEST_INTERVAL_SECONDS and index < len(target_rows):
            time.sleep(OPTION_PCR_REQUEST_INTERVAL_SECONDS)
    _log("Option PCR enrichment complete", updated=updated, errors=errors, skipped=skipped, rows=len(target_rows), tracked_symbols=len(tracked_symbols), elapsed_seconds=round(time.monotonic() - started_at, 1))
    return updated


def _read_previous_option_history() -> dict[str, list[dict[str, Any]]]:
    base_url = (os.environ.get("STATIC_DATA_BASE_URL") or "").rstrip("/")
    if not base_url:
        return {}
    url = f"{base_url}/markets/us/options/put-liquidity-history.json"
    try:
        req = request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - first run or unavailable history is expected
        print(f"Option put liquidity history unavailable: {exc}")
        return {}
    rows = payload.get("rows") or {}
    if not isinstance(rows, dict):
        return {}
    return {
        str(symbol).upper(): entries
        for symbol, entries in rows.items()
        if isinstance(entries, list)
    }


def _merge_option_history(
    rows: list[dict[str, Any]],
    *,
    output_dir: Path,
    generated_at: str,
    as_of_date: str,
    window_days: int = 7,
) -> dict[str, Any]:
    history = _read_previous_option_history()
    today_key = str(as_of_date)
    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol or row.get("option_put_volume_14_28dte") is None:
            continue
        entries = [entry for entry in history.get(symbol, []) if isinstance(entry, dict) and entry.get("date") != today_key]
        entries.append({
            "date": today_key,
            "put_volume": row.get("option_put_volume_14_28dte"),
            "put_oi": row.get("option_put_oi_14_28dte"),
            "pcr": row.get("option_pcr_volume_14_28dte"),
            "asof": row.get("option_pcr_volume_14_28dte_asof"),
        })
        entries = sorted(entries, key=lambda item: str(item.get("date") or ""))[-window_days:]
        history[symbol] = entries

    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        entries = history.get(symbol, [])[-window_days:]
        row["option_put_volume_14_28dte_history"] = [entry.get("put_volume") for entry in entries]
        row["option_put_oi_14_28dte_history"] = [entry.get("put_oi") for entry in entries]
        row["option_put_liquidity_history_dates"] = [entry.get("date") for entry in entries]

    payload = {
        "schema_version": "option-put-liquidity-history-v1",
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "window_days": window_days,
        "rows": history,
    }
    _write_json(output_dir / "markets/us/options/put-liquidity-history.json", payload)
    return payload


def _read_previous_option_contract_history() -> dict[str, dict[str, list[dict[str, Any]]]]:
    base_url = (os.environ.get("STATIC_DATA_BASE_URL") or "").rstrip("/")
    if not base_url:
        return {}
    url = f"{base_url}/markets/us/options/put-contract-liquidity-history.json"
    try:
        req = request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - first run or unavailable history is expected
        print(f"Option put contract liquidity history unavailable: {exc}")
        return {}
    rows = payload.get("rows") or {}
    if not isinstance(rows, dict):
        return {}
    history: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for symbol, contracts in rows.items():
        if not isinstance(contracts, dict):
            continue
        symbol_history: dict[str, list[dict[str, Any]]] = {}
        for contract_key, entries in contracts.items():
            if isinstance(entries, list):
                symbol_history[str(contract_key)] = [entry for entry in entries if isinstance(entry, dict)]
        if symbol_history:
            history[str(symbol).upper()] = symbol_history
    return history


def _contract_history_key(contract: dict[str, Any]) -> str:
    expiration = str(contract.get("expiration") or "")[:10]
    strike = contract.get("strike")
    if isinstance(strike, (int, float)) and math.isfinite(float(strike)):
        strike_text = (f"{float(strike):.4f}").rstrip("0").rstrip(".")
    else:
        strike_text = str(strike or "")
    contract_symbol = str(contract.get("contract_symbol") or "")
    return f"{expiration}|{strike_text}|{contract_symbol}"


def _contract_history_entry(contract: dict[str, Any], *, date_key: str) -> dict[str, Any]:
    return {
        "date": date_key,
        "asof": contract.get("asof"),
        "symbol": contract.get("symbol"),
        "contract_symbol": contract.get("contract_symbol"),
        "expiration": contract.get("expiration"),
        "expiration_date": contract.get("expiration_date"),
        "dte": contract.get("dte"),
        "dte_at_snapshot": contract.get("dte_at_snapshot"),
        "schwab_dte": contract.get("schwab_dte"),
        "strike": contract.get("strike"),
        "bid": contract.get("bid"),
        "ask": contract.get("ask"),
        "last": contract.get("last"),
        "mark": contract.get("mark"),
        "volume": contract.get("volume"),
        "open_interest": contract.get("open_interest"),
        "put_volume": contract.get("put_volume"),
        "put_oi": contract.get("put_oi"),
        "iv": contract.get("iv"),
        "delta": contract.get("delta"),
    }


def _merge_option_contract_history(
    rows: list[dict[str, Any]],
    *,
    output_dir: Path,
    generated_at: str,
    as_of_date: str,
    window_days: int = 90,
) -> dict[str, Any]:
    history = _read_previous_option_contract_history()
    latest: dict[str, list[dict[str, Any]]] = {}
    today_key = str(as_of_date)[:10]

    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        contracts = row.get("_option_put_contracts_14_28dte")
        if not symbol or not isinstance(contracts, list):
            continue
        normalized_contracts = [contract for contract in contracts if isinstance(contract, dict)]
        latest[symbol] = normalized_contracts
        symbol_history = dict(history.get(symbol) or {})
        for contract in normalized_contracts:
            contract_key = _contract_history_key(contract)
            entries = [
                entry
                for entry in symbol_history.get(contract_key, [])
                if isinstance(entry, dict) and entry.get("date") != today_key
            ]
            entries.append(_contract_history_entry(contract, date_key=today_key))
            symbol_history[contract_key] = sorted(entries, key=lambda item: str(item.get("date") or ""))[-window_days:]
        for contract_key, entries in list(symbol_history.items()):
            kept = sorted(
                [entry for entry in entries if isinstance(entry, dict)],
                key=lambda item: str(item.get("date") or ""),
            )[-window_days:]
            if kept:
                symbol_history[contract_key] = kept
            else:
                del symbol_history[contract_key]
        if symbol_history:
            history[symbol] = symbol_history

    latest_payload = {
        "schema_version": "option-put-contract-liquidity-latest-v1",
        "generated_at": generated_at,
        "as_of_date": today_key,
        "min_dte": OPTION_PCR_MIN_DTE,
        "max_dte": OPTION_PCR_MAX_DTE,
        "rows": latest,
    }
    history_payload = {
        "schema_version": "option-put-contract-liquidity-history-v1",
        "generated_at": generated_at,
        "as_of_date": today_key,
        "window_days": window_days,
        "min_dte": OPTION_PCR_MIN_DTE,
        "max_dte": OPTION_PCR_MAX_DTE,
        "rows": history,
    }
    _write_json(output_dir / "markets/us/options/put-contract-liquidity-latest.json", latest_payload)
    _write_json(output_dir / "markets/us/options/put-contract-liquidity-history.json", history_payload)
    for row in rows:
        row.pop("_option_put_contracts_14_28dte", None)
    return {"latest": latest_payload, "history": history_payload}


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value) if math.isfinite(float(value)) else "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _write_option_contract_d1_import_sql(
    rows: list[dict[str, Any]],
    *,
    output_dir: Path,
    generated_at: str,
    as_of_date: str,
    retention_days: int = 90,
) -> dict[str, Any]:
    sql_path = output_dir / "markets/us/options/option-contract-liquidity-d1.sql"
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    today_key = str(as_of_date)[:10]
    cutoff = (datetime.fromisoformat(today_key).date() - timedelta(days=retention_days - 1)).isoformat()
    statements = [
        "CREATE TABLE IF NOT EXISTS option_contract_liquidity_snapshots (\n"
        "    snapshot_date TEXT NOT NULL,\n"
        "    underlying_symbol TEXT NOT NULL,\n"
        "    option_type TEXT NOT NULL,\n"
        "    contract_symbol TEXT NOT NULL,\n"
        "    expiration_date TEXT NOT NULL,\n"
        "    strike REAL NOT NULL,\n"
        "    dte_at_snapshot INTEGER NOT NULL,\n"
        "    schwab_dte INTEGER,\n"
        "    bid REAL,\n"
        "    ask REAL,\n"
        "    last REAL,\n"
        "    mark REAL,\n"
        "    volume INTEGER NOT NULL DEFAULT 0,\n"
        "    open_interest INTEGER NOT NULL DEFAULT 0,\n"
        "    iv REAL,\n"
        "    delta REAL,\n"
        "    theta REAL,\n"
        "    theta_yield_pct REAL,\n"
        "    spread_pct REAL,\n"
        "    roc_pct REAL,\n"
        "    asof TEXT,\n"
        "    provider TEXT NOT NULL DEFAULT 'schwab',\n"
        "    created_at TEXT NOT NULL,\n"
        "    PRIMARY KEY (snapshot_date, contract_symbol)\n"
        ")",
        "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS option_contract_liquidity_summary (\n"
        "    snapshot_date TEXT NOT NULL,\n"
        "    underlying_symbol TEXT NOT NULL,\n"
        "    put_volume INTEGER NOT NULL DEFAULT 0,\n"
        "    call_volume INTEGER NOT NULL DEFAULT 0,\n"
        "    put_oi INTEGER NOT NULL DEFAULT 0,\n"
        "    call_oi INTEGER NOT NULL DEFAULT 0,\n"
        "    pcr_volume REAL,\n"
        "    pcr_oi REAL,\n"
        "    put_contract_count INTEGER NOT NULL DEFAULT 0,\n"
        "    call_contract_count INTEGER NOT NULL DEFAULT 0,\n"
        "    contract_count INTEGER NOT NULL DEFAULT 0,\n"
        "    asof TEXT,\n"
        "    created_at TEXT NOT NULL,\n"
        "    PRIMARY KEY (snapshot_date, underlying_symbol)\n"
        ")",
        "CREATE INDEX IF NOT EXISTS idx_opt_liq_symbol_date_dte ON option_contract_liquidity_snapshots(underlying_symbol, snapshot_date, dte_at_snapshot)",
        "CREATE INDEX IF NOT EXISTS idx_opt_liq_contract_date ON option_contract_liquidity_snapshots(contract_symbol, snapshot_date)",
        "CREATE INDEX IF NOT EXISTS idx_opt_liq_expiration ON option_contract_liquidity_snapshots(expiration_date, strike, option_type)",
        "CREATE INDEX IF NOT EXISTS idx_opt_liq_type_date ON option_contract_liquidity_snapshots(option_type, snapshot_date)",
        "CREATE INDEX IF NOT EXISTS idx_opt_liq_summary_symbol_date ON option_contract_liquidity_summary(underlying_symbol, snapshot_date)",
        f"DELETE FROM option_contract_liquidity_snapshots WHERE snapshot_date < {_sql_literal(cutoff)}",
        f"DELETE FROM option_contract_liquidity_summary WHERE snapshot_date < {_sql_literal(cutoff)}",
        "INSERT OR REPLACE INTO metadata(key, value) VALUES "
        f"('schema_version', 'option-contract-liquidity-d1-v4'), "
        f"('generated_at', {_sql_literal(generated_at)}), "
        f"('as_of_date', {_sql_literal(today_key)}), "
        f"('min_dte', {_sql_literal(str(OPTION_PCR_MIN_DTE))}), "
        f"('max_dte', {_sql_literal(str(OPTION_PCR_MAX_DTE))}), "
        f"('retention_days', {_sql_literal(str(retention_days))})",
    ]
    inserted = 0
    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        contracts = row.get("_option_contracts_14_28dte")
        if not symbol or not isinstance(contracts, list):
            continue
        for contract in contracts:
            if not isinstance(contract, dict):
                continue
            expiration_date = _date_key(contract.get("expiration_date") or contract.get("expiration"))
            strike = contract.get("strike")
            option_type = str(contract.get("option_type") or "").upper()
            if expiration_date is None or strike is None or option_type not in {"PUT", "CALL"}:
                continue
            contract_symbol = str(contract.get("contract_symbol") or _contract_history_key(contract))
            dte_at_snapshot = _calculated_dte(today_key, expiration_date)
            if dte_at_snapshot is None:
                continue
            values = [
                today_key,
                symbol,
                option_type,
                contract_symbol,
                expiration_date,
                strike,
                dte_at_snapshot,
                contract.get("schwab_dte"),
                contract.get("bid"),
                contract.get("ask"),
                contract.get("last"),
                contract.get("mark"),
                _int_value(contract.get("volume", contract.get("put_volume"))),
                _int_value(contract.get("open_interest", contract.get("put_oi"))),
                contract.get("iv", contract.get("volatility")),
                contract.get("delta"),
                contract.get("theta"),
                contract.get("theta_yield_pct"),
                contract.get("spread_pct"),
                contract.get("roc_pct"),
                contract.get("asof"),
                "schwab",
                generated_at,
            ]
            statements.append(
                "INSERT OR REPLACE INTO option_contract_liquidity_snapshots ("
                "snapshot_date, underlying_symbol, option_type, contract_symbol, expiration_date, strike, "
                "dte_at_snapshot, schwab_dte, bid, ask, last, mark, volume, open_interest, iv, delta, "
                "theta, theta_yield_pct, spread_pct, roc_pct, asof, provider, created_at"
                ") VALUES ("
                + ", ".join(_sql_literal(value) for value in values)
                + ")"
            )
            summary = summaries.setdefault(symbol, {
                "put_volume": 0,
                "call_volume": 0,
                "put_oi": 0,
                "call_oi": 0,
                "put_contract_count": 0,
                "call_contract_count": 0,
                "contract_count": 0,
                "asof": None,
            })
            volume = _int_value(contract.get("volume", contract.get("put_volume")))
            oi = _int_value(contract.get("open_interest", contract.get("put_oi")))
            if option_type == "PUT":
                summary["put_volume"] += volume
                summary["put_oi"] += oi
                summary["put_contract_count"] += 1
            else:
                summary["call_volume"] += volume
                summary["call_oi"] += oi
                summary["call_contract_count"] += 1
            summary["contract_count"] += 1
            summary["asof"] = max(str(summary.get("asof") or ""), str(contract.get("asof") or "")) or None
            inserted += 1
    for symbol, summary in summaries.items():
        put_volume = int(summary["put_volume"])
        call_volume = int(summary["call_volume"])
        put_oi = int(summary["put_oi"])
        call_oi = int(summary["call_oi"])
        values = [
            today_key,
            symbol,
            put_volume,
            call_volume,
            put_oi,
            call_oi,
            (put_volume / call_volume) if call_volume > 0 else None,
            (put_oi / call_oi) if call_oi > 0 else None,
            summary["put_contract_count"],
            summary["call_contract_count"],
            summary["contract_count"],
            summary.get("asof"),
            generated_at,
        ]
        statements.append(
            "INSERT OR REPLACE INTO option_contract_liquidity_summary ("
            "snapshot_date, underlying_symbol, put_volume, call_volume, put_oi, call_oi, "
            "pcr_volume, pcr_oi, put_contract_count, call_contract_count, contract_count, asof, created_at"
            ") VALUES ("
            + ", ".join(_sql_literal(value) for value in values)
            + ")"
        )
    sql_path.write_text(";\n".join(statements) + ";\n", encoding="utf-8")
    _log("Option D1 import SQL written", path=sql_path.as_posix(), statements=len(statements), inserted=inserted)
    return {"path": sql_path.as_posix(), "inserted": inserted, "retention_days": retention_days}


def _download_previous_option_contract_sqlite(db_path: Path) -> bool:
    base_url = (os.environ.get("STATIC_DATA_BASE_URL") or "").rstrip("/")
    if not base_url:
        return False
    url = f"{base_url}/markets/us/options/option-contract-liquidity.sqlite"
    try:
        req = request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/octet-stream"})
        with request.urlopen(req, timeout=30) as response:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.write_bytes(response.read())
        return True
    except Exception as exc:  # noqa: BLE001 - first run or unavailable history is expected
        print(f"Option put contract SQLite history unavailable: {exc}")
        return False


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value


def _merge_option_contract_sqlite(
    rows: list[dict[str, Any]],
    *,
    output_dir: Path,
    generated_at: str,
    as_of_date: str,
    retention_days: int = 90,
) -> dict[str, Any]:
    db_path = output_dir / "markets/us/options/option-contract-liquidity.sqlite"
    if not db_path.exists():
        _download_previous_option_contract_sqlite(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS option_contract_liquidity_snapshots (
                snapshot_date TEXT NOT NULL,
                underlying_symbol TEXT NOT NULL,
                option_type TEXT NOT NULL,
                contract_symbol TEXT NOT NULL,
                expiration_date TEXT NOT NULL,
                strike REAL NOT NULL,
                dte_at_snapshot INTEGER NOT NULL,
                schwab_dte INTEGER,
                bid REAL,
                ask REAL,
                last REAL,
                mark REAL,
                volume INTEGER NOT NULL DEFAULT 0,
                open_interest INTEGER NOT NULL DEFAULT 0,
                iv REAL,
                delta REAL,
                theta REAL,
                theta_yield_pct REAL,
                spread_pct REAL,
                roc_pct REAL,
                asof TEXT,
                provider TEXT NOT NULL DEFAULT 'schwab',
                created_at TEXT NOT NULL,
                PRIMARY KEY (snapshot_date, contract_symbol)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS option_contract_liquidity_summary (
                snapshot_date TEXT NOT NULL,
                underlying_symbol TEXT NOT NULL,
                put_volume INTEGER NOT NULL DEFAULT 0,
                call_volume INTEGER NOT NULL DEFAULT 0,
                put_oi INTEGER NOT NULL DEFAULT 0,
                call_oi INTEGER NOT NULL DEFAULT 0,
                pcr_volume REAL,
                pcr_oi REAL,
                put_contract_count INTEGER NOT NULL DEFAULT 0,
                call_contract_count INTEGER NOT NULL DEFAULT 0,
                contract_count INTEGER NOT NULL DEFAULT 0,
                asof TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (snapshot_date, underlying_symbol)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_liq_symbol_date_dte ON option_contract_liquidity_snapshots(underlying_symbol, snapshot_date, dte_at_snapshot)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_liq_contract_date ON option_contract_liquidity_snapshots(contract_symbol, snapshot_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_liq_expiration ON option_contract_liquidity_snapshots(expiration_date, strike, option_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_liq_type_date ON option_contract_liquidity_snapshots(option_type, snapshot_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_liq_summary_symbol_date ON option_contract_liquidity_summary(underlying_symbol, snapshot_date)")
        existing_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(option_contract_liquidity_snapshots)").fetchall()
        }
        for column_name in ("theta", "theta_yield_pct", "spread_pct", "roc_pct"):
            if column_name not in existing_columns:
                conn.execute(f"ALTER TABLE option_contract_liquidity_snapshots ADD COLUMN {column_name} REAL")

        today_key = str(as_of_date)[:10]
        inserted = 0
        summaries: dict[str, dict[str, Any]] = {}
        for row in rows:
            symbol = str(row.get("symbol") or "").upper().strip()
            contracts = row.get("_option_contracts_14_28dte")
            if not symbol or not isinstance(contracts, list):
                continue
            for contract in contracts:
                if not isinstance(contract, dict):
                    continue
                expiration_date = _date_key(contract.get("expiration_date") or contract.get("expiration"))
                strike = contract.get("strike")
                option_type = str(contract.get("option_type") or "").upper()
                if expiration_date is None or strike is None or option_type not in {"PUT", "CALL"}:
                    continue
                contract_symbol = contract.get("contract_symbol") or _contract_history_key(contract)
                dte_at_snapshot = _calculated_dte(today_key, expiration_date)
                if dte_at_snapshot is None:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO option_contract_liquidity_snapshots (
                        snapshot_date, underlying_symbol, option_type, contract_symbol,
                        expiration_date, strike, dte_at_snapshot, schwab_dte,
                        bid, ask, last, mark, volume, open_interest, iv, delta,
                        theta, theta_yield_pct, spread_pct, roc_pct, asof, provider, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        today_key,
                        symbol,
                        option_type,
                        contract_symbol,
                        expiration_date,
                        _sqlite_value(strike),
                        dte_at_snapshot,
                        _sqlite_value(contract.get("schwab_dte")),
                        _sqlite_value(contract.get("bid")),
                        _sqlite_value(contract.get("ask")),
                        _sqlite_value(contract.get("last")),
                        _sqlite_value(contract.get("mark")),
                        _int_value(contract.get("volume", contract.get("put_volume"))),
                        _int_value(contract.get("open_interest", contract.get("put_oi"))),
                        _sqlite_value(contract.get("iv", contract.get("volatility"))),
                        _sqlite_value(contract.get("delta")),
                        _sqlite_value(contract.get("theta")),
                        _sqlite_value(contract.get("theta_yield_pct")),
                        _sqlite_value(contract.get("spread_pct")),
                        _sqlite_value(contract.get("roc_pct")),
                        contract.get("asof"),
                        "schwab",
                        generated_at,
                    ),
                )
                summary = summaries.setdefault(symbol, {
                    "put_volume": 0,
                    "call_volume": 0,
                    "put_oi": 0,
                    "call_oi": 0,
                    "put_contract_count": 0,
                    "call_contract_count": 0,
                    "contract_count": 0,
                    "asof": None,
                })
                volume = _int_value(contract.get("volume", contract.get("put_volume")))
                oi = _int_value(contract.get("open_interest", contract.get("put_oi")))
                if option_type == "PUT":
                    summary["put_volume"] += volume
                    summary["put_oi"] += oi
                    summary["put_contract_count"] += 1
                else:
                    summary["call_volume"] += volume
                    summary["call_oi"] += oi
                    summary["call_contract_count"] += 1
                summary["contract_count"] += 1
                summary["asof"] = max(str(summary.get("asof") or ""), str(contract.get("asof") or "")) or None
                inserted += 1
        for symbol, summary in summaries.items():
            put_volume = int(summary["put_volume"])
            call_volume = int(summary["call_volume"])
            put_oi = int(summary["put_oi"])
            call_oi = int(summary["call_oi"])
            conn.execute(
                """
                INSERT OR REPLACE INTO option_contract_liquidity_summary (
                    snapshot_date, underlying_symbol, put_volume, call_volume, put_oi, call_oi,
                    pcr_volume, pcr_oi, put_contract_count, call_contract_count, contract_count, asof, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    today_key,
                    symbol,
                    put_volume,
                    call_volume,
                    put_oi,
                    call_oi,
                    (put_volume / call_volume) if call_volume > 0 else None,
                    (put_oi / call_oi) if call_oi > 0 else None,
                    summary["put_contract_count"],
                    summary["call_contract_count"],
                    summary["contract_count"],
                    summary.get("asof"),
                    generated_at,
                ),
            )

        cutoff = (datetime.fromisoformat(today_key).date() - timedelta(days=retention_days - 1)).isoformat()
        deleted = conn.execute(
            "DELETE FROM option_contract_liquidity_snapshots WHERE snapshot_date < ?",
            (cutoff,),
        ).rowcount
        conn.execute(
            "DELETE FROM option_contract_liquidity_summary WHERE snapshot_date < ?",
            (cutoff,),
        )
        metadata = {
            "schema_version": "option-contract-liquidity-sqlite-v4",
            "generated_at": generated_at,
            "as_of_date": today_key,
            "min_dte": str(OPTION_PCR_MIN_DTE),
            "max_dte": str(OPTION_PCR_MAX_DTE),
            "retention_days": str(retention_days),
        }
        conn.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            metadata.items(),
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        total_rows = conn.execute("SELECT COUNT(*) FROM option_contract_liquidity_snapshots").fetchone()[0]
    finally:
        conn.close()
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()

    summary = {
        "path": db_path.as_posix(),
        "inserted": inserted,
        "deleted": int(deleted or 0),
        "rows_total": int(total_rows),
        "retention_days": retention_days,
    }
    _log("Option contract SQLite merged", **summary)
    return summary


def _read_json(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _number(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return number


def _rows_by_symbol(bundle: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not bundle:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in bundle.get("rows") or []:
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol:
            result[symbol] = row
    return result


def _metrics_by_symbol(scan_metrics_bundle: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return _rows_by_symbol(scan_metrics_bundle)


def _latest_daily_by_symbol(daily_bundle: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not daily_bundle:
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for row in daily_bundle.get("rows") or []:
        symbol = str(row.get("symbol") or "").upper().strip()
        prices = row.get("prices") or []
        if not symbol or not prices:
            continue
        last = prices[-1]
        prev = prices[-2] if len(prices) >= 2 else None
        close = _number(last.get("close"))
        prev_close = _number(prev.get("close")) if prev else None
        change_1d = None
        if close is not None and prev_close not in (None, 0):
            change_1d = round(((float(close) - float(prev_close)) / float(prev_close)) * 100.0, 4)
        latest[symbol] = {
            "date": last.get("date"),
            "close": close,
            "volume": _number(last.get("volume")),
            "change_1d": change_1d,
            "prices": prices,
        }
    return latest


def _weekly_rows(weekly_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = weekly_bundle.get("snapshot") or {}
    rows = snapshot.get("rows") if isinstance(snapshot, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Foundation update bundle snapshot.rows must be a list")
    return rows


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("normalized_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    symbol = str(row.get("symbol") or payload.get("symbol") or "").upper().strip()
    return {**payload, "symbol": symbol, "exchange": row.get("exchange") or payload.get("exchange")}


def _composite_score(payload: dict[str, Any]) -> float | None:
    parts = [
        _number(payload.get("eps_rating")),
        _number(payload.get("perf_quarter")),
        _number(payload.get("perf_half_year")),
        _number(payload.get("relative_volume")),
    ]
    usable = [float(value) for value in parts if value is not None]
    if not usable:
        return None
    return round(sum(usable) / len(usable), 2)


def _sparkline_from_prices(prices: list[dict[str, Any]] | None, *, limit: int = 30) -> list[float]:
    values: list[float] = []
    for row in (prices or [])[-limit:]:
        close = _number(row.get("close"))
        if close is not None:
            values.append(float(close))
    return values


def _trend(values: list[float]) -> int:
    if len(values) < 2 or values[0] == 0:
        return 0
    change = (values[-1] - values[0]) / values[0]
    if change > 0.005:
        return 1
    if change < -0.005:
        return -1
    return 0


def _rs_sparkline(prices: list[dict[str, Any]] | None, benchmark: list[dict[str, Any]] | None, *, limit: int = 30) -> list[float]:
    benchmark_by_date = {
        str(row.get("date")): _number(row.get("close"))
        for row in (benchmark or [])
        if row.get("date") and _number(row.get("close")) not in (None, 0)
    }
    values: list[float] = []
    for row in (prices or [])[-limit:]:
        date_key = str(row.get("date"))
        close = _number(row.get("close"))
        bench_close = benchmark_by_date.get(date_key)
        if close is not None and bench_close not in (None, 0):
            values.append(round(float(close) / float(bench_close), 6))
    return values


def _scan_row(
    payload: dict[str, Any],
    latest_price: dict[str, Any] | None,
    benchmark_prices: list[dict[str, Any]] | None = None,
    scan_metrics: dict[str, Any] | None = None,
    group_rank: dict[str, Any] | None = None,
    listing_profile: dict[str, Any] | None = None,
    etf_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbol = payload["symbol"]
    price_history = (latest_price or {}).get("prices") or []
    price_sparkline = _sparkline_from_prices(price_history)
    rs_sparkline = _rs_sparkline(price_history, benchmark_prices)
    share_volume = (latest_price or {}).get("volume") or _number(payload.get("avg_volume"))
    current_price = (latest_price or {}).get("close")
    dollar_volume = None
    if share_volume is not None and current_price is not None:
        dollar_volume = float(share_volume) * float(current_price)
    change_1d = (latest_price or {}).get("change_1d")
    market_cap = _number(payload.get("market_cap"))
    currency = payload.get("currency") or "USD"
    composite = _composite_score(payload)
    eps_rating = _number(payload.get("eps_rating"))
    metrics = scan_metrics or {}
    group = group_rank or {}
    listing = listing_profile or {}
    etf = etf_profile or {}
    market_cap = market_cap or _number(etf.get("net_assets")) or _number(etf.get("aum"))
    row = {
        "symbol": symbol,
        "company_name": payload.get("company_name") or payload.get("name") or symbol,
        "market": payload.get("market") or DEFAULT_MARKET,
        "exchange": payload.get("exchange"),
        "currency": currency,
        "security_type": payload.get("security_type"),
        "is_etf": bool(payload.get("is_etf") or str(payload.get("security_type") or "").upper() == "ETF"),
        "current_price": current_price,
        "price_change_1d": change_1d,
        "volume": share_volume,
        "dollar_volume": dollar_volume,
        "avg_volume": _number(payload.get("avg_volume")),
        "market_cap": market_cap,
        "market_cap_usd": _number(payload.get("market_cap_usd")) or market_cap,
        "adv_usd": dollar_volume,
        "gics_sector": payload.get("sector"),
        "sector": payload.get("sector"),
        "industry": payload.get("industry"),
        "ibd_industry_group": group.get("ibd_industry_group") or group.get("group_name") or payload.get("ibd_industry_group") or payload.get("industry"),
        "ibd_group_rank": group.get("ibd_group_rank") or group.get("group_rank"),
        "ipo_date": listing.get("ipo_date") or listing.get("listing_date") or payload.get("ipo_date") or payload.get("first_trade_date"),
        "rating": metrics.get("rating") or payload.get("recommendation") or "Insufficient Data",
        "scan_mode": "artifact_reference",
        "composite_score": metrics.get("composite_score", composite),
        "minervini_score": metrics.get("minervini_score"),
        "canslim_score": metrics.get("canslim_score"),
        "ipo_score": metrics.get("ipo_score"),
        "custom_score": metrics.get("custom_score"),
        "volume_breakthrough_score": metrics.get("volume_breakthrough_score"),
        "se_setup_score": metrics.get("se_setup_score"),
        "rs_rating": metrics.get("rs_rating", eps_rating),
        "rs_rating_1m": metrics.get("rs_rating_1m"),
        "rs_rating_3m": metrics.get("rs_rating_3m"),
        "rs_rating_12m": metrics.get("rs_rating_12m"),
        "eps_rating": metrics.get("eps_rating", eps_rating),
        "eps_growth_qq": _number(payload.get("eps_growth_qq")),
        "sales_growth_qq": _number(payload.get("sales_growth_qq")),
        "adr_percent": metrics.get("adr_percent"),
        "beta": metrics.get("beta", _number(payload.get("beta"))),
        "beta_adj_rs": metrics.get("beta_adj_rs"),
        "vcp_score": metrics.get("vcp_score"),
        "vcp_pivot": metrics.get("vcp_pivot"),
        "stage": metrics.get("stage"),
        "ma_alignment": metrics.get("ma_alignment"),
        "passes_template": metrics.get("passes_template"),
        "pocket_pivot": None,
        "power_trend": None,
        "vcp_detected": metrics.get("vcp_detected"),
        "vcp_ready_for_breakout": metrics.get("vcp_ready_for_breakout"),
        "se_setup_ready": metrics.get("se_setup_ready"),
        "se_rs_line_new_high": metrics.get("se_rs_line_new_high"),
        "se_pattern_primary": metrics.get("se_pattern_primary"),
        "se_distance_to_pivot_pct": metrics.get("se_distance_to_pivot_pct"),
        "se_bb_width_pctile_252": metrics.get("se_bb_width_pctile_252"),
        "se_volume_vs_50d": metrics.get("se_volume_vs_50d"),
        "se_pivot_price": metrics.get("se_pivot_price"),
        "se_up_down_volume_ratio_10d": metrics.get("se_up_down_volume_ratio_10d"),
        "perf_week": _number(payload.get("perf_week")),
        "perf_month": _number(payload.get("perf_month")),
        "perf_3m": _number(payload.get("perf_quarter")),
        "perf_6m": _number(payload.get("perf_half_year")),
        "gap_percent": metrics.get("gap_percent"),
        "volume_surge": metrics.get("volume_surge", _number(payload.get("relative_volume"))),
        "ema_10_distance": metrics.get("ema_10_distance"),
        "ema_20_distance": metrics.get("ema_20_distance"),
        "ema_50_distance": metrics.get("ema_50_distance"),
        "week_52_high_distance": _number(payload.get("week_52_high_distance")),
        "week_52_low_distance": _number(payload.get("week_52_low_distance")),
        "pct_day": metrics.get("pct_day"),
        "pct_week": metrics.get("pct_week"),
        "pct_month": metrics.get("pct_month"),
        "sparkline": price_sparkline,
        "price_sparkline_data": price_sparkline,
        "price_trend": _trend(price_sparkline),
        "rs_sparkline": rs_sparkline,
        "rs_sparkline_data": rs_sparkline,
        "rs_trend": metrics.get("rs_trend", _trend(rs_sparkline)),
        "option_pcr_volume_14_28dte": metrics.get("option_pcr_volume_14_28dte"),
        "option_put_volume_14_28dte": metrics.get("option_put_volume_14_28dte"),
        "option_call_volume_14_28dte": metrics.get("option_call_volume_14_28dte"),
        "option_put_oi_14_28dte": metrics.get("option_put_oi_14_28dte"),
        "option_call_oi_14_28dte": metrics.get("option_call_oi_14_28dte"),
        "option_pcr_volume_14_28dte_expirations": metrics.get("option_pcr_volume_14_28dte_expirations"),
        "option_pcr_volume_14_28dte_contracts": metrics.get("option_pcr_volume_14_28dte_contracts"),
        "option_pcr_volume_14_28dte_min_dte": metrics.get("option_pcr_volume_14_28dte_min_dte"),
        "option_pcr_volume_14_28dte_max_dte": metrics.get("option_pcr_volume_14_28dte_max_dte"),
        "option_pcr_volume_14_28dte_asof": metrics.get("option_pcr_volume_14_28dte_asof"),
        "option_pcr_volume_14_28dte_provider": metrics.get("option_pcr_volume_14_28dte_provider"),
        "option_pcr_volume_14_28dte_error": metrics.get("option_pcr_volume_14_28dte_error"),
    }
    return row


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("adv_usd") is None,
            -(float(row.get("adv_usd") or 0)),
            row.get("rs_rating") is None,
            -(float(row.get("rs_rating") or 0)),
            row.get("symbol") or "",
        ),
    )


def _filter_options(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    def unique(field: str) -> list[str]:
        return sorted({str(row[field]) for row in rows if row.get(field)})

    return {
        "ibd_industries": unique("ibd_industry_group"),
        "gics_sectors": unique("gics_sector"),
        "ratings": unique("rating"),
    }


def _build_scan(
    output_dir: Path,
    *,
    generated_at: str,
    as_of_date: str,
    universe_as_of_date: str,
    price_as_of_date: str | None,
    scan_as_of_date: str | None,
    universe_updated_at: str | None,
    price_updated_at: str | None,
    scan_updated_at: str | None,
    git_push_hash: str | None,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    scan_dir = output_dir / "markets" / "us" / "scan"
    chunks_dir = scan_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunk_refs = []
    for index in range(0, len(rows), SCAN_CHUNK_SIZE):
        chunk = rows[index:index + SCAN_CHUNK_SIZE]
        chunk_num = index // SCAN_CHUNK_SIZE + 1
        rel = Path("markets/us/scan/chunks") / f"chunk-{chunk_num:04d}.json"
        _write_json(output_dir / rel, {
            "schema_version": SCAN_BUNDLE_SCHEMA_VERSION,
            "generated_at": generated_at,
            "as_of_date": as_of_date,
            "universe_as_of_date": universe_as_of_date,
            "price_as_of_date": price_as_of_date,
            "scan_as_of_date": scan_as_of_date,
            "universe_updated_at": universe_updated_at,
            "price_updated_at": price_updated_at,
            "scan_updated_at": scan_updated_at,
            "git_push_hash": git_push_hash,
            "run_id": "artifact-native-us",
            "chunk_index": chunk_num,
            "rows": chunk,
        })
        chunk_refs.append({"path": rel.as_posix(), "count": len(chunk)})

    default_filters = {"minVolume": None}
    manifest = {
        "schema_version": SCAN_BUNDLE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "universe_as_of_date": universe_as_of_date,
        "price_as_of_date": price_as_of_date,
        "scan_as_of_date": scan_as_of_date,
        "universe_updated_at": universe_updated_at,
        "price_updated_at": price_updated_at,
        "scan_updated_at": scan_updated_at,
        "git_push_hash": git_push_hash,
        "run_id": "artifact-native-us",
        "sort": {"field": "adv_rs", "order": "desc"},
        "default_page_size": 50,
        "chunk_size": SCAN_CHUNK_SIZE,
        "rows_total": len(rows),
        "default_filters": default_filters,
        "default_filtered_rows_total": len(rows),
        "filter_options": _filter_options(rows),
        "preset_screens": PRESET_SCREENS,
        "chunks": chunk_refs,
        "initial_rows": rows[:50],
        "preview_rows": rows[:10],
        "charts": {"path": "markets/us/charts/manifest.json", "limit": 0, "symbols_total": 0, "available": False},
    }
    _write_json(scan_dir / "manifest.json", manifest)
    _write_json(output_dir / "markets/us/charts/manifest.json", {
        "schema_version": "static-chart-index-v1",
        "generated_at": generated_at,
        "period": "6mo",
        "available": False,
        "symbols": [],
    })
    return manifest


def _build_home(
    *,
    generated_at: str,
    as_of_date: str,
    universe_as_of_date: str,
    price_as_of_date: str | None,
    scan_as_of_date: str | None,
    universe_updated_at: str | None,
    price_updated_at: str | None,
    scan_updated_at: str | None,
    git_push_hash: str | None,
    rows: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    top_groups = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        group = row.get("ibd_industry_group") or row.get("industry") or "No Group"
        groups.setdefault(group, []).append(row)
    for group, group_rows in groups.items():
        top_groups.append({
            "industry_group": group,
            "stock_count": len(group_rows),
            "avg_composite_score": round(
                sum(float(r.get("composite_score") or 0) for r in group_rows) / max(len(group_rows), 1), 2
            ),
            "top_symbol": group_rows[0].get("symbol"),
        })
    top_groups.sort(key=lambda item: (-item["avg_composite_score"], item["industry_group"]))
    return {
        "schema_version": STATIC_SITE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "market": DEFAULT_MARKET,
        "market_display_name": DEFAULT_MARKET_DISPLAY,
        "as_of_date": as_of_date,
        "universe_as_of_date": universe_as_of_date,
        "price_as_of_date": price_as_of_date,
        "scan_as_of_date": scan_as_of_date,
        "universe_updated_at": universe_updated_at,
        "price_updated_at": price_updated_at,
        "scan_updated_at": scan_updated_at,
        "git_push_hash": git_push_hash,
        "freshness": {
            "universe_as_of_date": universe_as_of_date,
            "price_as_of_date": price_as_of_date,
            "scan_as_of_date": scan_as_of_date,
            "universe_updated_at": universe_updated_at,
            "price_updated_at": price_updated_at,
            "scan_updated_at": scan_updated_at,
            "breadth_latest_date": None,
            "groups_latest_date": scan_as_of_date or as_of_date,
            "foundation_update_source_revision": coverage.get("source_revision"),
        },
        "coverage": coverage,
        "key_markets": [],
        "top_groups": top_groups[:10],
    }


def build_static_site_from_artifacts(
    *,
    foundation_update: Path,
    output_dir: Path,
    daily_price: Path | None = None,
    scan_metrics: Path | None = None,
    group_rank: Path | None = None,
    listing_profile: Path | None = None,
    etf_profile: Path | None = None,
) -> dict[str, Any]:
    started_at = time.monotonic()
    generated_at = _utc_now()
    _log("Build static site from artifacts starting", output_dir=output_dir)
    weekly = _read_json(foundation_update)
    _log("Loaded foundation artifact", path=foundation_update, rows=len(weekly.get("rows") or []))
    daily = _read_json(daily_price) if daily_price else None
    _log("Loaded daily price artifact", path=daily_price, rows=len((daily or {}).get("rows") or []))
    metrics_bundle = _read_json(scan_metrics) if scan_metrics else None
    _log("Loaded scan metrics artifact", path=scan_metrics, rows=len((metrics_bundle or {}).get("rows") or []))
    group_bundle = _read_json(group_rank) if group_rank else None
    _log("Loaded group rank artifact", path=group_rank, rows=len((group_bundle or {}).get("rows") or []))
    listing_bundle = _read_json(listing_profile) if listing_profile else None
    _log("Loaded listing profile artifact", path=listing_profile, rows=len((listing_bundle or {}).get("rows") or []))
    etf_bundle = _read_json(etf_profile) if etf_profile else None
    _log("Loaded ETF profile artifact", path=etf_profile, rows=len((etf_bundle or {}).get("rows") or []))
    latest_prices = _latest_daily_by_symbol(daily)
    metrics_by_symbol = _metrics_by_symbol(metrics_bundle)
    group_by_symbol = _rows_by_symbol(group_bundle)
    listing_by_symbol = _rows_by_symbol(listing_bundle)
    etf_by_symbol = _rows_by_symbol(etf_bundle)
    benchmark_prices = (latest_prices.get("SPY") or {}).get("prices") or []
    universe_as_of_date = str(weekly.get("as_of_date") or datetime.now(timezone.utc).date().isoformat())
    price_as_of_date = str(daily.get("as_of_date")) if daily and daily.get("as_of_date") else None
    scan_as_of_date = str(metrics_bundle.get("as_of_date")) if metrics_bundle and metrics_bundle.get("as_of_date") else None
    universe_updated_at = str(weekly.get("generated_at")) if weekly.get("generated_at") else None
    price_updated_at = str(daily.get("generated_at")) if daily and daily.get("generated_at") else None
    scan_updated_at = str(metrics_bundle.get("generated_at")) if metrics_bundle and metrics_bundle.get("generated_at") else None
    git_push_hash = _git_push_hash()
    as_of_date = universe_as_of_date
    coverage = dict((weekly.get("coverage") or {}))
    coverage["source_revision"] = weekly.get("source_revision")

    rows = []
    _log("Building scan rows starting")
    for source_row in _weekly_rows(weekly):
        payload = _row_payload(source_row)
        if not payload.get("symbol"):
            continue
        symbol = payload["symbol"]
        rows.append(_scan_row(
            payload,
            latest_prices.get(symbol),
            benchmark_prices,
            metrics_by_symbol.get(symbol),
            group_by_symbol.get(symbol),
            listing_by_symbol.get(symbol),
            etf_by_symbol.get(symbol),
        ))
    rows = _sort_rows(rows)
    _log("Built and sorted scan rows", rows=len(rows), elapsed_seconds=round(time.monotonic() - started_at, 1))
    option_history_date = scan_as_of_date or price_as_of_date or as_of_date
    _log("Building option tracking pool", as_of_date=option_history_date)
    option_tracking_pool = _build_option_tracking_pool(
        rows,
        generated_at=generated_at,
        as_of_date=option_history_date,
    )
    _log("Built option tracking pool", active_count=option_tracking_pool["active_count"], max_symbols=option_tracking_pool["max_symbols"])
    _enrich_rows_with_option_pcr(rows, tracked_symbols=option_tracking_pool["active_symbols"])

    output_dir.mkdir(parents=True, exist_ok=True)
    _log("Writing option tracking pool")
    _write_option_tracking_pool(output_dir, option_tracking_pool)
    _log("Merging option aggregate history")
    _merge_option_history(rows, output_dir=output_dir, generated_at=generated_at, as_of_date=option_history_date)
    _log("Merging option contract SQLite")
    _merge_option_contract_sqlite(rows, output_dir=output_dir, generated_at=generated_at, as_of_date=option_history_date)
    _log("Writing option D1 import SQL")
    _write_option_contract_d1_import_sql(rows, output_dir=output_dir, generated_at=generated_at, as_of_date=option_history_date)
    _log("Merging option contract JSON history")
    _merge_option_contract_history(rows, output_dir=output_dir, generated_at=generated_at, as_of_date=option_history_date)
    _log("Building scan payload")
    scan_manifest = _build_scan(
        output_dir,
        generated_at=generated_at,
        as_of_date=as_of_date,
        universe_as_of_date=universe_as_of_date,
        price_as_of_date=price_as_of_date,
        scan_as_of_date=scan_as_of_date,
        universe_updated_at=universe_updated_at,
        price_updated_at=price_updated_at,
        scan_updated_at=scan_updated_at,
        git_push_hash=git_push_hash,
        rows=rows,
    )

    breadth_payload = {
        "available": False,
        "message": f"Breadth data is not available in artifact-native static export for {DEFAULT_MARKET}.",
        "generated_at": generated_at,
        "payload": {},
    }
    groups_payload = {
        "available": False,
        "message": f"Group rankings are not available in artifact-native static export for {DEFAULT_MARKET}.",
        "generated_at": generated_at,
        "payload": {},
    }
    home_payload = _build_home(
        generated_at=generated_at,
        as_of_date=as_of_date,
        universe_as_of_date=universe_as_of_date,
        price_as_of_date=price_as_of_date,
        scan_as_of_date=scan_as_of_date,
        universe_updated_at=universe_updated_at,
        price_updated_at=price_updated_at,
        scan_updated_at=scan_updated_at,
        git_push_hash=git_push_hash,
        rows=rows,
        coverage=coverage,
    )
    _log("Writing market payloads")
    _write_json(output_dir / "markets/us/home.json", home_payload)
    _write_json(output_dir / "markets/us/breadth.json", breadth_payload)
    _write_json(output_dir / "markets/us/groups.json", groups_payload)

    market_entry = {
        "market": DEFAULT_MARKET,
        "display_name": DEFAULT_MARKET_DISPLAY,
        "as_of_date": as_of_date,
        "universe_as_of_date": universe_as_of_date,
        "price_as_of_date": price_as_of_date,
        "scan_as_of_date": scan_as_of_date,
        "universe_updated_at": universe_updated_at,
        "price_updated_at": price_updated_at,
        "scan_updated_at": scan_updated_at,
        "git_push_hash": git_push_hash,
        "features": {"scan": True, "breadth": False, "groups": False, "charts": False},
        "pages": {
            "home": {"path": "markets/us/home.json"},
            "scan": {"path": "markets/us/scan/manifest.json"},
            "breadth": {"path": "markets/us/breadth.json"},
            "groups": {"path": "markets/us/groups.json"},
        },
        "assets": {
            "charts": {"path": "markets/us/charts/manifest.json", "limit": 0, "symbols_total": 0},
            "option_put_liquidity_history": {"path": "markets/us/options/put-liquidity-history.json", "window_days": 7},
            "option_chain_tracking_pool": {"path": "markets/us/options/option-chain-tracking-pool.json", "max_symbols": OPTION_CHAIN_MAX_FETCH_SYMBOLS, "retention_days": OPTION_CHAIN_TRACKING_RETENTION_DAYS},
            "option_put_contract_liquidity_latest": {"path": "markets/us/options/put-contract-liquidity-latest.json"},
            "option_put_contract_liquidity_history": {"path": "markets/us/options/put-contract-liquidity-history.json", "window_days": 90},
            "option_contract_liquidity_sqlite": {"path": "markets/us/options/option-contract-liquidity.sqlite", "retention_days": 90},
            "option_contract_liquidity_d1_import": {"path": "markets/us/options/option-contract-liquidity-d1.sql", "retention_days": 90},
        },
        "freshness": home_payload["freshness"],
    }
    manifest = {
        "schema_version": STATIC_SITE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "universe_as_of_date": universe_as_of_date,
        "price_as_of_date": price_as_of_date,
        "scan_as_of_date": scan_as_of_date,
        "universe_updated_at": universe_updated_at,
        "price_updated_at": price_updated_at,
        "scan_updated_at": scan_updated_at,
        "git_push_hash": git_push_hash,
        "freshness": home_payload["freshness"],
        "default_market": DEFAULT_MARKET,
        "supported_markets": [DEFAULT_MARKET],
        "features": dict(market_entry["features"]),
        "pages": dict(market_entry["pages"]),
        "assets": dict(market_entry["assets"]),
        "markets": {DEFAULT_MARKET: market_entry},
        "warnings": [
            "Artifact-native export does not require Postgres; breadth, group rankings, and chart payloads are disabled until artifact-native inputs are available."
        ],
    }
    _log("Writing manifests")
    _write_json(output_dir / "manifest.json", manifest)
    _write_json(output_dir / "markets/us/manifest.market.json", {
        "schema_version": STATIC_SITE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "market": DEFAULT_MARKET,
        "entry": market_entry,
        "warnings": manifest["warnings"],
    })
    _log("Build static site from artifacts complete", rows=len(rows), elapsed_seconds=round(time.monotonic() - started_at, 1))
    return {"rows_total": len(rows), "scan_manifest": scan_manifest, "manifest": manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foundation-update", required=True, type=Path)
    parser.add_argument("--daily-price", type=Path, default=None)
    parser.add_argument("--scan-metrics", type=Path, default=None)
    parser.add_argument("--group-rank", type=Path, default=None)
    parser.add_argument("--listing-profile", type=Path, default=None)
    parser.add_argument("--etf-profile", type=Path, default=None)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    summary = build_static_site_from_artifacts(
        foundation_update=args.foundation_update,
        daily_price=args.daily_price,
        scan_metrics=args.scan_metrics,
        group_rank=args.group_rank,
        listing_profile=args.listing_profile,
        etf_profile=args.etf_profile,
        output_dir=args.output_dir,
    )
    print(json.dumps({"rows_total": summary["rows_total"], "output_dir": str(args.output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
