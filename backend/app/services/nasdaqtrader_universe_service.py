"""NasdaqTrader US symbol-directory fetcher for optionable-universe scans."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.request import Request, urlopen


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

_DEFAULT_LIQUID_ETF_ALLOWLIST = frozenset(
    {
        "DIA",
        "EEM",
        "EFA",
        "GLD",
        "HYG",
        "IWM",
        "QQQ",
        "SLV",
        "SPY",
        "TLT",
        "USO",
        "VTI",
        "XLE",
        "XLF",
        "XLK",
        "XLP",
        "XLU",
        "XLV",
    }
)

_SECURITY_NAME_EXCLUDE_RE = re.compile(
    r"\b(warrants?|rights?|units?|preferred|depositary|notes?|bonds?|debentures?)\b",
    re.IGNORECASE,
)
_ALLOWED_OTHER_LISTING_EXCHANGES = {
    "N": ("NYSE", "XNYS"),
    "A": ("NYSE American", "XASE"),
    "P": ("NYSE Arca", "ARCX"),
}


@dataclass(frozen=True)
class NasdaqTraderSymbol:
    symbol: str
    name: str
    exchange: str
    mic: str
    is_etf: bool
    is_test_issue: bool
    source_file: str


@dataclass(frozen=True)
class NasdaqTraderUniverseSnapshot:
    as_of: str
    raw_symbols: int
    filtered_symbols: int
    rows: tuple[NasdaqTraderSymbol, ...]


class NasdaqTraderUniverseService:
    """Fetch and clean NasdaqTrader symbol directories for US equities/ETFs."""

    def __init__(
        self,
        *,
        nasdaq_url: str = NASDAQ_LISTED_URL,
        other_url: str = OTHER_LISTED_URL,
        etf_allowlist: Iterable[str] = _DEFAULT_LIQUID_ETF_ALLOWLIST,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.nasdaq_url = nasdaq_url
        self.other_url = other_url
        self.etf_allowlist = {symbol.upper() for symbol in etf_allowlist}
        self.timeout_seconds = timeout_seconds

    def fetch_clean_snapshot(self) -> NasdaqTraderUniverseSnapshot:
        """Download both directories and return filtered rows sorted by symbol."""
        nasdaq_text = self._download_text(self.nasdaq_url)
        other_text = self._download_text(self.other_url)
        raw_rows = [
            *self.parse_nasdaqlisted(nasdaq_text),
            *self.parse_otherlisted(other_text),
        ]
        filtered: dict[str, NasdaqTraderSymbol] = {}
        for row in raw_rows:
            if self._keep_symbol(row):
                filtered.setdefault(row.symbol, row)
        rows = tuple(sorted(filtered.values(), key=lambda item: item.symbol))
        return NasdaqTraderUniverseSnapshot(
            as_of=datetime.now(timezone.utc).date().isoformat(),
            raw_symbols=len({row.symbol for row in raw_rows}),
            filtered_symbols=len(rows),
            rows=rows,
        )

    @staticmethod
    def parse_nasdaqlisted(text: str) -> tuple[NasdaqTraderSymbol, ...]:
        rows: list[NasdaqTraderSymbol] = []
        for record in _pipe_records(text):
            symbol = _normalize_symbol(record.get("Symbol"))
            if not symbol:
                continue
            rows.append(
                NasdaqTraderSymbol(
                    symbol=symbol,
                    name=(record.get("Security Name") or "").strip(),
                    exchange="NASDAQ",
                    mic="XNAS",
                    is_etf=_is_yes(record.get("ETF")),
                    is_test_issue=_is_yes(record.get("Test Issue")),
                    source_file="nasdaqlisted.txt",
                )
            )
        return tuple(rows)

    @staticmethod
    def parse_otherlisted(text: str) -> tuple[NasdaqTraderSymbol, ...]:
        rows: list[NasdaqTraderSymbol] = []
        for record in _pipe_records(text):
            symbol = _normalize_symbol(record.get("ACT Symbol"))
            listing_exchange = (record.get("Listing Exchange") or "").strip().upper()
            if not symbol or listing_exchange not in _ALLOWED_OTHER_LISTING_EXCHANGES:
                continue
            exchange, mic = _ALLOWED_OTHER_LISTING_EXCHANGES[listing_exchange]
            rows.append(
                NasdaqTraderSymbol(
                    symbol=symbol,
                    name=(record.get("Security Name") or "").strip(),
                    exchange=exchange,
                    mic=mic,
                    is_etf=_is_yes(record.get("ETF")),
                    is_test_issue=_is_yes(record.get("Test Issue")),
                    source_file="otherlisted.txt",
                )
            )
        return tuple(rows)

    def _download_text(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": "stock-screener-static-pipeline/1.0"})
        with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - fixed public URLs
            return response.read().decode("utf-8", errors="replace")

    def _keep_symbol(self, row: NasdaqTraderSymbol) -> bool:
        if not row.symbol or row.symbol.endswith(".") or row.is_test_issue:
            return False
        if "." in row.symbol:
            # NasdaqTrader class shares are often dot-delimited. Schwab uses varying
            # class-share spellings, so keep the first MVP deterministic and avoid
            # symbols that are likely to fail /chains.
            return False
        if row.is_etf:
            return row.symbol in self.etf_allowlist
        name = row.name or ""
        if _SECURITY_NAME_EXCLUDE_RE.search(name):
            return False
        if " trust" in name.lower() and row.symbol not in self.etf_allowlist:
            return False
        return True


def _pipe_records(text: str) -> Iterable[dict[str, str]]:
    lines = [line for line in text.splitlines() if line and not line.startswith("File Creation Time:")]
    if not lines:
        return ()
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter="|")
    return (dict(row) for row in reader if row and not str(next(iter(row.values()), "")).startswith("File Creation Time:"))


def _normalize_symbol(value: str | None) -> str:
    symbol = (value or "").strip().upper()
    if not symbol or symbol in {"SYMBOL", "ACT SYMBOL"}:
        return ""
    return symbol


def _is_yes(value: str | None) -> bool:
    return (value or "").strip().upper() == "Y"
