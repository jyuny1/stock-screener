"""Shared dispatch for official exchange universe snapshots."""

from __future__ import annotations

from typing import Any


OFFICIAL_UNIVERSE_INGEST_METHODS = {
    "HK": "ingest_hk_snapshot_rows",
    "IN": "ingest_in_snapshot_rows",
    "JP": "ingest_jp_snapshot_rows",
    "KR": "ingest_kr_snapshot_rows",
    "TW": "ingest_tw_snapshot_rows",
    "CN": "ingest_cn_snapshot_rows",
    "CA": "ingest_ca_snapshot_rows",
    "DE": "ingest_de_snapshot_rows",
    "SG": "ingest_sg_snapshot_rows",
    "AU": "ingest_au_snapshot_rows",
    "MY": "ingest_my_snapshot_rows",
}
OFFICIAL_SOURCE_MARKETS = frozenset(OFFICIAL_UNIVERSE_INGEST_METHODS)


def ingest_official_market_snapshot(
    db: Any,
    stock_universe_service: Any,
    snapshot: Any,
) -> dict[str, Any]:
    """Route a normalized official snapshot into the matching ingest method."""
    market = str(snapshot.market or "").strip().upper()
    method_name = OFFICIAL_UNIVERSE_INGEST_METHODS.get(market)
    if method_name is None:
        raise ValueError(
            f"Unsupported official universe snapshot market {snapshot.market!r}"
        )

    ingest_snapshot = getattr(stock_universe_service, method_name)
    return ingest_snapshot(
        db,
        rows=snapshot.rows,
        source_name=snapshot.source_name,
        snapshot_id=snapshot.snapshot_id,
        snapshot_as_of=snapshot.snapshot_as_of,
        source_metadata=snapshot.source_metadata,
    )
