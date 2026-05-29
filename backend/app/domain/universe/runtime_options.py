"""Runtime Universe option payloads derived from catalog-backed definitions."""

from __future__ import annotations

from dataclasses import asdict

from ..markets.catalog import CATALOG_VERSION, get_market_catalog
from ..markets.mic_aliases import mic_alias_registry
from .indexes import index_registry
from .listing_tiers import listing_tier_registry


def _market_universe_def(market: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"type": "market", "market": market}
    payload.update({key: value for key, value in overrides.items() if value is not None})
    return payload


def build_runtime_universe_options_payload(
    *,
    enabled_markets: list[str],
) -> dict[str, object]:
    """Build stable Universe choices without live scan-readiness state."""
    catalog = get_market_catalog()
    enabled = {market.strip().upper() for market in enabled_markets}
    markets: list[dict[str, object]] = []
    for code in catalog.supported_market_codes():
        entry = catalog.get(code)
        mic_aliases_by_mic: dict[str, list[str]] = {mic: [] for mic in entry.mics}
        mic_alias_options: list[dict[str, object]] = []
        for alias in mic_alias_registry.aliases(code):
            resolved = mic_alias_registry.resolve(code, alias)
            if resolved is None or resolved.alias == resolved.mic:
                continue
            mic_aliases_by_mic.setdefault(resolved.mic, []).append(resolved.alias)
            mic_alias_options.append(
                {
                    "value": f"market:{code}:alias:{resolved.alias}",
                    "alias": resolved.alias,
                    "mic": resolved.mic,
                    "label": resolved.alias,
                    "universe_def": _market_universe_def(code, mic=resolved.mic),
                }
            )

        mics = [
            {
                "value": f"market:{code}:mic:{facts.mic}",
                "label": facts.mic,
                "mic": facts.mic,
                "aliases": mic_aliases_by_mic.get(facts.mic, []),
                "universe_def": _market_universe_def(code, mic=facts.mic),
            }
            for facts in entry.mic_facts
        ]
        indexes = [
            {
                "value": f"index:{definition.key}",
                "label": definition.label,
                "key": definition.key,
                "aliases": list(definition.aliases),
                "universe_def": {"type": "index", "index": definition.key},
            }
            for definition in index_registry.definitions(code)
        ]
        listing_tiers = []
        for definition in listing_tier_registry.definitions(code):
            tier_value = f"market:{code}:tier:{definition.key}"
            if definition.mic:
                tier_value = f"market:{code}:mic:{definition.mic}:tier:{definition.key}"
            listing_tiers.append(
                {
                    "value": tier_value,
                    "label": definition.label,
                    "key": definition.key,
                    "mic": definition.mic,
                    "aliases": list(definition.aliases),
                    "universe_def": _market_universe_def(
                        code,
                        mic=definition.mic,
                        listing_tier=definition.key,
                    ),
                }
            )

        markets.append(
            {
                "code": code,
                "label": entry.label,
                "enabled": code in enabled,
                "capabilities": asdict(entry.capabilities),
                "market": {
                    "value": f"market:{code}",
                    "label": f"All {entry.label}",
                    "universe_def": _market_universe_def(code),
                },
                "mics": mics,
                "mic_aliases": mic_alias_options,
                "indexes": indexes,
                "listing_tiers": listing_tiers,
            }
        )
    return {
        "version": CATALOG_VERSION,
        "supported_markets": catalog.supported_market_codes(),
        "enabled_markets": enabled_markets,
        "markets": markets,
    }
