"""Schwab option-chain derived metrics for scan rows."""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.config import settings
from app.models.scan_result import ScanResult
from app.models.stock import StockFundamental
from app.models.stock_universe import StockUniverse
from app.services.schwab_token_service import SchwabTokenService

logger = logging.getLogger(__name__)

SCHWAB_MARKETDATA_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
PCR_VOLUME_14_28DTE_FIELD = "option_pcr_volume_14_28dte"


@dataclass(frozen=True)
class OptionPutContractLiquidity:
    symbol: str
    contract_symbol: str | None
    expiration: str | None
    expiration_date: str | None
    dte: int | None
    dte_at_snapshot: int | None
    schwab_dte: int | None
    strike: float | None
    bid: float | None
    ask: float | None
    last: float | None
    mark: float | None
    put_volume: int
    put_oi: int
    delta: float | None
    iv: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "underlying_symbol": self.symbol,
            "option_type": "PUT",
            "contract_symbol": self.contract_symbol,
            "expiration": self.expiration,
            "expiration_date": self.expiration_date,
            "dte": self.dte,
            "dte_at_snapshot": self.dte_at_snapshot,
            "schwab_dte": self.schwab_dte,
            "strike": self.strike,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "mark": self.mark,
            "put_volume": self.put_volume,
            "put_oi": self.put_oi,
            "delta": self.delta,
            "iv": self.iv,
        }


@dataclass(frozen=True)
class OptionVolumePcrMetric:
    symbol: str
    min_dte: int
    max_dte: int
    put_volume: int
    call_volume: int
    put_oi: int
    call_oi: int
    pcr: float | None
    expirations: int
    contract_count: int
    asof: str
    provider: str = "schwab"
    put_contracts: tuple[OptionPutContractLiquidity, ...] = ()

    def to_details_patch(self) -> dict[str, Any]:
        return {
            "option_pcr_volume_14_28dte": self.pcr,
            "option_put_volume_14_28dte": self.put_volume,
            "option_call_volume_14_28dte": self.call_volume,
            "option_put_oi_14_28dte": self.put_oi,
            "option_call_oi_14_28dte": self.call_oi,
            "option_put_contracts_14_28dte": [contract.to_dict() for contract in self.put_contracts],
            "option_put_contracts_14_28dte_count": len(self.put_contracts),
            "option_pcr_volume_14_28dte_expirations": self.expirations,
            "option_pcr_volume_14_28dte_contracts": self.contract_count,
            "option_pcr_volume_14_28dte_min_dte": self.min_dte,
            "option_pcr_volume_14_28dte_max_dte": self.max_dte,
            "option_pcr_volume_14_28dte_asof": self.asof,
            "option_pcr_volume_14_28dte_provider": self.provider,
        }


class SchwabOptionMetricsService:
    """Compute option-chain metrics from Schwab Market Data.

    The scan table needs ticker-level PCR, not a strike-level option candidate.
    Schwab /chains is single-symbol, so callers should rate-limit and tolerate
    partial coverage when credentials or chain data are unavailable.
    """

    def __init__(
        self,
        *,
        base_url: str = SCHWAB_MARKETDATA_BASE_URL,
        timeout_seconds: float = 30.0,
        access_token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._access_token = access_token

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        token = os.environ.get("SCHWAB_ACCESS_TOKEN")
        if token:
            self._access_token = token
            return token
        logger.info(
            "Schwab access token missing; refreshing from SCHWAB_REFRESH_TOKEN "
            "before option PCR enrichment"
        )
        token_pair = SchwabTokenService.from_env().refresh_from_env()
        os.environ["SCHWAB_ACCESS_TOKEN"] = token_pair.access_token
        os.environ["SCHWAB_REFRESH_TOKEN"] = token_pair.new_refresh_token
        self._access_token = token_pair.access_token
        return self._access_token

    def compute_volume_pcr(
        self,
        symbol: str,
        *,
        min_dte: int = 14,
        max_dte: int = 28,
        today: date | None = None,
    ) -> OptionVolumePcrMetric:
        symbol = symbol.upper().strip()
        current_date = today or datetime.now(UTC).date()
        from_date = current_date + timedelta(days=min_dte)
        to_date = current_date + timedelta(days=max_dte)
        payload = self._get_chains(symbol, from_date=from_date, to_date=to_date)

        put_contracts = self._flatten_contracts(payload.get("putExpDateMap"))
        call_contracts = self._flatten_contracts(payload.get("callExpDateMap"))
        put_volume = sum(_safe_int(c.get("totalVolume")) for c in put_contracts)
        call_volume = sum(_safe_int(c.get("totalVolume")) for c in call_contracts)
        put_oi = sum(_safe_int(c.get("openInterest")) for c in put_contracts)
        call_oi = sum(_safe_int(c.get("openInterest")) for c in call_contracts)
        put_contract_liquidity = tuple(
            _normalize_put_contract(symbol, contract, snapshot_date=current_date)
            for contract in put_contracts
        )
        expiration_keys = set()
        for contract in [*put_contracts, *call_contracts]:
            exp = contract.get("expirationDate")
            if exp:
                expiration_keys.add(str(exp)[:10])

        return OptionVolumePcrMetric(
            symbol=symbol,
            min_dte=min_dte,
            max_dte=max_dte,
            put_volume=put_volume,
            call_volume=call_volume,
            put_oi=put_oi,
            call_oi=call_oi,
            pcr=(put_volume / call_volume) if call_volume > 0 else None,
            expirations=len(expiration_keys),
            contract_count=len(put_contracts) + len(call_contracts),
            asof=datetime.now(UTC).isoformat(),
            put_contracts=put_contract_liquidity,
        )

    def _get_chains(self, symbol: str, *, from_date: date, to_date: date) -> dict[str, Any]:
        response = requests.get(
            f"{self.base_url}/chains",
            params={
                "symbol": symbol,
                "contractType": "ALL",
                "strategy": "SINGLE",
                "fromDate": from_date.isoformat(),
                "toDate": to_date.isoformat(),
                "includeUnderlyingQuote": "false",
                "optionType": "ALL",
            },
            headers={
                "Authorization": f"Bearer {self._get_access_token()}",
                "Accept": "application/json",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _flatten_contracts(exp_date_map: Any) -> list[dict[str, Any]]:
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


def enrich_scan_results_with_option_pcr(
    db: Session,
    scan_id: str,
    *,
    metrics_service: SchwabOptionMetricsService | None = None,
) -> int:
    """Best-effort enrichment of US scan rows with 14-28 DTE volume PCR."""
    if not settings.scan_option_pcr_enabled:
        return 0
    if metrics_service is None and not _has_schwab_auth_material():
        logger.info(
            "Option PCR enrichment skipped for scan %s: missing SCHWAB_ACCESS_TOKEN or refresh credentials",
            scan_id,
        )
        return 0

    min_dte = settings.scan_option_pcr_min_dte
    max_dte = settings.scan_option_pcr_max_dte
    max_symbols = settings.scan_option_pcr_max_symbols_per_scan
    sleep_seconds = max(0.0, settings.scan_option_pcr_request_interval_seconds)

    query = (
        db.query(ScanResult)
        .outerjoin(StockUniverse, ScanResult.symbol == StockUniverse.symbol)
        .outerjoin(StockFundamental, ScanResult.symbol == StockFundamental.symbol)
        .filter(ScanResult.scan_id == scan_id)
        .filter((StockUniverse.market == "US") | (StockUniverse.market.is_(None)))
        .order_by(
            StockFundamental.adv_usd.desc().nullslast(),
            ScanResult.rs_rating.desc().nullslast(),
            ScanResult.symbol.asc(),
        )
    )
    if max_symbols and max_symbols > 0:
        query = query.limit(max_symbols)
    rows = query.all()
    if not rows:
        return 0

    service = metrics_service or SchwabOptionMetricsService()
    updated = 0
    for index, row in enumerate(rows):
        details = dict(row.details or {})
        if details.get(PCR_VOLUME_14_28DTE_FIELD) is not None:
            continue
        try:
            metric = service.compute_volume_pcr(row.symbol, min_dte=min_dte, max_dte=max_dte)
        except Exception as exc:  # noqa: BLE001 - enrichment must never fail scan finalization
            logger.info("Option PCR enrichment skipped for %s: %s", row.symbol, exc)
            details.update({
                "option_pcr_volume_14_28dte_error": str(exc)[:200],
                "option_pcr_volume_14_28dte_provider": "schwab",
                "option_pcr_volume_14_28dte_min_dte": min_dte,
                "option_pcr_volume_14_28dte_max_dte": max_dte,
            })
            row.details = details
            continue
        details.update(metric.to_details_patch())
        row.details = details
        updated += 1
        if sleep_seconds and index < len(rows) - 1:
            time.sleep(sleep_seconds)

    db.commit()
    logger.info("Option PCR enrichment complete for scan %s: updated=%d rows=%d", scan_id, updated, len(rows))
    return updated


def _has_schwab_auth_material() -> bool:
    if os.environ.get("SCHWAB_ACCESS_TOKEN"):
        return True
    return all(
        os.environ.get(name)
        for name in ("SCHWAB_CLIENT_ID", "SCHWAB_CLIENT_SECRET", "SCHWAB_REFRESH_TOKEN")
    )


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _safe_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _first_text(contract: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = contract.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_float(contract: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        parsed = _safe_float(contract.get(key))
        if parsed is not None:
            return parsed
    return None


def _first_int(contract: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = contract.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _date_key(value: Any) -> str | None:
    text = value.strip() if isinstance(value, str) and value.strip() else None
    return text[:10] if text else None


def _calculated_dte(snapshot_date: date, expiration_date: str | None) -> int | None:
    if not expiration_date:
        return None
    try:
        expiration = datetime.fromisoformat(expiration_date[:10]).date()
    except ValueError:
        return None
    return (expiration - snapshot_date).days


def _normalize_put_contract(symbol: str, contract: dict[str, Any], *, snapshot_date: date) -> OptionPutContractLiquidity:
    expiration_date = _date_key(_first_text(contract, "expirationDate", "expiration"))
    schwab_dte = _first_int(contract, "daysToExpiration", "dte")
    dte_at_snapshot = _calculated_dte(snapshot_date, expiration_date)
    return OptionPutContractLiquidity(
        symbol=symbol,
        contract_symbol=_first_text(contract, "symbol", "contractSymbol"),
        expiration=expiration_date,
        expiration_date=expiration_date,
        dte=dte_at_snapshot,
        dte_at_snapshot=dte_at_snapshot,
        schwab_dte=schwab_dte,
        strike=_first_float(contract, "strikePrice", "strike"),
        bid=_first_float(contract, "bidPrice", "bid"),
        ask=_first_float(contract, "askPrice", "ask"),
        last=_first_float(contract, "lastPrice", "last"),
        mark=_first_float(contract, "markPrice", "mark"),
        put_volume=_safe_int(contract.get("totalVolume", contract.get("volume"))),
        put_oi=_safe_int(contract.get("openInterest")),
        delta=_first_float(contract, "delta"),
        iv=_first_float(contract, "volatility", "impliedVolatility", "iv"),
    )
