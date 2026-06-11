from datetime import date

from app.services.schwab_option_metrics_service import SchwabOptionMetricsService


def test_compute_volume_pcr_14_28dte_from_chain_payload():
    service = SchwabOptionMetricsService(access_token="token")

    def fake_get_chains(symbol, *, from_date, to_date):
        assert symbol == "SPY"
        assert from_date == date(2026, 6, 23)
        assert to_date == date(2026, 7, 7)
        return {
            "putExpDateMap": {
                "2026-07-17:38": {
                    "600.0": [{"symbol": "SPY260703P00600000", "expirationDate": "2026-07-03T20:00:00.000+00:00", "daysToExpiration": 24, "strikePrice": 600, "bid": 1.2, "ask": 1.3, "totalVolume": 10, "openInterest": 100, "delta": -0.2}],
                    "595.0": [{"symbol": "SPY260703P00595000", "expirationDate": "2026-07-03T20:00:00.000+00:00", "daysToExpiration": 24, "strikePrice": 595, "bid": 0.9, "ask": 1.0, "totalVolume": 15, "openInterest": 150}],
                }
            },
            "callExpDateMap": {
                "2026-07-17:38": {
                    "600.0": [{"expirationDate": "2026-07-03T20:00:00.000+00:00", "totalVolume": 5, "openInterest": 50}],
                    "605.0": [{"expirationDate": "2026-07-03T20:00:00.000+00:00", "totalVolume": 20, "openInterest": 200}],
                }
            },
        }

    service._get_chains = fake_get_chains

    metric = service.compute_volume_pcr("spy", min_dte=14, max_dte=28, today=date(2026, 6, 9))

    assert metric.symbol == "SPY"
    assert metric.put_volume == 25
    assert metric.call_volume == 25
    assert metric.put_oi == 250
    assert metric.call_oi == 250
    assert metric.pcr == 1.0
    assert metric.expirations == 1
    assert metric.contract_count == 4
    assert len(metric.put_contracts) == 2
    assert metric.put_contracts[0].contract_symbol == "SPY260703P00600000"
    assert metric.put_contracts[0].expiration_date == "2026-07-03"
    assert metric.put_contracts[0].dte_at_snapshot == 24
    assert metric.put_contracts[0].schwab_dte == 24
    assert metric.put_contracts[0].strike == 600
    assert metric.put_contracts[0].put_volume == 10
    assert metric.put_contracts[0].put_oi == 100
    assert metric.put_contracts[0].bid == 1.2
    assert metric.to_details_patch()["option_put_contracts_14_28dte"][0]["dte_at_snapshot"] == 24
    assert metric.to_details_patch()["option_put_contracts_14_28dte"][0]["put_volume"] == 10


def test_compute_volume_pcr_returns_none_when_no_call_volume():
    service = SchwabOptionMetricsService(access_token="token")
    service._get_chains = lambda *args, **kwargs: {
        "putExpDateMap": {"2026-07-17:38": {"600.0": [{"totalVolume": 3}]}},
        "callExpDateMap": {"2026-07-17:38": {"600.0": [{"totalVolume": 0}]}},
    }

    metric = service.compute_volume_pcr("ABC", today=date(2026, 6, 9))

    assert metric.put_volume == 3
    assert metric.call_volume == 0
    assert metric.put_oi == 0
    assert metric.call_oi == 0
    assert metric.pcr is None
