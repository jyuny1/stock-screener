"""Shared types for official exchange universe fetchers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OfficialMarketUniverseSnapshot:
    """Canonical upstream snapshot ready for stock_universe ingest."""

    market: str
    source_name: str
    snapshot_id: str
    snapshot_as_of: str
    source_metadata: dict[str, Any]
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class FetchedSource:
    url: str
    content: bytes
    fetched_at: str
    last_modified: str | None
    tls_verification_disabled: bool
