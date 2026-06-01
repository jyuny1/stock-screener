"""Import an IBD classification bundle into the local database.

Upserts the bundle's classifications into ``ibd_industry_groups`` without ever
overwriting authoritative (``csv``/``manual``) rows. Run after loading the curated
CSV so the CSV remains the seed/override layer.

Usage:
    python -m app.scripts.import_ibd_classification_bundle --input /tmp/ibd/ibd-classification-sg-...json.gz
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.database import SessionLocal
from app.scripts._runtime import prepare_runtime
from app.services.ibd_classification_bundle import import_classifications, read_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to the .json.gz bundle.")
    args = parser.parse_args()

    prepare_runtime()
    payload = read_bundle(Path(args.input))

    with SessionLocal() as db:
        stats = import_classifications(db, payload)

    print("IBD classification import complete:")
    print(f"  - input: {args.input}")
    for key, value in stats.items():
        print(f"  - {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
