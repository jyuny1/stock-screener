"""Schwab option-chain derived metrics for scan rows."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.config import settings
from app.models.scan_result import ScanResult
from app.models.stock_universe import StockUniverse
from app.services.schwab_token_service import SchwabTokenService

logger = logging.getLogger(__name__)

SCHWAB_MARKETDATA_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
PCR_VOLUME_30_45DTE_FIELD = "option_pcr_volume_30_45dte"


@dataclass(frozen=True)
class OptionVolumePcrMetric:
    symbol: str
    min_dte: int
    max_dte: int
    put_volume: int
    call_volume: int
    pcr: float | None
    expirations: int
    contract_count: int
    asof: str
    provider: str = "schwab"

    def to_details_patch(self) -> dict[str, Any]:
        return {
            "option_pcr_volume_30_45dte": self.pcr,
            "option_put_volume_30_45dte": self.put_volume,
            "option_call_volume_30_45dte": self.call_volume,
            "option_pcr_volume_30_45dte_expirations": self.expirations,
            "option_pcr_volume_30_45dte_contracts": self.contract_count,
            "option_pcr_volume_30_45dte_min_dte": self.min_dte,
            "option_pcr_volume_30_45dte_max_dte": self.max_dte,
            "option_pcr_volume_30_45dte_asof": self.asof,
            "option_pcr_volume_30_45dte_provider": self.provider,
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
        min_dte: int = 30,
        max_dte: int = 45,
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
            pcr=(put_volume / call_volume) if call_volume > 0 else None,
            expirations=len(expiration_keys),
            contract_count=len(put_contracts) + len(call_contracts),
            asof=datetime.now(UTC).isoformat(),
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
    """Best-effort enrichment of US scan rows with 30-45 DTE volume PCR."""
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
        .filter(ScanResult.scan_id == scan_id)
        .filter((StockUniverse.market == "US") | (StockUniverse.market.is_(None)))
        .order_by(ScanResult.composite_score.desc().nullslast(), ScanResult.symbol.asc())
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
        if details.get(PCR_VOLUME_30_45DTE_FIELD) is not None:
            continue
        try:
            metric = service.compute_volume_pcr(row.symbol, min_dte=min_dte, max_dte=max_dte)
        except Exception as exc:  # noqa: BLE001 - enrichment must never fail scan finalization
            logger.info("Option PCR enrichment skipped for %s: %s", row.symbol, exc)
            details.update({
                "option_pcr_volume_30_45dte_error": str(exc)[:200],
                "option_pcr_volume_30_45dte_provider": "schwab",
                "option_pcr_volume_30_45dte_min_dte": min_dte,
                "option_pcr_volume_30_45dte_max_dte": max_dte,
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
