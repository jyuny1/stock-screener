"""Derive the GICS/sector → IBD industry-group crosswalk from curated labels.

Reads the curated ``data/IBD_industry_group.csv`` (authoritative ``symbol → IBD
group``) and joins it against the classification attributes already in the DB
(``stock_industry`` GICS sub-industry, ``stock_universe`` sector/industry) to
majority-vote an IBD group per attribute key. Writes ``data/ibd_crosswalk.json``.

This is a one-time / regenerable dev step — run it after the curated CSV or GICS
data changes. The committed JSON is what the classifier loads at runtime, so the
classifier does not need ``stock_industry`` populated in CI.

Usage:
    python -m app.scripts.build_ibd_crosswalk [--csv PATH] [--output PATH] [--as-of ISO8601]
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from app.database import SessionLocal
from app.models.stock import StockIndustry
from app.models.stock_universe import StockUniverse
from app.scripts._runtime import prepare_runtime, repo_root
from app.services.ibd_crosswalk import build_crosswalk


def _default_csv_path() -> Path:
    return repo_root() / "data" / "IBD_industry_group.csv"


def _default_output_path() -> Path:
    return repo_root() / "data" / "ibd_crosswalk.json"


def _parse_csv(csv_path: Path) -> dict[str, str]:
    symbol_to_group: dict[str, str] = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) != 2:
                continue
            symbol, group = row[0].strip().upper(), row[1].strip()
            if symbol and group:
                symbol_to_group[symbol] = group
    return symbol_to_group


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(_default_csv_path()))
    parser.add_argument("--output", default=str(_default_output_path()))
    parser.add_argument(
        "--as-of",
        default=None,
        help="ISO timestamp stamped into the artifact (defaults to unset for determinism).",
    )
    args = parser.parse_args()

    prepare_runtime()

    symbol_to_group = _parse_csv(Path(args.csv))
    symbols = list(symbol_to_group.keys())

    symbol_to_subindustry: dict[str, str] = {}
    symbol_to_sector_industry: dict[str, tuple[str, str]] = {}

    with SessionLocal() as db:
        for start in range(0, len(symbols), 500):
            chunk = symbols[start:start + 500]
            for sym, sub in db.query(
                StockIndustry.symbol, StockIndustry.sub_industry
            ).filter(StockIndustry.symbol.in_(chunk)).all():
                if sub:
                    symbol_to_subindustry[sym] = sub
            for sym, sector, industry in db.query(
                StockUniverse.symbol, StockUniverse.sector, StockUniverse.industry
            ).filter(StockUniverse.symbol.in_(chunk)).all():
                symbol_to_sector_industry[sym] = (sector or "", industry or "")

    crosswalk = build_crosswalk(
        symbol_to_group=symbol_to_group,
        symbol_to_subindustry=symbol_to_subindustry,
        symbol_to_sector_industry=symbol_to_sector_industry,
        generated_at=args.as_of,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(crosswalk, f, indent=2, sort_keys=True)
        f.write("\n")

    print("IBD crosswalk build complete:")
    print(f"  - labelled symbols:        {len(symbol_to_group)}")
    print(f"  - with GICS sub-industry:  {len(symbol_to_subindustry)}")
    print(f"  - with sector/industry:    {len(symbol_to_sector_industry)}")
    print(f"  - gics_subindustry keys:   {len(crosswalk['gics_subindustry'])}")
    print(f"  - sector_industry keys:    {len(crosswalk['sector_industry'])}")
    print(f"  - sector keys:             {len(crosswalk['sector'])}")
    print(f"  - output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
