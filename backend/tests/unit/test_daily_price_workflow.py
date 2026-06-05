from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_daily_price_workflow_is_us_only_and_publishes_release_assets() -> None:
    content = (ROOT / ".github" / "workflows" / "daily-price.yml").read_text()

    assert "name: Daily Price Data" in content
    assert "workflow_dispatch" in content
    assert "schedule:" not in content
    assert "group: daily-price-us" in content
    assert "cancel-in-progress: false" in content
    assert "postgres" not in content.lower()
    assert "DATABASE_URL" not in content
    assert "pip install -r backend/requirements.txt" not in content
    assert "foundation-update-latest-us.json" in content
    assert "US_OPTIONABLE" in content
    assert "build_daily_price_artifact" in content
    assert "import_foundation_update_bundle" not in content
    assert "sync_daily_price_bundle_from_github" not in content
    assert "--refresh-daily" not in content
    assert "build_daily_price_bundle" not in content
    assert "daily-price-latest-us.json" in content
    assert "daily-price-us-*.json.gz" in content
    assert "gh release upload daily-price-data" in content
