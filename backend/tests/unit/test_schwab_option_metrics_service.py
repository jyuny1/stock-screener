from datetime import date

from app.services.schwab_option_metrics_service import SchwabOptionMetricsService


def test_compute_volume_pcr_30_45dte_from_chain_payload():
    service = SchwabOptionMetricsService(access_token="token")

    def fake_get_chains(symbol, *, from_date, to_date):
        assert symbol == "SPY"
        assert from_date == date(2026, 7, 9)
        assert to_date == date(2026, 7, 24)
        return {
            "putExpDateMap": {
                "2026-07-17:38": {
                    "600.0": [{"expirationDate": "2026-07-17T20:00:00.000+00:00", "totalVolume": 10}],
                    "595.0": [{"expirationDate": "2026-07-17T20:00:00.000+00:00", "totalVolume": 15}],
                }
            },
            "callExpDateMap": {
                "2026-07-17:38": {
                    "600.0": [{"expirationDate": "2026-07-17T20:00:00.000+00:00", "totalVolume": 5}],
                    "605.0": [{"expirationDate": "2026-07-17T20:00:00.000+00:00", "totalVolume": 20}],
                }
            },
        }

    service._get_chains = fake_get_chains

    metric = service.compute_volume_pcr("spy", min_dte=30, max_dte=45, today=date(2026, 6, 9))

    assert metric.symbol == "SPY"
    assert metric.put_volume == 25
    assert metric.call_volume == 25
    assert metric.pcr == 1.0
    assert metric.expirations == 1
    assert metric.contract_count == 4


def test_compute_volume_pcr_returns_none_when_no_call_volume():
    service = SchwabOptionMetricsService(access_token="token")
    service._get_chains = lambda *args, **kwargs: {
        "putExpDateMap": {"2026-07-17:38": {"600.0": [{"totalVolume": 3}]}},
        "callExpDateMap": {"2026-07-17:38": {"600.0": [{"totalVolume": 0}]}},
    }

    metric = service.compute_volume_pcr("ABC", today=date(2026, 6, 9))

    assert metric.put_volume == 3
    assert metric.call_volume == 0
    assert metric.pcr is None
