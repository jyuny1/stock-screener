"""AU market universe ingestion adapter with deterministic canonicalization.

Australia lists primarily on the Australian Securities Exchange (MIC: XASX).
ASX symbols are alphanumeric issuer codes (BHP, CBA, WES) that Yahoo Finance
suffixes with ``.AX``. This adapter normalizes ASX-style and Yahoo-style inputs
to ``<TICKER>.AX`` and emits a single deterministic row per canonical symbol.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from ..domain.universe.ingestion import (
    CanonicalUniverseIngestionResult,
    CanonicalUniverseRow,
    RejectedUniverseRow,
    UniverseSourceProvenance,
)
from .security_master_service import security_master_resolver

_AU_EXCHANGE_ALIASES: dict[str, str] = {
    "ASX": "XASX",
    "XASX": "XASX",
}

_APPROVED_AU_SOURCES: frozenset[str] = frozenset(
    {
        "asx_official_public_csv",
        "au_manual_csv",
        "au_reference_bundle",
        "asx_official",
    }
)

_AU_LOCAL_CODE_RE = re.compile(r"^[A-Z0-9]{2,6}$")


AUCanonicalUniverseRow = CanonicalUniverseRow
AURejectedUniverseRow = RejectedUniverseRow
AUCanonicalizationResult = CanonicalUniverseIngestionResult


class AUUniverseIngestionAdapter:
    """Normalize and validate AU universe rows for deterministic snapshots."""

    @staticmethod
    def normalize_source_name(source_name: str) -> str:
        normalized = (source_name or "").strip().lower().replace("-", "_")
        if not normalized:
            raise ValueError("source_name must be provided")
        return normalized

    @classmethod
    def is_approved_source(cls, source_name: str) -> bool:
        normalized = cls.normalize_source_name(source_name)
        if normalized in _APPROVED_AU_SOURCES:
            return True
        return normalized.startswith("asx_") or normalized.startswith("au_")

    @staticmethod
    def _normalize_source_symbol(raw_symbol: Any) -> str:
        symbol = str(raw_symbol or "").strip().upper().replace(" ", "")
        if symbol.startswith("$"):
            symbol = symbol[1:]
        return symbol

    @staticmethod
    def _normalize_exchange(raw_exchange: Any) -> str:
        exchange = str(raw_exchange or "").strip().upper() or "XASX"
        normalized = _AU_EXCHANGE_ALIASES.get(exchange)
        if normalized is None:
            raise ValueError(
                f"Unsupported AU exchange '{exchange}'. Expected one of: ASX, XASX"
            )
        return normalized

    @staticmethod
    def _normalize_au_local_code(source_symbol: str) -> str:
        token = source_symbol
        for prefix in ("ASX:", "XASX:"):
            if token.startswith(prefix):
                token = token[len(prefix):]
                break
        if token.endswith(".AX"):
            token = token[:-3]

        if not _AU_LOCAL_CODE_RE.fullmatch(token):
            raise ValueError(
                f"Invalid AU symbol '{source_symbol}'. "
                "Expected 2-6 alphanumeric local code with optional .AX suffix."
            )
        return token

    @staticmethod
    def _parse_market_cap(raw_value: Any) -> float | None:
        if raw_value is None:
            return None
        if isinstance(raw_value, (int, float)):
            return float(raw_value)

        raw = str(raw_value).strip().upper().replace(",", "")
        if not raw or raw == "-":
            return None

        multiplier = 1.0
        if raw.endswith("B"):
            multiplier = 1e9
            raw = raw[:-1]
        elif raw.endswith("M"):
            multiplier = 1e6
            raw = raw[:-1]
        elif raw.endswith("K"):
            multiplier = 1e3
            raw = raw[:-1]

        try:
            return float(raw) * multiplier
        except ValueError:
            return None

    @staticmethod
    def _hash_payload(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _selection_key(row: AUCanonicalUniverseRow) -> tuple[str, int]:
        row_number = row.provenance.source_row_number or 0
        return (row.provenance.source_symbol, row_number)

    @staticmethod
    def _prefer_text(primary: str, fallback: str) -> str:
        return primary if primary.strip() else fallback

    def _canonical_payload(
        self,
        row: AUCanonicalUniverseRow,
        *,
        name: str,
        sector: str,
        industry: str,
        market_cap: float | None,
    ) -> dict[str, Any]:
        return {
            "symbol": row.symbol,
            "market": row.market,
            "exchange": row.mic,
            "local_code": row.local_code,
            "name": name,
            "sector": sector,
            "industry": industry,
            "market_cap": market_cap,
            "source_name": row.provenance.source_name,
            "snapshot_id": row.provenance.snapshot_id,
        }

    def _merge_duplicate_rows(
        self,
        first: AUCanonicalUniverseRow,
        second: AUCanonicalUniverseRow,
    ) -> AUCanonicalUniverseRow:
        if self._selection_key(first) <= self._selection_key(second):
            primary = first
            secondary = second
        else:
            primary = second
            secondary = first

        merged_name = self._prefer_text(primary.name, secondary.name)
        merged_sector = self._prefer_text(primary.sector, secondary.sector)
        merged_industry = self._prefer_text(primary.industry, secondary.industry)
        merged_market_cap = (
            primary.market_cap if primary.market_cap is not None else secondary.market_cap
        )
        merged_source_metadata = (
            primary.provenance.source_metadata
            if primary.provenance.source_metadata
            else secondary.provenance.source_metadata
        )
        merged_row_hash = self._hash_payload(
            self._canonical_payload(
                primary,
                name=merged_name,
                sector=merged_sector,
                industry=merged_industry,
                market_cap=merged_market_cap,
            )
        )
        merged_provenance = replace(
            primary.provenance,
            source_metadata=merged_source_metadata,
            row_hash=merged_row_hash,
        )
        return replace(
            primary,
            name=merged_name,
            sector=merged_sector,
            industry=merged_industry,
            market_cap=merged_market_cap,
            provenance=merged_provenance,
        )

    def canonicalize_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        source_name: str,
        snapshot_id: str,
        snapshot_as_of: str | None = None,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> AUCanonicalizationResult:
        normalized_source_name = self.normalize_source_name(source_name)
        if not self.is_approved_source(normalized_source_name):
            raise ValueError(
                f"Unapproved AU source '{source_name}'. "
                "Use an approved AU source identifier."
            )

        normalized_snapshot_id = (snapshot_id or "").strip()
        if not normalized_snapshot_id:
            raise ValueError("snapshot_id must be provided")

        metadata = dict(source_metadata or {})
        canonical_by_symbol: dict[str, AUCanonicalUniverseRow] = {}
        rejected_rows: list[AURejectedUniverseRow] = []

        for index, raw_row in enumerate(rows, start=1):
            source_symbol = self._normalize_source_symbol(
                raw_row.get("symbol")
                or raw_row.get("local_code")
                or raw_row.get("ticker")
            )
            if not source_symbol:
                rejected_rows.append(
                    AURejectedUniverseRow(
                        source_row_number=index,
                        source_symbol="",
                        reason="Missing symbol/local_code/ticker",
                    )
                )
                continue

            try:
                exchange = self._normalize_exchange(raw_row.get("exchange"))
                local_code = self._normalize_au_local_code(source_symbol)
                identity = security_master_resolver.resolve_identity(
                    symbol=f"{local_code}.AX",
                    market="AU",
                    exchange=exchange,
                    local_code=local_code,
                )
                row_name = str(
                    raw_row.get("name") or raw_row.get("company") or ""
                ).strip()
                row_sector = str(raw_row.get("sector") or "").strip()
                row_industry = str(raw_row.get("industry") or "").strip()
                row_market_cap = self._parse_market_cap(
                    raw_row.get("market_cap") or raw_row.get("marketcap")
                )
                row_listing_tier = raw_row.get("listing_tier") or raw_row.get("board")

                lineage_payload = {
                    "source_name": normalized_source_name,
                    "snapshot_id": normalized_snapshot_id,
                    "source_row_number": index,
                    "source_symbol": source_symbol,
                    "canonical_symbol": identity.canonical_symbol,
                }
                canonical_exchange = identity.exchange or "XASX"
                canonical_payload = {
                    "symbol": identity.canonical_symbol,
                    "market": identity.market,
                    "exchange": canonical_exchange,
                    "local_code": identity.local_code,
                    "name": row_name,
                    "sector": row_sector,
                    "industry": row_industry,
                    "market_cap": row_market_cap,
                    "source_name": normalized_source_name,
                    "snapshot_id": normalized_snapshot_id,
                }
                canonical_row = CanonicalUniverseRow(
                    symbol=identity.canonical_symbol,
                    name=row_name,
                    market=identity.market,
                    mic=canonical_exchange,
                    currency=identity.currency,
                    timezone=identity.timezone,
                    local_code=identity.local_code,
                    listing_tier=row_listing_tier,
                    sector=row_sector,
                    industry=row_industry,
                    market_cap=row_market_cap,
                    provenance=UniverseSourceProvenance(
                        source_name=normalized_source_name,
                        source_symbol=source_symbol,
                        source_row_number=index,
                        snapshot_id=normalized_snapshot_id,
                        snapshot_as_of=snapshot_as_of,
                        source_metadata=metadata,
                        lineage_hash=self._hash_payload(lineage_payload),
                        row_hash=self._hash_payload(canonical_payload),
                    ),
                )

                existing = canonical_by_symbol.get(canonical_row.symbol)
                if existing is None:
                    canonical_by_symbol[canonical_row.symbol] = canonical_row
                else:
                    canonical_by_symbol[canonical_row.symbol] = self._merge_duplicate_rows(
                        existing,
                        canonical_row,
                    )
            except ValueError as exc:
                rejected_rows.append(
                    RejectedUniverseRow(
                        source_row_number=index,
                        source_symbol=source_symbol,
                        reason=str(exc),
                    )
                )

        canonical_rows = tuple(
            sorted(canonical_by_symbol.values(), key=lambda row: row.symbol)
        )
        rejected_rows_tuple = tuple(
            sorted(rejected_rows, key=lambda row: row.source_row_number)
        )
        return CanonicalUniverseIngestionResult(
            canonical_rows=canonical_rows,
            rejected_rows=rejected_rows_tuple,
        )


au_universe_ingestion_adapter = AUUniverseIngestionAdapter()
