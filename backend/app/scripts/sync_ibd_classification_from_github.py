"""Sync the latest IBD classification bundle from the GitHub release into the DB.

Downloads ``ibd-classification-latest-<market>.json`` + its bundle from the
``ibd-classification-data`` release (validating sha256) and imports the
classifications, preserving authoritative CSV/manual rows. Used by the daily
static-site build after the curated CSV is loaded.

Usage:
    python -m app.scripts.sync_ibd_classification_from_github --market SG [--allow-stale]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.config import settings
from app.database import SessionLocal
from app.scripts._runtime import prepare_runtime
from app.services.github_release_sync_service import GitHubReleaseSyncService
from app.services.ibd_classification_bundle import (
    IBD_CLASSIFICATION_MANIFEST_SCHEMA_VERSION,
    RELEASE_TAG,
    import_classifications,
    latest_manifest_name,
    read_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", required=True)
    parser.add_argument("--output-dir", default=str(Path(".tmp") / "ibd-classification"))
    parser.add_argument("--allow-stale", action="store_true")
    args = parser.parse_args()

    prepare_runtime()
    market = args.market.strip().upper()

    repository = getattr(settings, "github_data_repository", "") or ""
    api_base = getattr(settings, "github_data_api_base", "https://api.github.com")
    token = getattr(settings, "github_data_token", "") or None
    timeout = int(getattr(settings, "github_data_timeout_seconds", 60) or 60)
    source_mode = getattr(settings, "market_data_source_mode", "github_first")

    sync = GitHubReleaseSyncService(api_base=api_base)
    result = sync.fetch_latest_bundle(
        repository_full_name=repository,
        release_tag=RELEASE_TAG,
        manifest_asset_name=latest_manifest_name(market),
        source_mode=source_mode,
        expected_manifest_schema=str(IBD_CLASSIFICATION_MANIFEST_SCHEMA_VERSION),
        required_manifest_keys=("bundle_asset_name", "sha256"),
        allow_stale=args.allow_stale,
        github_token=token,
        request_timeout_seconds=timeout,
        output_dir=args.output_dir,
    )

    status = result.get("status")
    print(f"IBD classification GitHub sync: status={status}")
    if status != "success":
        for key in ("reason", "error", "stale_reason"):
            if result.get(key):
                print(f"  - {key}: {result[key]}")
        # live_only / up_to_date are non-fatal; anything else is a soft failure.
        return 0 if status in {"live_only", "up_to_date"} else 1

    payload = read_bundle(Path(result["bundle_path"]))
    with SessionLocal() as db:
        stats = import_classifications(db, payload)
    print("Imported:")
    for key, value in stats.items():
        print(f"  - {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
