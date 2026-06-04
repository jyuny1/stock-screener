from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.nasdaqtrader_universe_service import NasdaqTraderUniverseService
from app.services.schwab_token_service import SchwabTokenService
import app.scripts.build_optionable_symbols as optionable_script
import app.scripts.build_weekly_reference_bundle as weekly_script


ROOT = Path(__file__).resolve().parents[3]


def test_nasdaqtrader_service_filters_to_clean_common_symbols_and_liquid_etfs():
    nasdaq_text = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. Common Stock|Q|N|N|100|N|N
TQQQ|ProShares UltraPro QQQ|G|N|N|100|Y|N
ABCW|ABC Warrants|Q|N|N|100|N|N
FOO.U|Foo Units|Q|N|N|100|N|N
File Creation Time: 2026-06-04
"""
    other_text = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
XNYS1|Example NYSE Common Stock|N|XNYS1|N|100|N|XNYS1
XASE1|Example NYSE American Common Stock|A|XASE1|N|100|N|XASE1
SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY
BRK.B|Berkshire Hathaway Class B|N|BRK.B|N|100|N|BRK.B
XYZ|XYZ Debenture|A|XYZ|N|100|N|XYZ
CBOE|Cboe Listed Co|Z|CBOE|N|100|N|CBOE
File Creation Time: 2026-06-04
"""

    rows = [
        *NasdaqTraderUniverseService.parse_nasdaqlisted(nasdaq_text),
        *NasdaqTraderUniverseService.parse_otherlisted(other_text),
    ]
    service = NasdaqTraderUniverseService(etf_allowlist={"SPY"})
    kept = [row.symbol for row in rows if service._keep_symbol(row)]

    assert kept == ["AAPL", "XNYS1", "XASE1", "SPY"]


class _FakeResponse:
    status_code = 200

    def json(self):
        return {"access_token": "access", "refresh_token": "rotated", "expires_in": 1800}


def test_optionable_retryable_failure_classifier():
    assert optionable_script._is_retryable_failure("http_401") is True
    assert optionable_script._is_retryable_failure("error:Timeout") is True
    assert optionable_script._is_retryable_failure("empty_chain") is False
    assert optionable_script._is_retryable_failure("not_found") is False


def test_optionable_checkpoint_loader_accepts_published_latest_artifact(tmp_path):
    checkpoint = tmp_path / "optionable-symbols-latest-us.json"
    checkpoint.write_text(
        json.dumps(
            {
                "schema_version": "optionable-symbols-v1",
                "symbols": ["AAPL", "MSFT"],
                "failures": {"XYZ": "http_401", "ABC": "empty_chain"},
            }
        ),
        encoding="utf-8",
    )

    loaded = optionable_script._load_checkpoint(checkpoint)

    assert loaded["optionable"] == ["AAPL", "MSFT"]
    assert loaded["failures"] == {"XYZ": "http_401", "ABC": "empty_chain"}


def test_optionable_scanner_refreshes_token_and_retries_401(monkeypatch, tmp_path):
    calls = []

    class FakeChainResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    responses = [
        FakeChainResponse(401),
        FakeChainResponse(200, {"callExpDateMap": {"2026-06-19:15": {}}}),
    ]

    def fake_get(url, *, params, headers, timeout):
        calls.append({"symbol": params["symbol"], "auth": headers["Authorization"]})
        return responses.pop(0)

    class FakeTokenService:
        def refresh_from_env(self):
            return SimpleNamespace(access_token="new-access", new_refresh_token="new-refresh")

    token_path = tmp_path / "new-refresh.txt"
    monkeypatch.setattr(optionable_script.requests, "get", fake_get)
    scanner = optionable_script.SchwabOptionableScanner(
        access_token="expired-access",
        calls_per_minute=120,
        token_service=FakeTokenService(),
        new_refresh_token_path=token_path,
    )

    is_optionable, reason = scanner.is_optionable("MSFT")

    assert is_optionable is True
    assert reason is None
    assert scanner.refresh_count == 1
    assert token_path.read_text(encoding="utf-8") == "new-refresh"
    assert calls == [
        {"symbol": "MSFT", "auth": "Bearer expired-access"},
        {"symbol": "MSFT", "auth": "Bearer new-access"},
    ]
    assert [attempt["http_status"] for attempt in scanner.last_attempts] == [401, 200]
    assert scanner.token_events == [
        {
            "refreshed_at": scanner.token_events[0]["refreshed_at"],
            "refresh_count": 1,
            "symbol": "MSFT",
            "trigger_status": 401,
        }
    ]
    assert "expired-access" not in json.dumps(scanner.last_attempts)
    assert "new-access" not in json.dumps(scanner.last_attempts)
    assert "new-refresh" not in json.dumps(scanner.token_events)


def test_optionable_retry_diagnostics_files(tmp_path):
    attempts_path = tmp_path / "scan-attempts.jsonl"
    token_path = tmp_path / "token-events.jsonl"
    retryable_path = tmp_path / "retryable-failures.json"

    optionable_script._append_jsonl(
        attempts_path,
        {
            "round": "retry 1/3",
            "symbol": "BAD",
            "reason": "http_500",
            "attempts": [{"http_status": 500}],
        },
    )
    optionable_script._append_jsonl(
        token_path,
        {"symbol": "TOK", "trigger_status": 401, "refresh_count": 1},
    )
    retryable = optionable_script._write_retryable_failures_snapshot(
        retryable_path,
        round_label="retry 1/3",
        failures={"BAD": "http_500", "TIME": "error:ReadTimeout", "EMPTY": "empty_chain"},
    )

    assert [json.loads(line) for line in attempts_path.read_text(encoding="utf-8").splitlines()][0]["symbol"] == "BAD"
    assert [json.loads(line) for line in token_path.read_text(encoding="utf-8").splitlines()][0]["trigger_status"] == 401
    assert retryable == {"BAD": "http_500", "TIME": "error:ReadTimeout"}
    snapshot = json.loads(retryable_path.read_text(encoding="utf-8"))
    assert snapshot["round"] == "retry 1/3"
    assert snapshot["count"] == 2
    assert snapshot["failures"] == retryable


def test_schwab_token_service_refreshes_without_logging_secret(monkeypatch):
    calls = []

    def fake_post(url, *, data, headers, timeout):
        calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return _FakeResponse()

    monkeypatch.setattr("app.services.schwab_token_service.requests.post", fake_post)
    pair = SchwabTokenService(token_url="https://example.test/token").refresh(
        client_id="client",
        client_secret="secret",
        refresh_token="old-refresh",
    )

    assert pair.access_token == "access"
    assert pair.new_refresh_token == "rotated"
    assert calls[0]["data"] == {"grant_type": "refresh_token", "refresh_token": "old-refresh"}
    assert calls[0]["headers"]["Authorization"].startswith("Basic ")


def test_weekly_us_optionable_mode_publishes_from_artifact(monkeypatch, tmp_path):
    payload = {
        "schema_version": "optionable-symbols-v1",
        "as_of": "2026-06-04",
        "symbols": ["AAPL", "SPY"],
        "symbol_metadata": {
            "AAPL": {"name": "Apple Inc.", "mic": "XNAS"},
            "SPY": {"name": "SPDR S&P 500 ETF Trust", "mic": "ARCX"},
        },
    }
    artifact = tmp_path / "optionable-symbols-latest-us.json"
    artifact.write_text(json.dumps(payload), encoding="utf-8")

    active_rows = [
        SimpleNamespace(symbol="AAPL", market="US", exchange="XNAS", name="Apple", sector=None, industry=None, market_cap=None),
        SimpleNamespace(symbol="SPY", market="US", exchange="ARCX", name="SPY", sector=None, industry=None, market_cap=None),
    ]

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return active_rows

    class FakeDB:
        def __init__(self):
            self.added = []
            self.commits = 0

        def query(self, model):
            return FakeQuery()

        def add(self, row):
            self.added.append(row)

        def commit(self):
            self.commits += 1

    fake_db = FakeDB()
    cached = {
        "AAPL": {"market_cap": 100.0, "sector": "Technology"},
        "SPY": {"market_cap": 200.0, "sector": "ETF"},
    }
    monkeypatch.setattr(weekly_script, "get_fundamentals_cache", lambda: SimpleNamespace(get_many=lambda symbols: cached))
    monkeypatch.setattr(
        weekly_script,
        "get_hybrid_fundamentals_service",
        lambda: SimpleNamespace(
            fetch_fundamentals_batch=lambda symbols, **kwargs: {symbol: cached[symbol] for symbol in symbols},
            store_all_caches=lambda data, *args, **kwargs: {
                "fundamentals_stored": len(data),
                "persisted_symbols": len(data),
                "failed_persistence_symbols": 0,
                "failed": 0,
                "provider_error_counts": {},
            },
        ),
    )
    publish_calls = []
    provider = SimpleNamespace(
        build_market_snapshot_row=lambda **kwargs: {"symbol": kwargs["symbol"], "exchange": kwargs["exchange"]},
        publish_market_snapshot_run=lambda db, **kwargs: publish_calls.append(kwargs)
        or {"published": True, "coverage": kwargs["coverage_stats"], "coverage_thresholds": {"market": "US"}},
        get_published_run=lambda db, snapshot_key: SimpleNamespace(
            published_at=weekly_script.datetime(2026, 6, 4),
            created_at=weekly_script.datetime(2026, 6, 4),
            source_revision="fundamentals_v1_us:optionable:20260604",
        ),
        export_weekly_reference_bundle=lambda db, **kwargs: {"bundle_path": str(kwargs["output_path"])},
    )

    summary = weekly_script._build_us_bundle(
        fake_db,
        provider_snapshot_service=provider,
        stock_universe_service=SimpleNamespace(populate_universe=lambda db: pytest.fail("Finviz should not run")),
        market="US",
        output_dir=tmp_path,
        bundle_name=None,
        latest_manifest_name="weekly-reference-latest-us.json",
        universe_mode="optionable",
        optionable_symbols_path=str(artifact),
    )

    assert summary["universe_refresh"]["mode"] == "US_OPTIONABLE"
    assert publish_calls[0]["coverage_stats"]["universe_mode"] == "US_OPTIONABLE"
    assert [row["symbol"] for row in publish_calls[0]["rows"]] == ["AAPL", "SPY"]


def test_workflows_default_static_us_to_optionable():
    weekly = (ROOT / ".github" / "workflows" / "weekly-reference-data.yml").read_text()
    static = (ROOT / ".github" / "workflows" / "static-site.yml").read_text()
    optionable = (ROOT / ".github" / "workflows" / "optionable-symbols.yml").read_text()

    assert "US_UNIVERSE_MODE: ${{ matrix.market == 'US' && 'optionable' || 'full' }}" in weekly
    assert "Download US optionable symbols" in weekly
    assert "--us-universe-mode \"${US_UNIVERSE_MODE}\"" in weekly
    assert "US_UNIVERSE_MODE: ${{ matrix.market == 'US' && 'optionable' || 'full' }}" in static
    assert "cron: '0 * * * *'" in optionable
    assert "target is every other Sunday 20:00 America/New_York" in optionable
    assert "TZ=Asia/Taipei" in optionable
    assert "timeout-minutes: 75" in optionable
    assert "group: schwab-token-refresh" in optionable
    assert "Install optionable-symbols dependencies" in optionable
    assert "pip install requests" in optionable
    assert "pip install -r backend/requirements.txt" not in optionable
    assert "Validate Schwab secrets" in optionable
    assert "SCHWAB_SECRET_WRITE_TOKEN" in optionable
    assert "Refresh Schwab token and persist rotation" in optionable
    assert "retry_errors_from_latest" in optionable
    assert "max_retry_rounds" in optionable
    assert "--max-retry-rounds" in optionable
    assert "Upload failed optionable diagnostics" in optionable
    assert "/tmp/optionable-symbols/*.jsonl" in optionable
    assert "Seed checkpoint from latest artifact" in optionable
    assert "cp /tmp/optionable-symbols/optionable-symbols-latest-us.json /tmp/optionable-symbols/checkpoint.json" in optionable
    assert "SCHWAB_ACCESS_TOKEN=$(cat /tmp/schwab-access-token.txt)" in optionable
    assert "cat /tmp/schwab-new-refresh-token.txt | gh secret set SCHWAB_REFRESH_TOKEN" in optionable
    assert "optionable-symbols-latest-us.json" in optionable
    assert "github.event.inputs.dry_run != 'true'" in optionable
