"""Unit tests for IBDIndustryService CSV loading and the seed+override invariant.

The curated CSV is the authoritative seed layer. ``load_from_csv`` must:
- tag loaded rows ``source='csv'`` / ``market='US'``;
- preserve classifier-derived rows for symbols the CSV does NOT claim;
- let the CSV win (replace classifier rows) for symbols it DOES claim;
- never clobber human ``manual`` overrides, even for symbols in the CSV.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.industry import IBDIndustryGroup
from app.services.ibd_industry_service import IBDIndustryService


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _write_csv(tmp_path, rows):
    path = tmp_path / "ibd.csv"
    path.write_text("".join(f"{sym},{grp}\n" for sym, grp in rows), encoding="utf-8")
    return str(path)


def test_load_tags_provenance(tmp_path):
    session = _make_session()
    csv_path = _write_csv(tmp_path, [("AAPL", "Computers-Large"), ("MSFT", "Computers-Software")])

    loaded = IBDIndustryService.load_from_csv(session, csv_path)

    assert loaded == 2
    rows = {r.symbol: r for r in session.query(IBDIndustryGroup).all()}
    assert rows["AAPL"].source == "csv"
    assert rows["AAPL"].market == "US"
    assert rows["AAPL"].industry_group == "Computers-Large"


def test_reload_preserves_classifier_row_not_in_csv(tmp_path):
    session = _make_session()
    # A classifier-assigned symbol that the CSV never mentions.
    session.add(IBDIndustryGroup(
        symbol="0700.HK", industry_group="Internet-Content",
        market="HK", source="embedding", confidence=0.91, method="centroid_nn",
    ))
    session.commit()

    csv_path = _write_csv(tmp_path, [("AAPL", "Computers-Large")])
    IBDIndustryService.load_from_csv(session, csv_path)

    hk = session.query(IBDIndustryGroup).filter_by(symbol="0700.HK").one()
    assert hk.source == "embedding"  # survived the CSV reload
    assert session.query(IBDIndustryGroup).filter_by(symbol="AAPL").one().source == "csv"


def test_reload_csv_wins_over_classifier_for_claimed_symbol(tmp_path):
    session = _make_session()
    # Classifier guessed a group for AAPL before it was hand-curated.
    session.add(IBDIndustryGroup(
        symbol="AAPL", industry_group="Wrong-Guess",
        market="US", source="llm", confidence=0.4, method="llm_shortlist",
    ))
    session.commit()

    csv_path = _write_csv(tmp_path, [("AAPL", "Computers-Large")])
    IBDIndustryService.load_from_csv(session, csv_path)

    aapl = session.query(IBDIndustryGroup).filter_by(symbol="AAPL").one()
    assert aapl.source == "csv"
    assert aapl.industry_group == "Computers-Large"  # CSV is authoritative


def test_reload_preserves_manual_override_over_csv(tmp_path):
    session = _make_session()
    # A human deliberately overrode AAPL's group.
    session.add(IBDIndustryGroup(
        symbol="AAPL", industry_group="Human-Choice",
        market="US", source="manual",
    ))
    session.commit()

    csv_path = _write_csv(tmp_path, [("AAPL", "Computers-Large")])
    IBDIndustryService.load_from_csv(session, csv_path)

    rows = session.query(IBDIndustryGroup).filter_by(symbol="AAPL").all()
    assert len(rows) == 1
    assert rows[0].source == "manual"
    assert rows[0].industry_group == "Human-Choice"  # manual wins, CSV skipped
