"""ASX public CSV universe fetcher."""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
import io
import logging
from pathlib import Path
import re
from typing import Any, Callable

import requests

from ..config import settings
from .official_market_universe_types import (
    FetchedSource,
    OfficialMarketUniverseSnapshot,
)

logger = logging.getLogger(__name__)

_AU_SOURCE_NAME = "asx_official_public_csv"
_AU_FALLBACK_SOURCE_NAME = "au_manual_csv"
_AU_LIVE_TICKER_RE = re.compile(r"^[A-Z0-9]{2,6}$")


class ASXOfficialUniverseSource:
    """Fetch and normalize the Australian equity universe from ASX's CSV."""

    def __init__(self, *, http_get: Callable[..., FetchedSource]) -> None:
        self._http_get = http_get

    def fetch_snapshot(self) -> OfficialMarketUniverseSnapshot:
        """Fetch the AU equity universe from ASX's public listed-company CSV.

        ASX publishes a stable CSV link with a preamble line before the
        ``Company name,ASX code,GICS industry group`` header. The live CSV is
        attempted first by default; unreachable, unparsable, empty, or tiny
        live responses fall back to the bundled ASX CSV.
        """
        fetch_mode = "live_http"
        fetch_errors: dict[str, str] = {}
        fetched_at: str | None = None
        last_modified: str | None = None
        as_of: date | None = None
        tls_verification_disabled = False
        rows: list[dict[str, Any]] = []

        if settings.au_universe_source_url:
            try:
                live_meta = self._fetch_live()
            except (requests.exceptions.RequestException, ValueError) as exc:
                live_error = str(exc)
                print(
                    f"[au] Live universe fetch failed: {live_error!s}. "
                    f"Falling back to bundled CSV at {settings.au_universe_fallback_csv_path}.",
                    flush=True,
                )
                logger.warning(
                    "Live AU universe fetch failed (%s); falling back to bundled CSV at %s",
                    live_error,
                    settings.au_universe_fallback_csv_path,
                )
                fetch_errors["live_http"] = live_error
                fallback_meta = self.load_csv_fallback_meta()
                rows = fallback_meta["rows"]
                as_of = fallback_meta.get("as_of")
                fetch_mode = "csv_fallback"
                fetched_at = datetime.now(UTC).isoformat()
            else:
                rows = live_meta["rows"]
                fetched_at = live_meta.get("fetched_at")
                last_modified = live_meta.get("http_last_modified")
                as_of = live_meta.get("as_of")
                tls_verification_disabled = bool(
                    live_meta.get("tls_verification_disabled")
                )
                print(
                    f"[au] Live universe fetch succeeded: {len(rows)} rows "
                    f"from {settings.au_universe_source_url}",
                    flush=True,
                )
        else:
            logger.info(
                "AU universe source URL is blank; using bundled fallback CSV at %s",
                settings.au_universe_fallback_csv_path,
            )
            fallback_meta = self.load_csv_fallback_meta()
            rows = fallback_meta["rows"]
            as_of = fallback_meta.get("as_of")
            fetch_mode = "csv_fallback"
            fetched_at = datetime.now(UTC).isoformat()

        if not rows:
            raise ValueError(
                "AU official universe fetch returned no rows (live + fallback both empty)"
            )
        min_size = int(settings.au_live_min_universe_size or 0)
        if fetch_mode == "csv_fallback" and min_size and len(rows) < min_size:
            raise ValueError(
                "AU official universe fetch returned "
                f"{len(rows)} rows from fallback CSV, below {min_size} threshold"
            )

        rows = sorted(rows, key=lambda row: row["symbol"])
        snapshot_as_of = (
            as_of or self._date_from_http_header(last_modified) or self._utc_today()
        ).isoformat()
        source_name = (
            _AU_FALLBACK_SOURCE_NAME if fetch_mode == "csv_fallback" else _AU_SOURCE_NAME
        )
        source_metadata: dict[str, Any] = {
            "source_urls": [settings.au_universe_source_url]
            if settings.au_universe_source_url
            else [],
            "fetched_at": fetched_at or datetime.now(UTC).isoformat(),
            "http_last_modified": last_modified,
            "tls_verification_disabled": tls_verification_disabled,
            "fetch_mode": fetch_mode,
            "fetch_errors": fetch_errors,
            "filters": {
                "source": "ASX listed companies public CSV",
                "symbol_regex": _AU_LIVE_TICKER_RE.pattern,
            },
            "row_counts": {
                "xasx": len(rows),
                "total": len(rows),
            },
        }
        if fetch_mode == "csv_fallback":
            source_metadata["fallback_csv_path"] = settings.au_universe_fallback_csv_path

        snapshot_id_prefix = (
            "asx-listed-companies" if fetch_mode == "live_http" else "au-csv-fallback"
        )
        return OfficialMarketUniverseSnapshot(
            market="AU",
            source_name=source_name,
            snapshot_id=f"{snapshot_id_prefix}-{snapshot_as_of}",
            snapshot_as_of=snapshot_as_of,
            source_metadata=source_metadata,
            rows=tuple(rows),
        )

    def _fetch_live(self) -> dict[str, Any]:
        """Download and parse the ASX listed companies CSV."""
        fetched = self._http_get(settings.au_universe_source_url)
        rows = self.parse_asx_csv(fetched.content)
        as_of = self.extract_asx_as_of(fetched.content)
        if not rows:
            raise ValueError("ASX CSV yielded no equity rows after filtering")

        min_universe_size = int(getattr(settings, "au_live_min_universe_size", 0) or 0)
        if min_universe_size > 0 and len(rows) < min_universe_size:
            raise ValueError(
                f"AU live universe has only {len(rows)} rows "
                f"(below {min_universe_size} threshold); refusing to publish - "
                "the ASX CSV response shape may have changed"
            )

        return {
            "rows": sorted(rows, key=lambda row: row["symbol"]),
            "fetched_at": fetched.fetched_at,
            "http_last_modified": fetched.last_modified,
            "as_of": as_of,
            "tls_verification_disabled": fetched.tls_verification_disabled,
        }

    @classmethod
    def parse_asx_csv(cls, content: bytes) -> list[dict[str, Any]]:
        """Parse ASX's public listed-company CSV with dynamic header detection."""
        text = content.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        header_index: int | None = None
        expected_header = "company name,asx code,gics industry group"
        for index, line in enumerate(lines):
            if line.strip().lower().startswith(expected_header):
                header_index = index
                break
        if header_index is None:
            raise ValueError("ASX CSV header not found")

        csv_text = "\n".join(lines[header_index:])
        reader = csv.DictReader(io.StringIO(csv_text))
        rows: list[dict[str, Any]] = []
        seen_codes: set[str] = set()
        for record in reader:
            name = str(record.get("Company name") or "").strip()
            local_code = str(record.get("ASX code") or "").strip().upper()
            gics = str(record.get("GICS industry group") or "").strip()
            if not name or not local_code:
                continue
            if not _AU_LIVE_TICKER_RE.fullmatch(local_code):
                continue
            if local_code in seen_codes:
                continue
            seen_codes.add(local_code)
            rows.append(
                {
                    "symbol": f"{local_code}.AX",
                    "local_code": local_code,
                    "name": name,
                    "exchange": "XASX",
                    "sector": "",
                    "industry": gics,
                    "gics_industry_group": gics,
                    "market_cap": None,
                }
            )
        return rows

    @classmethod
    def extract_asx_as_of(cls, content: bytes) -> date | None:
        text = content.decode("utf-8-sig", errors="replace")
        first_line = text.splitlines()[0].strip() if text.splitlines() else ""
        match = re.fullmatch(
            r"ASX listed companies as at (.+?) [A-Z]{2,5} (\d{4})",
            first_line,
        )
        if not match:
            return None
        try:
            return datetime.strptime(
                f"{match.group(1)} {match.group(2)}",
                "%a %b %d %H:%M:%S %Y",
            ).date()
        except ValueError:
            return None

    @classmethod
    def load_csv_fallback(cls) -> list[dict[str, Any]]:
        return cls.load_csv_fallback_meta()["rows"]

    @classmethod
    def load_csv_fallback_meta(cls) -> dict[str, Any]:
        csv_path = Path(settings.au_universe_fallback_csv_path)
        if not csv_path.exists():
            return {"rows": [], "as_of": None}
        content = csv_path.read_bytes()
        return {
            "rows": cls.parse_asx_csv(content),
            "as_of": cls.extract_asx_as_of(content),
        }

    @staticmethod
    def _utc_today() -> date:
        return datetime.now(UTC).date()

    @staticmethod
    def _date_from_http_header(header_value: str | None) -> date | None:
        if not header_value:
            return None
        try:
            return parsedate_to_datetime(header_value).astimezone(UTC).date()
        except Exception:
            return None
