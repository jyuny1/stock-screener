"""Unit tests for backfilling StockUniverse.sector/industry from fundamentals.

Foreign-market universe ingest lands without sector/industry (CN="Other", HK empty),
which starves the IBD crosswalk + embedding tiers. yfinance already returns proper
sector/industry on the fundamentals fetch; these helpers propagate it into the
universe row without clobbering an existing meaningful value (e.g. US finviz data).
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.stock_universe import StockUniverse
from app.services.fundamentals_cache_service import (
    _backfill_universe_classification,
    _is_meaningful_classification,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _add(session, symbol, sector, industry, market="HK"):
    session.add(StockUniverse(
        symbol=symbol, market=market, sector=sector, industry=industry, is_active=True
    ))
    session.commit()


def test_is_meaningful_classification_truth_table():
    for good in ("Technology", "Communication Services", " Energy "):
        assert _is_meaningful_classification(good) is True
    for bad in (None, "", "   ", "Other", "other", "Unknown", "UNKNOWN", "N/A", "n/a", "None"):
        assert _is_meaningful_classification(bad) is False


def test_backfill_fills_empty_and_placeholder_values():
    session = _session()
    _add(session, "0700.HK", "", "")              # HK: both empty
    _add(session, "600519.SS", "Other", None, market="CN")  # CN: Other / null

    assert _backfill_universe_classification(session, "0700.HK", {
        "sector": "Communication Services",
        "industry": "Internet Content & Information",
    }) is True
    assert _backfill_universe_classification(session, "600519.SS", {
        "sector": "Consumer Defensive",
        "industry": "Beverages - Wineries & Distilleries",
    }) is True
    session.commit()

    rows = {r.symbol: r for r in session.query(StockUniverse).all()}
    assert rows["0700.HK"].sector == "Communication Services"
    assert rows["0700.HK"].industry == "Internet Content & Information"
    assert rows["600519.SS"].sector == "Consumer Defensive"
    assert rows["600519.SS"].industry == "Beverages - Wineries & Distilleries"


def test_backfill_does_not_clobber_meaningful_existing():
    session = _session()
    _add(session, "AAPL", "Technology", "Consumer Electronics", market="US")  # US finviz

    changed = _backfill_universe_classification(session, "AAPL", {
        "sector": "Technology Different", "industry": "Something Else",
    })
    assert changed is False
    row = session.query(StockUniverse).filter_by(symbol="AAPL").first()
    assert row.sector == "Technology"
    assert row.industry == "Consumer Electronics"


def test_backfill_noop_when_fetched_not_meaningful():
    session = _session()
    _add(session, "X.HK", "", "")
    assert _backfill_universe_classification(session, "X.HK", {
        "sector": "Other", "industry": None,
    }) is False
    assert (session.query(StockUniverse).filter_by(symbol="X.HK").first().sector or "") == ""


def test_backfill_noop_when_no_universe_row():
    session = _session()
    assert _backfill_universe_classification(session, "GHOST.HK", {"sector": "Energy"}) is False


def test_backfill_partial_industry_only():
    # sector already meaningful, industry empty → only industry is filled.
    session = _session()
    _add(session, "P.HK", "Energy", "")
    changed = _backfill_universe_classification(session, "P.HK", {
        "sector": "Energy", "industry": "Oil & Gas E&P",
    })
    assert changed is True
    row = session.query(StockUniverse).filter_by(symbol="P.HK").first()
    assert row.sector == "Energy"
    assert row.industry == "Oil & Gas E&P"
