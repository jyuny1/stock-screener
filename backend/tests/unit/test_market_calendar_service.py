from datetime import date, datetime

import pandas as pd
import pytest

from app.domain.markets.catalog import get_market_catalog
from app.services.market_calendar_service import MarketCalendarService


class _FakeCalendar:
    def __init__(self):
        self.sessions = [
            pd.Timestamp("2026-04-09"),
            pd.Timestamp("2026-04-10"),
        ]
        self.schedule = pd.DataFrame(
            {
                "close": [
                    pd.Timestamp("2026-04-09 08:00:00+00:00"),
                    pd.Timestamp("2026-04-10 08:00:00+00:00"),
                ],
            },
            index=self.sessions,
        )

    def is_session(self, session: pd.Timestamp) -> bool:
        return any(s.date() == session.date() for s in self.sessions)

    def previous_session(self, session: pd.Timestamp) -> pd.Timestamp:
        previous = [s for s in self.sessions if s.date() < session.date()]
        return previous[-1]

    def is_open_on_minute(self, ts: pd.Timestamp, ignore_breaks: bool = False) -> bool:
        # Keep this deterministic: only one minute is considered open.
        return ts == pd.Timestamp("2026-04-10 01:30:00+00:00")


class _FallbackCalendar:
    def __init__(self):
        self.sessions = [pd.Timestamp("2026-04-10")]
        self.schedule = pd.DataFrame(
            {
                "market_open": [pd.Timestamp("2026-04-10 03:45:00+00:00")],
                "market_close": [pd.Timestamp("2026-04-10 10:00:00+00:00")],
            },
            index=self.sessions,
        )

    def is_session(self, session: pd.Timestamp) -> bool:
        return any(s.date() == session.date() for s in self.sessions)


class _ProviderCalendar:
    pass


class _BoundsCalendar:
    def is_session(self, session: pd.Timestamp) -> bool:
        raise ValueError("Requested date is later than the last session available")


def test_market_calendar_service_uses_canonical_calendar_ids():
    service = MarketCalendarService(calendar_provider=lambda _: _FakeCalendar())

    assert service.calendar_id("US") == "XNYS"
    assert service.calendar_id("HK") == "XHKG"
    assert service.calendar_id("IN") == "XNSE"
    assert service.calendar_id("JP") == "XTKS"
    assert service.calendar_id("KR") == "XKRX"
    assert service.calendar_id("TW") == "XTAI"
    assert service.calendar_id("CN") == "XSHG"


def test_market_calendar_service_matches_catalog_primary_mic_facts():
    service = MarketCalendarService(calendar_provider=lambda _: _FakeCalendar())

    catalog = get_market_catalog()
    for market in catalog.supported_market_codes():
        assert (
            service.calendar_id(market)
            == catalog.get(market).primary_mic_facts.calendar_id
        )


def test_market_calendar_service_uses_primary_mic_facts_for_market_level_calls():
    service = MarketCalendarService(calendar_provider=lambda _: _FakeCalendar())
    india = get_market_catalog().get("IN")
    primary_facts = india.primary_mic_facts

    assert service.calendar_id("IN") == primary_facts.calendar_id
    assert service.provider_calendar_id("IN") == primary_facts.provider_calendar_id
    assert service.market_timezone("IN").key == primary_facts.timezone
    assert service.default_currency("IN") == primary_facts.default_currency


def test_market_calendar_service_supports_mic_specific_fact_lookup():
    service = MarketCalendarService(calendar_provider=lambda _: _FakeCalendar())
    bombay_facts = get_market_catalog().get("IN").mic_facts_for("XBOM")

    assert service.calendar_id("IN", mic="XBOM") == bombay_facts.calendar_id
    assert (
        service.provider_calendar_id("IN", mic="XBOM")
        == bombay_facts.provider_calendar_id
    )
    assert service.market_timezone("IN", mic="XBOM").key == bombay_facts.timezone
    assert service.default_currency("IN", mic="XBOM") == bombay_facts.default_currency


def test_last_completed_trading_day_before_close_returns_previous_session():
    service = MarketCalendarService(calendar_provider=lambda _: _FakeCalendar())
    now_hkt = datetime.fromisoformat("2026-04-10T15:30:00+08:00")

    expected = service.last_completed_trading_day("HK", now=now_hkt)

    assert expected == pd.Timestamp("2026-04-09").date()


def test_last_completed_trading_day_after_close_returns_current_session():
    service = MarketCalendarService(calendar_provider=lambda _: _FakeCalendar())
    now_hkt = datetime.fromisoformat("2026-04-10T16:30:00+08:00")

    expected = service.last_completed_trading_day("HK", now=now_hkt)

    assert expected == pd.Timestamp("2026-04-10").date()


def test_last_completed_trading_day_before_post_close_buffer_returns_previous_session():
    service = MarketCalendarService(calendar_provider=lambda _: _FakeCalendar())
    now_hkt = datetime.fromisoformat("2026-04-10T16:29:00+08:00")

    expected = service.last_completed_trading_day("HK", now=now_hkt)

    assert expected == pd.Timestamp("2026-04-09").date()


def test_is_market_open_uses_calendar_open_minute():
    service = MarketCalendarService(calendar_provider=lambda _: _FakeCalendar())
    open_minute_hkt = datetime.fromisoformat("2026-04-10T09:30:00+08:00")
    closed_minute_hkt = datetime.fromisoformat("2026-04-10T09:31:00+08:00")

    assert service.is_market_open("HK", now=open_minute_hkt) is True
    assert service.is_market_open("HK", now=closed_minute_hkt) is False


def test_is_market_open_schedule_fallback_treats_close_minute_as_closed():
    service = MarketCalendarService(calendar_provider=lambda _: _FallbackCalendar())
    pre_close_ist = datetime.fromisoformat("2026-04-10T15:29:00+05:30")
    close_minute_ist = datetime.fromisoformat("2026-04-10T15:30:00+05:30")

    assert service.is_market_open("IN", now=pre_close_ist) is True
    assert service.is_market_open("IN", now=close_minute_ist) is False


def test_india_pmc_lookup_uses_provider_specific_calendar_id():
    calls = []
    service = MarketCalendarService()
    service._pmc_provider = lambda calendar_id: calls.append(calendar_id) or _ProviderCalendar()
    service._xcals_provider = lambda calendar_id: calls.append(calendar_id) or _ProviderCalendar()

    service._get_calendar("IN")

    assert calls == ["NSE"]


def test_singapore_lookup_uses_exchange_calendars_calendar_id():
    calls = []
    service = MarketCalendarService()
    service._pmc_provider = (
        lambda calendar_id: calls.append(("pmc", calendar_id)) or _ProviderCalendar()
    )
    service._xcals_provider = (
        lambda calendar_id: calls.append(("xcals", calendar_id)) or _ProviderCalendar()
    )

    service._get_calendar("SG")

    assert calls == [("xcals", "XSES")]


def test_india_injected_calendar_provider_uses_provider_specific_calendar_id():
    calls = []
    service = MarketCalendarService(
        calendar_provider=lambda calendar_id: calls.append(calendar_id)
        or _ProviderCalendar()
    )

    service._get_calendar("IN")

    assert calls == ["NSE"]


def test_injected_calendar_provider_uses_mic_specific_provider_calendar_id():
    calls = []
    service = MarketCalendarService(
        calendar_provider=lambda calendar_id: calls.append(calendar_id)
        or _ProviderCalendar()
    )

    service._get_calendar("IN", mic="XBOM")

    assert calls == ["XBOM"]


def test_trading_day_lookup_uses_mic_specific_calendar_id():
    calls = []
    service = MarketCalendarService(
        calendar_provider=lambda calendar_id: calls.append(calendar_id)
        or _FakeCalendar()
    )

    assert service.is_trading_day("IN", date(2026, 4, 10), mic="XBOM") is True
    assert calls == ["XBOM"]


@pytest.mark.parametrize("market", ["CN", "SG"])
def test_calendar_bounds_fallback_uses_weekdays(market):
    service = MarketCalendarService(calendar_provider=lambda _: _BoundsCalendar())

    assert service.is_trading_day(market, date(2026, 4, 10)) is True
    assert service.is_trading_day(market, date(2026, 4, 11)) is False


@pytest.mark.parametrize("market", ["CN", "SG"])
def test_last_completed_trading_day_bounds_fallback(market):
    service = MarketCalendarService(calendar_provider=lambda _: _BoundsCalendar())

    before_close = datetime.fromisoformat("2026-04-10T15:00:00+08:00")
    after_close = datetime.fromisoformat("2026-04-10T16:00:00+08:00")

    assert service.last_completed_trading_day(market, now=before_close) == date(2026, 4, 9)
    assert service.last_completed_trading_day(market, now=after_close) == date(2026, 4, 10)
