"""Static-site and foundation-update workflow coverage."""

from __future__ import annotations

from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_foundation_update_workflow_is_us_only_for_static_pipeline():
    content = (_PROJECT_ROOT / ".github" / "workflows" / "foundation-update.yml").read_text(encoding="utf-8")

    assert "prepare:" in content
    assert "build-etf:" in content
    assert "build-stock:" in content
    assert "merge-publish:" in content
    assert "optionable-symbols-latest-us.json" in content
    assert "foundation-update-latest-us.json" in content
    assert "postgres" not in content.lower()


def test_static_workflow_is_us_only_artifact_native():
    content = (_PROJECT_ROOT / ".github" / "workflows" / "static-site.yml").read_text(encoding="utf-8")

    assert "build_static_site_from_artifacts" in content
    assert "foundation-update-latest-us.json" in content
    assert "foundation-update-data" in content
    assert "weekly-reference-data" in content  # compatibility fallback until first foundation-update publish
    assert "postgres" not in content.lower()
    assert "DATABASE_URL" not in content


def test_static_workflow_falls_back_to_legacy_reference_until_foundation_update_exists():
    content = (_PROJECT_ROOT / ".github" / "workflows" / "static-site.yml").read_text(encoding="utf-8")

    assert "No foundation-update-data release yet; falling back to legacy weekly-reference-data." in content
    assert "FOUNDATION_UPDATE_RELEASE=\"weekly-reference-data\"" in content
    assert "FOUNDATION_UPDATE_MANIFEST=\"weekly-reference-latest-us.json\"" in content


def test_local_celery_startup_derives_market_workers_from_backend_topology():
    content = (_PROJECT_ROOT / "backend" / "start_celery.sh").read_text(encoding="utf-8")

    assert "from app.tasks.market_queues import SUPPORTED_MARKETS" in content
    assert "from app.tasks.market_queues import all_data_fetch_queues" in content
    assert 'ENABLED_MARKETS="${ENABLED_MARKETS:-$SUPPORTED_MARKETS}"' in content
    assert '-Q "$DATA_FETCH_QUEUES"' in content
    assert "US|HK|IN|JP|KR|TW|CN|CA|DE|SG|MY" not in content
