"""Drift guards for duplicated Market facts.

These tests intentionally document today's compatibility drift instead of
changing runtime behavior. Later harmonization tasks should remove the
exception dictionaries as each consumer moves behind Market Catalog.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.api.v1 import breadth, groups
from app.domain.markets import SUPPORTED_MARKET_CODES, market_registry
from app.domain.markets.catalog import get_market_catalog
from app.services import provider_routing_policy
from app.services.field_capability_registry import field_capability_registry
from app.services.security_master_service import SecurityMasterResolver
from app.tasks import market_queues


REPO_ROOT = Path(__file__).resolve().parents[5]

DOCUMENTED_BACKWARD_COMPATIBLE_CATALOG_ALIASES: dict[str, set[str]] = {
    "US": {"NYSE", "NASDAQ", "AMEX"},
    "HK": {"HKEX", "SEHK"},
    "IN": {"NSE", "BSE"},
    "JP": {"TSE", "JPX"},
    "KR": {"KOSPI", "KOSDAQ", "KRX"},
    "TW": {"TWSE", "TPEX"},
    "CN": {"SSE", "SZSE", "BJSE"},
    "CA": {"TSX", "TSXV"},
    "DE": {"XETRA", "FRA", "FWB"},
    "SG": {"SGX", "SES"},
}

DOCUMENTED_REGISTRY_ONLY_EXCHANGE_ALIASES: dict[str, set[str]] = {
    "US": {"XNYS", "XNAS", "XASE"},
    "CN": {"SHSE", "XBEI"},
}

DOCUMENTED_CATALOG_ONLY_EXCHANGE_ALIASES: dict[str, set[str]] = {}


def _catalog_codes() -> list[str]:
    return get_market_catalog().supported_market_codes()


def _catalog_market_codes_by_capability(capability: str) -> set[str]:
    catalog = get_market_catalog()
    return {
        code
        for code in catalog.supported_market_codes()
        if getattr(catalog.get(code).capabilities, capability)
    }


def _fallback_catalog_codes_from_frontend() -> list[str]:
    runtime_context = REPO_ROOT / "frontend" / "src" / "contexts" / "RuntimeContext.jsx"
    source = runtime_context.read_text()
    fallback_catalog = source.split(
        "export const DEFAULT_MARKET_CATALOG = {", maxsplit=1
    )[1].split("const DEFAULT_SUPPORTED_MARKETS", maxsplit=1)[0]
    return re.findall(r"code:\s*'([A-Z]{2})'", fallback_catalog)


def test_supported_market_code_surfaces_match_catalog_codes() -> None:
    expected = set(_catalog_codes())

    assert SUPPORTED_MARKET_CODES == expected
    assert set(market_registry.supported_market_codes()) == expected
    assert set(market_queues.SUPPORTED_MARKETS) == expected
    assert set(provider_routing_policy.KNOWN_MARKETS) == expected
    assert set(provider_routing_policy.supported_markets()) == expected
    assert set(field_capability_registry.MARKET_ORDER) == expected


def test_runtime_order_surfaces_match_catalog_order() -> None:
    catalog_order = tuple(_catalog_codes())

    assert market_registry.supported_market_codes() == catalog_order
    assert market_queues.SUPPORTED_MARKETS == catalog_order


def test_catalog_exchange_aliases_are_mics_or_documented_compatibility_aliases() -> None:
    catalog = get_market_catalog()

    for code in catalog.supported_market_codes():
        documented_aliases = DOCUMENTED_BACKWARD_COMPATIBLE_CATALOG_ALIASES.get(
            code,
            set(),
        )
        undocumented = {
            exchange
            for exchange in catalog.get(code).exchanges
            if not exchange.startswith("X") and exchange not in documented_aliases
        }
        assert undocumented == set(), f"{code} has undocumented aliases: {undocumented}"


def test_catalog_and_registry_exchange_alias_drift_is_documented() -> None:
    catalog = get_market_catalog()

    for code in catalog.supported_market_codes():
        catalog_exchanges = set(catalog.get(code).exchanges)
        registry_exchanges = set(market_registry.profile(code).exchanges)

        assert catalog_exchanges - registry_exchanges == (
            DOCUMENTED_CATALOG_ONLY_EXCHANGE_ALIASES.get(code, set())
        )
        assert registry_exchanges - catalog_exchanges == (
            DOCUMENTED_REGISTRY_ONLY_EXCHANGE_ALIASES.get(code, set())
        )


def test_bse_alias_ambiguity_is_market_scoped_in_current_callers() -> None:
    resolver = SecurityMasterResolver()

    india_bse = resolver.resolve_identity(symbol="500325", exchange="BSE")
    china_bse = resolver.resolve_identity(symbol="920118", market="CN", exchange="BSE")

    assert india_bse.market == "IN"
    assert india_bse.canonical_symbol == "500325.BO"
    assert china_bse.market == "CN"
    assert china_bse.canonical_symbol == "920118.BJ"


def test_frontend_fallback_catalog_codes_match_backend_catalog_codes() -> None:
    assert _fallback_catalog_codes_from_frontend() == _catalog_codes()


def test_endpoint_capability_allowlists_match_catalog_capabilities() -> None:
    assert breadth.SUPPORTED_BREADTH_MARKETS == _catalog_market_codes_by_capability(
        "breadth"
    )
    assert groups.SUPPORTED_GROUP_MARKETS == _catalog_market_codes_by_capability(
        "group_rankings"
    )
