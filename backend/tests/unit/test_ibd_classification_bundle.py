"""Unit tests for the IBD classification bundle format + import upsert."""
import hashlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.industry import IBDIndustryGroup
from app.services.ibd_classification_bundle import (
    IBD_CLASSIFICATION_BUNDLE_SCHEMA_VERSION,
    bundle_asset_name,
    build_manifest,
    build_payload,
    import_classifications,
    latest_manifest_name,
    read_bundle,
    write_bundle,
    write_manifest,
)
from app.services.ibd_classification_service import Assignment


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _assignments():
    return [
        Assignment("0700.HK", "HK", "Internet-Content", "embedding", 0.91, "centroid_nn", None),
        Assignment("9988.HK", "HK", "Retail-Internet", "crosswalk", 0.8, "sector_industry", None),
        Assignment("1810.HK", "HK", "Telecom-Equipment", "llm", None, "llm_shortlist", "deepseek-chat"),
    ]


def test_asset_names():
    assert bundle_asset_name("HK", "20260601", "ibd:20260601120000") == (
        "ibd-classification-hk-20260601-ibd-20260601120000.json.gz"
    )
    assert latest_manifest_name("SG") == "ibd-classification-latest-sg.json"


def test_bundle_roundtrip_and_manifest_sha(tmp_path):
    payload = build_payload(
        market="HK", as_of_date="2026-06-01", source_revision="ibd:1",
        generated_at="2026-06-01T00:00:00Z", model_id="deepseek-chat",
        assignments=_assignments(), summary={"newly_classified": 3},
    )
    bundle_path = tmp_path / "bundle.json.gz"
    sha = write_bundle(bundle_path, payload)

    # sha matches the file on disk.
    assert sha == hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    # round-trips losslessly.
    loaded = read_bundle(bundle_path)
    assert loaded["schema_version"] == IBD_CLASSIFICATION_BUNDLE_SCHEMA_VERSION
    assert loaded["market"] == "HK"
    assert len(loaded["classifications"]) == 3
    assert loaded["classifications"][0]["symbol"] == "0700.HK"

    manifest = build_manifest(payload=payload, bundle_name="bundle.json.gz", sha256=sha)
    manifest_path = tmp_path / latest_manifest_name("HK")
    write_manifest(manifest_path, manifest)
    assert manifest["sha256"] == sha
    assert manifest["bundle_asset_name"] == "bundle.json.gz"
    assert manifest_path.read_text().endswith("\n")


def test_import_inserts_updates_and_skips_authoritative():
    session = _make_session()
    # Pre-existing rows: one authoritative (csv), one stale classifier (embedding).
    session.add(IBDIndustryGroup(symbol="0700.HK", industry_group="Old-Group",
                                 market="HK", source="csv"))
    session.add(IBDIndustryGroup(symbol="9988.HK", industry_group="Stale-Guess",
                                 market="HK", source="embedding", confidence=0.5))
    session.commit()

    payload = build_payload(
        market="HK", as_of_date="2026-06-01", source_revision="ibd:1",
        generated_at=None, model_id="deepseek-chat",
        assignments=_assignments(), summary={},
    )
    stats = import_classifications(session, payload)

    assert stats["skipped_authoritative"] == 1  # 0700.HK (csv) untouched
    assert stats["updated"] == 1                 # 9988.HK refreshed
    assert stats["inserted"] == 1                # 1810.HK new

    rows = {r.symbol: r for r in session.query(IBDIndustryGroup).all()}
    assert rows["0700.HK"].industry_group == "Old-Group"  # csv preserved
    assert rows["0700.HK"].source == "csv"
    assert rows["9988.HK"].industry_group == "Retail-Internet"  # updated
    assert rows["9988.HK"].source == "crosswalk"
    assert rows["1810.HK"].source == "llm"
    assert rows["1810.HK"].model_version == "deepseek-chat"
