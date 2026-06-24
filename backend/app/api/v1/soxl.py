"""SOXL-specific precomputed analytics endpoints."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SOXL_D1_DATABASE = "stock-screener-soxl-price"


def _load_latest_soxl_support_snapshot(database_name: str = DEFAULT_SOXL_D1_DATABASE) -> dict[str, Any]:
    wrangler = shutil.which("wrangler")
    command = ([wrangler] if wrangler else ["npx", "--yes", "wrangler@latest"]) + [
        "d1",
        "execute",
        database_name,
        "--remote",
        "--json",
        "--command",
        (
            "SELECT symbol, as_of, spot, daily_support_json, intraday_support_json, "
            "merged_support_json, sell_put_buckets_json, created_at "
            "FROM soxl_support_snapshots ORDER BY as_of DESC LIMIT 1"
        ),
    ]
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "wrangler d1 execute failed").strip())
    stdout = completed.stdout.strip()
    json_start = stdout.find("[")
    if json_start > 0:
        stdout = stdout[json_start:]
    payload = json.loads(stdout)
    rows = payload[0].get("results", []) if payload else []
    if not rows:
        raise RuntimeError("soxl_support_snapshots is empty")
    row = rows[0]
    return {
        "symbol": row.get("symbol") or "SOXL",
        "asOf": row.get("as_of"),
        "spot": row.get("spot"),
        "dailySupport": json.loads(row.get("daily_support_json") or "{}"),
        "intradaySupport": json.loads(row.get("intraday_support_json") or "{}"),
        "mergedSupport": json.loads(row.get("merged_support_json") or "{}"),
        "sellPutSupportBuckets": json.loads(row.get("sell_put_buckets_json") or "{}").get("sellPutSupportBuckets", []),
        "createdAt": row.get("created_at"),
        "source": "d1_soxl_support_snapshots",
    }


@router.get("/support-snapshot")
def get_soxl_support_snapshot() -> dict[str, Any]:
    try:
        return _load_latest_soxl_support_snapshot()
    except Exception as exc:  # pragma: no cover - depends on local wrangler auth/state
        raise HTTPException(status_code=502, detail=f"SOXL support snapshot unavailable: {exc}") from exc

