"""get_fundamentals must surface sector/industry (already in the fetched info dict).

These feed the universe sector/industry backfill; yfinance returns them on the same
``ticker.info`` call the fundamentals fetch already makes, so exposing them is free.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import yfinance_service as ymod


class _FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    @property
    def info(self):
        return {
            "sector": "Communication Services",
            "industry": "Internet Content & Information",
            "marketCap": 1_000_000,
            "longName": "Tencent Holdings",
        }


def test_get_fundamentals_surfaces_sector_and_industry(monkeypatch):
    svc = ymod.YFinanceService(rate_limiter=SimpleNamespace(), eps_rating_service=SimpleNamespace())
    monkeypatch.setattr(ymod.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(svc, "_wait_for_yfinance_rate_limit", lambda: None)
    monkeypatch.setattr(svc, "_extract_eps_rating_data", lambda ticker: {})

    result = svc.get_fundamentals("0700.HK")

    assert result["sector"] == "Communication Services"
    assert result["industry"] == "Internet Content & Information"
