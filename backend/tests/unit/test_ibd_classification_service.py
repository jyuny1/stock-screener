"""Unit tests for the hybrid IBD classifier cascade.

Uses a deterministic fake embedding engine (orthogonal one-hot vectors keyed off
group keywords) so the crosswalk → embedding → LLM tiers can be exercised without
loading sentence-transformers or calling an API.
"""
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.industry import IBDIndustryGroup
from app.models.stock_universe import StockUniverse
from app.services.ibd_classification_service import (
    SOURCE_CROSSWALK,
    SOURCE_EMBEDDING,
    SOURCE_LLM,
    IBDClassificationService,
)
from app.services.ibd_crosswalk import IBDCrosswalk, build_crosswalk

DIM = 3
_KEYWORDS = {"software": 0, "drug": 1, "energy": 2}
_NEUTRAL = np.ones(DIM) / np.sqrt(DIM)


class FakeEngine:
    """encode() → one-hot for a recognised keyword, neutral vector otherwise,
    or None for the sentinel 'NOVEC' (simulates an un-embeddable stock)."""

    def __init__(self):
        self.calls = 0

    def encode(self, text: str):
        self.calls += 1
        lowered = (text or "").lower()
        if "novec" in lowered:
            return None
        for kw, idx in _KEYWORDS.items():
            if kw in lowered:
                v = np.zeros(DIM)
                v[idx] = 1.0
                return v
        return _NEUTRAL.copy()

    @staticmethod
    def cosine_similarity(left, right) -> float:
        denom = np.linalg.norm(left) * np.linalg.norm(right)
        return float(np.dot(left, right) / denom) if denom else 0.0


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_taxonomy(session):
    # Curated US groups (centroids derive from these names; no US universe rows,
    # so centroids are name-only — the foreign-market scenario).
    for sym, grp in [
        ("AAPL", "Computers-Software"),
        ("JNJ", "Medical-Drugs"),
        ("XOM", "Energy-Oil"),
    ]:
        session.add(IBDIndustryGroup(symbol=sym, industry_group=grp, market="US", source="csv"))
    session.commit()


def _add_universe(session, symbol, market, name="", sector="", industry=""):
    session.add(StockUniverse(
        symbol=symbol, name=name, market=market, sector=sector, industry=industry, is_active=True
    ))
    session.commit()


def test_crosswalk_tier_short_circuits():
    session = _make_session()
    _seed_taxonomy(session)
    _add_universe(session, "D05.SG", "SG", name="DBS", sector="Financials", industry="Banks")
    crosswalk = IBDCrosswalk(build_crosswalk(
        symbol_to_group={f"B{i}": "Banks-Money Center" for i in range(5)},
        symbol_to_sector_industry={f"B{i}": ("Financials", "Banks") for i in range(5)},
    ))
    svc = IBDClassificationService(crosswalk=crosswalk, embedding_engine=FakeEngine())

    result = svc.classify_market(session, "SG")

    assert len(result.assignments) == 1
    a = result.assignments[0]
    assert a.source == SOURCE_CROSSWALK
    assert a.industry_group == "Banks-Money Center"
    assert a.confidence == 1.0


def test_embedding_tier_attaches_above_threshold():
    session = _make_session()
    _seed_taxonomy(session)
    _add_universe(session, "0700.HK", "HK", name="Tencent", sector="Tech", industry="Software")
    svc = IBDClassificationService(crosswalk=None, embedding_engine=FakeEngine())

    result = svc.classify_market(session, "HK")

    a = result.assignments[0]
    assert a.source == SOURCE_EMBEDDING
    assert a.industry_group == "Computers-Software"
    assert a.confidence is not None and a.confidence > 0.99


def test_llm_tiebreaker_invoked_below_threshold():
    session = _make_session()
    _seed_taxonomy(session)
    # Neutral text → best cosine ~0.577; high attach threshold forces the LLM.
    _add_universe(session, "MIXED.SG", "SG", name="Conglomerate Holdings")
    captured = {}

    def fake_llm(text, shortlist):
        captured["shortlist"] = shortlist
        return shortlist[-1]  # pick a deterministic, non-top candidate

    svc = IBDClassificationService(
        crosswalk=None, embedding_engine=FakeEngine(),
        llm_tiebreaker=fake_llm, llm_model_id="test/model", attach_threshold=0.9,
    )

    result = svc.classify_market(session, "SG")

    a = result.assignments[0]
    assert a.source == SOURCE_LLM
    assert a.model_id == "test/model"
    assert a.industry_group == captured["shortlist"][-1]
    assert a.industry_group in {"Computers-Software", "Medical-Drugs", "Energy-Oil"}


def test_authoritative_symbols_are_skipped():
    session = _make_session()
    _seed_taxonomy(session)
    _add_universe(session, "RELIANCE.NS", "IN", name="Reliance", sector="Energy", industry="Oil")
    # Human override for the same symbol → must be skipped, counted authoritative.
    session.add(IBDIndustryGroup(
        symbol="RELIANCE.NS", industry_group="Energy-Oil", market="IN", source="manual"
    ))
    session.commit()
    svc = IBDClassificationService(crosswalk=None, embedding_engine=FakeEngine())

    result = svc.classify_market(session, "IN")

    assert result.assignments == []
    assert result.candidates == 0
    assert result.skipped_authoritative == 1


def test_unresolved_when_no_match_and_no_llm():
    session = _make_session()
    _seed_taxonomy(session)
    _add_universe(session, "NOVEC.SG", "SG", name="NOVEC unembeddable")
    svc = IBDClassificationService(crosswalk=None, embedding_engine=FakeEngine())

    result = svc.classify_market(session, "SG")

    assert result.assignments == []
    assert result.unresolved == ["NOVEC.SG"]
    assert result.summary()["coverage_pct"] == 0.0
