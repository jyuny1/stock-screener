"""Classify a market's universe into IBD groups and write a release bundle.

Runs the hybrid cascade (crosswalk → local embedding → optional LLM tiebreaker)
over every active universe symbol for ``--market`` that lacks an authoritative
group, then writes ``ibd-classification-<market>-<date>-<rev>.json.gz`` plus the
``ibd-classification-latest-<market>.json`` manifest into ``--output-dir``.

Expects the universe + curated CSV already loaded into the DB (the GitHub Action
imports the weekly-reference bundle and loads the CSV in prior steps). The LLM
tiebreaker is configured purely via env vars (``IBD_LLM_*``) — see
``app.services.llm.openai_compatible_client``.

Usage:
    python -m app.scripts.build_ibd_classification_bundle --market SG --output-dir /tmp/ibd
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from app.database import SessionLocal
from app.scripts._runtime import prepare_runtime, repo_root
from app.services.ibd_classification_bundle import (
    bundle_asset_name,
    build_manifest,
    build_payload,
    latest_manifest_name,
    write_bundle,
    write_manifest,
)
from app.services.ibd_classification_service import IBDClassificationService
from app.services.ibd_crosswalk import IBDCrosswalk


def _default_crosswalk_path() -> Path:
    return repo_root() / "data" / "ibd_crosswalk.json"


def _load_crosswalk(path: Path) -> IBDCrosswalk | None:
    if path.exists():
        return IBDCrosswalk.load(path)
    print(f"WARNING: crosswalk file {path} not found; skipping deterministic tier", flush=True)
    return None


def _build_engine(disabled: bool):
    if disabled:
        return None
    try:
        from app.services.theme_embedding_service import ThemeEmbeddingEngine

        return ThemeEmbeddingEngine("all-MiniLM-L6-v2")
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: embedding engine unavailable ({exc}); skipping embedding tier", flush=True)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", required=True)
    parser.add_argument("--output-dir", default=str(repo_root() / ".tmp" / "ibd-classification"))
    parser.add_argument("--crosswalk", default=str(_default_crosswalk_path()))
    parser.add_argument("--bundle-name", default=None)
    parser.add_argument("--latest-manifest-name", default=None)
    parser.add_argument("--no-llm", action="store_true", help="Disable the LLM tiebreaker tier.")
    parser.add_argument(
        "--no-embedding",
        action="store_true",
        help="Disable the embedding tier. The LLM tiebreaker ranks the embedding "
        "shortlist, so this also disables the LLM tier (crosswalk-only).",
    )
    parser.add_argument("--as-of", default=None, help="Override as-of date (YYYY-MM-DD).")
    args = parser.parse_args()

    prepare_runtime()
    market = args.market.strip().upper()

    crosswalk = _load_crosswalk(Path(args.crosswalk))
    engine = _build_engine(args.no_embedding)

    tiebreaker = None
    model_id = None
    if not args.no_llm:
        from app.services.llm.openai_compatible_client import build_ibd_tiebreaker

        tiebreaker, model_id = build_ibd_tiebreaker()

    now = datetime.utcnow().replace(microsecond=0)
    as_of_date = args.as_of or now.date().isoformat()
    as_of_compact = as_of_date.replace("-", "")
    source_revision = f"ibd:{now.strftime('%Y%m%d%H%M%S')}"

    with SessionLocal() as db:
        service = IBDClassificationService(
            crosswalk=crosswalk,
            embedding_engine=engine,
            llm_tiebreaker=tiebreaker,
            llm_model_id=model_id,
        )
        result = service.classify_market(db, market)

    summary = result.summary()
    payload = build_payload(
        market=market,
        as_of_date=as_of_date,
        source_revision=source_revision,
        generated_at=now.isoformat() + "Z",
        model_id=model_id,
        assignments=result.assignments,
        summary=summary,
    )

    output_dir = Path(args.output_dir)
    resolved_bundle_name = args.bundle_name or bundle_asset_name(market, as_of_compact, source_revision)
    resolved_manifest_name = args.latest_manifest_name or latest_manifest_name(market)
    bundle_path = output_dir / resolved_bundle_name
    manifest_path = output_dir / resolved_manifest_name

    sha256 = write_bundle(bundle_path, payload)
    manifest = build_manifest(payload=payload, bundle_name=resolved_bundle_name, sha256=sha256)
    write_manifest(manifest_path, manifest)

    print("IBD classification bundle complete:")
    print(f"  - market:   {market}")
    print(f"  - summary:  {summary}")
    print(f"  - bundle:   {bundle_path}")
    print(f"  - manifest: {manifest_path}")
    print(f"  - sha256:   {sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
