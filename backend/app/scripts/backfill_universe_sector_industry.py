"""One-time backfill of ``StockUniverse.sector``/``industry`` from yfinance.

Foreign-market universe rows ingest without classification (CN ``sector="Other"``,
HK empty), which starves the IBD crosswalk + embedding tiers. yfinance returns
sector/industry on its fundamentals call; this fills rows that are missing it,
without touching rows that already carry a real value (e.g. US finviz data).
Idempotent and safe to re-run; new full refreshes carry the data automatically
once the fundamentals persist path is in place, so this just avoids waiting.

Usage:
    python -m app.scripts.backfill_universe_sector_industry [--market HK] [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.stock_universe import StockUniverse
from app.scripts._runtime import prepare_runtime
from app.services.fundamentals_cache_service import (
    _backfill_universe_classification,
    _is_meaningful_classification,
)


def _needs_backfill(row: StockUniverse) -> bool:
    """True when the row lacks a real sector OR industry."""
    return not (
        _is_meaningful_classification(row.sector)
        and _is_meaningful_classification(row.industry)
    )


def backfill_universe(
    db: Session,
    *,
    fetch_fundamentals: Callable[[str], Optional[dict]],
    market: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    progress: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Fill missing sector/industry for active universe rows via ``fetch_fundamentals``.

    Pure orchestration over an injected fetcher (so it's testable without yfinance);
    reuses the shared, no-clobber ``_backfill_universe_classification`` for the actual
    write so the precedence rules stay in one place.
    """
    query = db.query(StockUniverse).filter(StockUniverse.is_active.is_(True))
    if market:
        query = query.filter(StockUniverse.market == market.strip().upper())
    candidates = [row for row in query.all() if _needs_backfill(row)]
    if limit is not None:
        candidates = candidates[:limit]

    filled = errors = 0
    for i, row in enumerate(candidates, 1):
        try:
            data = fetch_fundamentals(row.symbol) or {}
        except Exception:  # noqa: BLE001 — one bad symbol must not abort the batch
            errors += 1
            continue
        if _backfill_universe_classification(db, row.symbol, data):
            filled += 1
        if progress and i % 200 == 0:
            progress({"scanned": i, "total": len(candidates), "filled": filled, "errors": errors})

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {"candidates": len(candidates), "filled": filled, "errors": errors, "dry_run": dry_run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default=None, help="Restrict to one market (e.g. HK).")
    parser.add_argument("--limit", type=int, default=None, help="Cap rows processed.")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without persisting.")
    args = parser.parse_args(argv)

    prepare_runtime()
    from app.services.yfinance_service import YFinanceService

    yf_service = YFinanceService()
    with SessionLocal() as db:
        stats = backfill_universe(
            db,
            fetch_fundamentals=yf_service.get_fundamentals,
            market=args.market,
            limit=args.limit,
            dry_run=args.dry_run,
            progress=lambda rec: print(f"  progress: {rec}", flush=True),
        )

    print("Universe sector/industry backfill complete:")
    for key, value in stats.items():
        print(f"  - {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
