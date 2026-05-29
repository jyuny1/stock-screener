"""SecurityMaster resolver and deterministic symbol/market normalization utilities.

This service is the single source of truth for deriving canonical identity fields
(symbol/market/exchange/currency/timezone/local_code) from mixed legacy inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..domain.markets import (
    UnsupportedMarketError,
    get_market_catalog,
    market_registry,
    mic_alias_registry,
)


_SUPPORTED_MARKETS = set(market_registry.supported_market_codes())

_MARKET_DEFAULTS: dict[str, tuple[str, str]] = {
    profile.market.code: (profile.currency, profile.timezone_name)
    for profile in market_registry.profiles()
}

_MARKET_BY_SUFFIX: tuple[tuple[str, str], ...] = (
    (".HK", "HK"),
    (".NS", "IN"),
    (".BO", "IN"),
    (".KS", "KR"),
    (".KQ", "KR"),
    (".TWO", "TW"),
    (".TW", "TW"),
    (".SS", "CN"),
    (".SZ", "CN"),
    (".BJ", "CN"),
    (".SI", "SG"),
    (".TO", "CA"),
    (".V", "CA"),
    (".T", "JP"),
    (".DE", "DE"),
    (".F", "DE"),
)

_SUFFIX_BY_MARKET: dict[str, str] = {
    "HK": ".HK",
    "IN": ".NS",
    "JP": ".T",
    "KR": ".KS",
    "TW": ".TW",
    "CN": ".SS",
    "SG": ".SI",
    "CA": ".TO",
    "DE": ".DE",
}

_SUFFIX_BY_EXCHANGE: dict[str, str] = {
    "TSX": ".TO",
    "XTSE": ".TO",
    "TSXV": ".V",
    "XTNX": ".V",
    "XETR": ".DE",
    "XETRA": ".DE",
    "XFRA": ".F",
    "FRA": ".F",
    "FWB": ".F",
    "NSE": ".NS",
    "XNSE": ".NS",
    "XBOM": ".BO",
    "KOSPI": ".KS",
    "KOSDAQ": ".KQ",
    "KRX": ".KS",
    "XKRX": ".KS",
    "TWSE": ".TW",
    "XTAI": ".TW",
    "TPEX": ".TWO",
    "SSE": ".SS",
    "SHSE": ".SS",
    "XSHG": ".SS",
    "SZSE": ".SZ",
    "XSHE": ".SZ",
    "BJSE": ".BJ",
    "XBSE": ".BJ",
    "XBEI": ".BJ",
    "SGX": ".SI",
    "SES": ".SI",
    "XSES": ".SI",
}

_SUFFIX_BY_MARKET_EXCHANGE: dict[tuple[str, str], str] = {
    ("IN", "BSE"): ".BO",
    ("CN", "BSE"): ".BJ",
}


@dataclass(frozen=True)
class SecurityIdentity:
    """Canonical security identity fields derived by SecurityMaster."""

    normalized_symbol: str
    canonical_symbol: str
    market: str
    exchange: Optional[str]
    mic: Optional[str]
    currency: str
    timezone: str
    local_code: str


class SecurityMasterResolver:
    """Deterministic resolver for symbol -> market/exchange/currency identity."""

    @staticmethod
    def normalize_symbol(symbol: str | None) -> str:
        """Normalize a symbol without applying market-specific transforms."""
        normalized = (symbol or "").strip().upper()
        if normalized.startswith("$"):
            normalized = normalized[1:]
        return normalized

    @staticmethod
    def normalize_exchange(exchange: str | None) -> str | None:
        normalized = (exchange or "").strip().upper()
        return normalized or None

    @staticmethod
    def normalize_market(market: str | None) -> str | None:
        try:
            return market_registry.profile(market or "").market.code
        except UnsupportedMarketError:
            return None

    @staticmethod
    def resolve_exchange_mic(market: str | None, exchange: str | None) -> str | None:
        if market:
            resolved = mic_alias_registry.resolve(market, exchange)
        else:
            resolved = mic_alias_registry.resolve_global(exchange)
        return resolved.mic if resolved else None

    def infer_market(self, symbol: str, exchange: str | None = None) -> str:
        normalized_exchange = self.normalize_exchange(exchange)
        exchange_resolution = mic_alias_registry.resolve_global(normalized_exchange)
        if exchange_resolution is not None:
            return exchange_resolution.market

        normalized_symbol = self.normalize_symbol(symbol)
        for suffix, market in _MARKET_BY_SUFFIX:
            if normalized_symbol.endswith(suffix):
                return market
        return "US"

    def _resolve_suffix(self, market: str, exchange: str | None) -> str | None:
        normalized_exchange = self.normalize_exchange(exchange)
        if normalized_exchange:
            scoped_suffix = _SUFFIX_BY_MARKET_EXCHANGE.get(
                (market, normalized_exchange)
            )
            if scoped_suffix:
                return scoped_suffix
            if normalized_exchange in _SUFFIX_BY_EXCHANGE:
                return _SUFFIX_BY_EXCHANGE[normalized_exchange]
            alias_resolution = mic_alias_registry.resolve(market, normalized_exchange)
            if alias_resolution is not None:
                mic_suffix = _SUFFIX_BY_EXCHANGE.get(alias_resolution.mic)
                if mic_suffix:
                    return mic_suffix
        return _SUFFIX_BY_MARKET.get(market)

    def _recognized_symbol_suffix(self, symbol: str) -> str | None:
        normalized_symbol = self.normalize_symbol(symbol)
        for suffix, _ in _MARKET_BY_SUFFIX:
            if normalized_symbol.endswith(suffix):
                return suffix
        return None

    def resolve_identity(
        self,
        *,
        symbol: str,
        market: str | None = None,
        exchange: str | None = None,
        currency: str | None = None,
        timezone: str | None = None,
        local_code: str | None = None,
    ) -> SecurityIdentity:
        """Resolve deterministic identity fields for a security."""
        normalized_symbol = self.normalize_symbol(symbol)
        normalized_exchange = self.normalize_exchange(exchange)
        normalized_market = self.normalize_market(market)
        if normalized_market is None:
            normalized_market = self.infer_market(normalized_symbol, normalized_exchange)
        exchange_resolution = (
            mic_alias_registry.resolve(normalized_market, normalized_exchange)
            if normalized_exchange
            else None
        )

        default_currency, default_timezone = _MARKET_DEFAULTS.get(
            normalized_market,
            _MARKET_DEFAULTS["US"],
        )

        resolved_currency = (currency or "").strip().upper() or default_currency
        resolved_timezone = (timezone or "").strip() or default_timezone

        resolved_local_code = (local_code or "").strip()
        if not resolved_local_code:
            if "." in normalized_symbol:
                resolved_local_code = normalized_symbol.split(".", 1)[0]
            else:
                resolved_local_code = normalized_symbol

        resolved_mic = exchange_resolution.mic if exchange_resolution else None
        canonical_symbol = normalized_symbol
        if normalized_market != "US" and resolved_local_code:
            # Preserve explicit non-US suffix when no exchange override is provided.
            if normalized_exchange is None and self._recognized_symbol_suffix(normalized_symbol):
                canonical_symbol = normalized_symbol
            else:
                suffix = self._resolve_suffix(normalized_market, normalized_exchange)
                if suffix:
                    expected_canonical = f"{resolved_local_code}{suffix}"
                    if canonical_symbol != expected_canonical:
                        canonical_symbol = expected_canonical
            if resolved_mic is None and normalized_exchange is None:
                resolved_mic = get_market_catalog().get(normalized_market).primary_mic

        return SecurityIdentity(
            normalized_symbol=normalized_symbol,
            canonical_symbol=canonical_symbol,
            market=normalized_market,
            exchange=normalized_exchange,
            mic=resolved_mic,
            currency=resolved_currency,
            timezone=resolved_timezone,
            local_code=resolved_local_code,
        )


security_master_resolver = SecurityMasterResolver()
