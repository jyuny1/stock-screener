"""Bundle/manifest format + import for the IBD classification release artifact.

Mirrors the weekly-reference / daily-price convention: a gzipped JSON ``bundle``
plus a small ``latest`` manifest carrying the bundle filename and its sha256.
The artifact is published to the ``ibd-classification-data`` GitHub release and
consumed by the daily static-site build.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..models.industry import IBDIndustryGroup
from .ibd_industry_service import IBDIndustryService

IBD_CLASSIFICATION_BUNDLE_SCHEMA_VERSION = 1
IBD_CLASSIFICATION_MANIFEST_SCHEMA_VERSION = 1

RELEASE_TAG = "ibd-classification-data"


def bundle_asset_name(market: str, as_of_compact: str, revision: str) -> str:
    rev = (revision or "snapshot").replace(":", "-").replace("/", "-")
    return f"ibd-classification-{market.lower()}-{as_of_compact}-{rev}.json.gz"


def latest_manifest_name(market: str) -> str:
    return f"ibd-classification-latest-{market.lower()}.json"


def write_bundle(output_path: Path, payload: dict) -> str:
    """Write the gzipped JSON bundle; return its sha256 hex digest."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    # mtime=0 keeps the gzip header deterministic for a given payload.
    with gzip.GzipFile(filename=str(output_path), mode="wb", mtime=0) as gz:
        gz.write(data)
    return hashlib.sha256(output_path.read_bytes()).hexdigest()


def read_bundle(input_path: Path) -> dict:
    with gzip.open(input_path, "rb") as gz:
        return json.loads(gz.read().decode("utf-8"))


def build_payload(
    *,
    market: str,
    as_of_date: str,
    source_revision: str,
    generated_at: str | None,
    model_id: str | None,
    assignments: Iterable[Any],
    summary: dict,
) -> dict:
    rows = [
        {
            "symbol": a.symbol,
            "market": a.market,
            "industry_group": a.industry_group,
            "source": a.source,
            "confidence": a.confidence,
            "method": a.method,
            "model_id": a.model_id,
        }
        for a in assignments
    ]
    return {
        "schema_version": IBD_CLASSIFICATION_BUNDLE_SCHEMA_VERSION,
        "market": market.upper(),
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "source_revision": source_revision,
        "generator": "ibd_classification",
        "model_id": model_id,
        "summary": summary,
        "classifications": rows,
    }


def build_manifest(*, payload: dict, bundle_name: str, sha256: str) -> dict:
    return {
        "schema_version": IBD_CLASSIFICATION_MANIFEST_SCHEMA_VERSION,
        "market": payload["market"],
        "generated_at": payload.get("generated_at"),
        "as_of_date": payload.get("as_of_date"),
        "source_revision": payload.get("source_revision"),
        "bundle_asset_name": bundle_name,
        "sha256": sha256,
        "summary": payload.get("summary"),
    }


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def import_classifications(db: Session, payload: dict) -> dict:
    """Upsert a bundle's classifications into ``ibd_industry_groups``.

    Authoritative rows (``source in {csv, manual}``) are never overwritten — the
    curated CSV and human overrides win. Non-authoritative rows are updated in
    place; missing symbols are inserted.
    """
    if payload.get("schema_version") != IBD_CLASSIFICATION_BUNDLE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported IBD classification bundle schema: {payload.get('schema_version')}"
        )

    rows = payload.get("classifications", [])
    symbols = [r["symbol"] for r in rows]

    existing: dict[str, IBDIndustryGroup] = {}
    for start in range(0, len(symbols), 500):
        chunk = symbols[start:start + 500]
        for row in db.query(IBDIndustryGroup).filter(IBDIndustryGroup.symbol.in_(chunk)).all():
            existing[row.symbol] = row

    inserted = updated = skipped_authoritative = 0
    for r in rows:
        symbol = r["symbol"]
        current = existing.get(symbol)
        if current is not None and current.source in IBDIndustryService.AUTHORITATIVE_SOURCES:
            skipped_authoritative += 1
            continue
        if current is None:
            db.add(IBDIndustryGroup(
                symbol=symbol,
                industry_group=r["industry_group"],
                market=r.get("market") or payload.get("market") or "US",
                source=r.get("source") or "embedding",
                confidence=r.get("confidence"),
                method=r.get("method"),
                model_version=r.get("model_id"),
            ))
            inserted += 1
        else:
            current.industry_group = r["industry_group"]
            current.market = r.get("market") or current.market
            current.source = r.get("source") or current.source
            current.confidence = r.get("confidence")
            current.method = r.get("method")
            current.model_version = r.get("model_id")
            updated += 1

    db.commit()
    return {
        "market": payload.get("market"),
        "rows": len(rows),
        "inserted": inserted,
        "updated": updated,
        "skipped_authoritative": skipped_authoritative,
    }
