from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_foundation_update_workflow_is_artifact_native_us_only():
    content = (ROOT / ".github" / "workflows" / "foundation-update.yml").read_text(encoding="utf-8")

    assert "name: Foundation Update" in content
    assert "optionable-symbols-latest-us.json" in content
    assert "foundation-update-data" in content
    assert "build_foundation_update_artifact" in content
    assert "foundation-update-latest-us.json" in content
    assert "foundation-update-us-*.json.gz" in content
    assert "weekly-reference-data" in content  # prior compatibility fallback only
    assert "postgres" not in content.lower()
    assert "DATABASE_URL" not in content
    assert "backend/requirements.txt" not in content
    assert "pip install -r" not in content
    assert "cancel-in-progress: false" in content


def test_foundation_update_workflow_has_contract_gates():
    content = (ROOT / ".github" / "workflows" / "foundation-update.yml").read_text(encoding="utf-8")

    assert "symbol_coverage" in content
    assert "field_coverage" in content
    assert "company_name" in content
    assert "market_cap" in content
    assert "sha256" in content
