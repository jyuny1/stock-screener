from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_scan_metrics_workflow_is_artifact_native():
    content = (ROOT / ".github" / "workflows" / "scan-metrics.yml").read_text(encoding="utf-8")

    assert "name: Scan Metrics" in content
    assert "scan-metrics-data" in content
    assert "foundation-update-data" in content
    assert "daily-price-data" in content
    assert "build_scan_metrics_artifact" in content
    assert "scan-metrics-latest-us.json" in content
    assert "postgres" not in content.lower()
    assert "DATABASE_URL" not in content
    assert "backend/requirements.txt" not in content
    assert "pip install -r" not in content
    assert "cancel-in-progress: false" in content


def test_static_site_consumes_scan_metrics_optionally():
    content = (ROOT / ".github" / "workflows" / "static-site.yml").read_text(encoding="utf-8")

    assert "Download optional scan metrics artifact" in content
    assert "scan-metrics-data" in content
    assert "--scan-metrics \"$SCAN_METRICS_BUNDLE\"" in content
    assert "scan metrics checksum" in content.lower()
