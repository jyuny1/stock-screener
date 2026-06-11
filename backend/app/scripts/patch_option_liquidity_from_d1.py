"""Patch static scan artifacts with option Put liquidity aggregates queried from D1.

The static builder fetches per-contract Schwab option-chain snapshots and emits a D1
import SQL file. After GitHub Actions imports that SQL into Cloudflare D1, the
workflow queries D1 for symbol-level aggregates and feeds the JSON output to this
script. The public API remains artifact-native: it reads the patched scan rows
from R2 instead of querying D1 directly.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _extract_d1_rows(payload: Any) -> list[dict[str, Any]]:
    """Accept common wrangler D1 JSON output shapes and return result rows."""
    if isinstance(payload, list):
        rows: list[dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("results"), list):
                rows.extend(row for row in item["results"] if isinstance(row, dict))
            elif isinstance(item, dict) and {"date", "symbol"}.issubset(item):
                rows.append(item)
        return rows

    if not isinstance(payload, dict):
        return []

    for key in ("results", "rows", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]

    result = payload.get("result")
    if isinstance(result, list):
        rows = []
        for item in result:
            if isinstance(item, dict) and isinstance(item.get("results"), list):
                rows.extend(row for row in item["results"] if isinstance(row, dict))
        return rows

    return []


def _normalize_aggregate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        date = _as_text(row.get("date"))
        symbol = (_as_text(row.get("symbol")) or "").upper()
        if not date or not symbol:
            continue
        normalized.append({
            "date": date[:10],
            "symbol": symbol,
            "put_volume": _as_int(row.get("put_volume")),
            "put_oi": _as_int(row.get("put_oi")),
            "contract_count": _as_int(row.get("contract_count")),
            "asof": _as_text(row.get("asof")),
        })
    return sorted(normalized, key=lambda item: (item["date"], item["symbol"]))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _patch_row(row: dict[str, Any], aggregate: dict[str, Any] | None, history: list[dict[str, Any]]) -> bool:
    symbol = str(row.get("symbol") or "").upper().strip()
    if not symbol:
        return False

    changed = False
    if aggregate is not None:
        updates = {
            "option_put_volume_14_28dte": aggregate["put_volume"],
            "option_put_oi_14_28dte": aggregate["put_oi"],
            "option_put_contracts_14_28dte_count": aggregate["contract_count"],
            "option_put_liquidity_14_28dte_source": "cloudflare-d1",
            "option_put_liquidity_14_28dte_asof": aggregate.get("asof"),
        }
        for key, value in updates.items():
            if row.get(key) != value:
                row[key] = value
                changed = True

    if history:
        volume_history = [entry["put_volume"] for entry in history]
        oi_history = [entry["put_oi"] for entry in history]
        dates = [entry["date"] for entry in history]
        for key, value in (
            ("option_put_volume_14_28dte_history", volume_history),
            ("option_put_oi_14_28dte_history", oi_history),
            ("option_put_liquidity_history_dates", dates),
        ):
            if row.get(key) != value:
                row[key] = value
                changed = True

    return changed


def patch_static_data(static_data_dir: Path, aggregate_rows: list[dict[str, Any]], *, history_window_days: int) -> dict[str, Any]:
    if not aggregate_rows:
        raise SystemExit("D1 aggregate result has no rows; refusing to patch static artifacts")

    latest_date = max(row["date"] for row in aggregate_rows)
    latest_by_symbol = {
        row["symbol"]: row
        for row in aggregate_rows
        if row["date"] == latest_date
    }
    history_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in aggregate_rows:
        history_by_symbol[row["symbol"]].append(row)
    history_by_symbol = {
        symbol: entries[-history_window_days:]
        for symbol, entries in history_by_symbol.items()
    }

    scan_manifest_path = static_data_dir / "markets/us/scan/manifest.json"
    scan_manifest = _read_json(scan_manifest_path)
    patched_rows = 0

    for chunk_ref in scan_manifest.get("chunks") or []:
        chunk_path_text = chunk_ref.get("path") if isinstance(chunk_ref, dict) else None
        if not isinstance(chunk_path_text, str):
            continue
        chunk_path = static_data_dir / chunk_path_text
        chunk_payload = _read_json(chunk_path)
        changed = False
        for row in chunk_payload.get("rows") or []:
            symbol = str(row.get("symbol") or "").upper().strip()
            if _patch_row(row, latest_by_symbol.get(symbol), history_by_symbol.get(symbol, [])):
                patched_rows += 1
                changed = True
        if changed:
            _write_json(chunk_path, chunk_payload)

    for field in ("initial_rows", "preview_rows"):
        changed = False
        for row in scan_manifest.get(field) or []:
            symbol = str(row.get("symbol") or "").upper().strip()
            if _patch_row(row, latest_by_symbol.get(symbol), history_by_symbol.get(symbol, [])):
                changed = True
        if changed:
            scan_manifest[f"{field}_option_liquidity_source"] = "cloudflare-d1"
    _write_json(scan_manifest_path, scan_manifest)

    history_payload = {
        "schema_version": "option-put-liquidity-history-v1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "as_of_date": latest_date,
        "window_days": history_window_days,
        "source": "cloudflare-d1",
        "rows": {
            symbol: [
                {
                    "date": entry["date"],
                    "put_volume": entry["put_volume"],
                    "put_oi": entry["put_oi"],
                    "contract_count": entry["contract_count"],
                    "asof": entry.get("asof"),
                    "source": "cloudflare-d1",
                }
                for entry in entries
            ]
            for symbol, entries in sorted(history_by_symbol.items())
        },
    }
    _write_json(static_data_dir / "markets/us/options/put-liquidity-history.json", history_payload)

    return {
        "latest_date": latest_date,
        "aggregate_symbols": len(latest_by_symbol),
        "history_symbols": len(history_by_symbol),
        "patched_rows": patched_rows,
        "history_window_days": history_window_days,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Patch static option liquidity fields from D1 aggregate JSON")
    parser.add_argument("--static-data-dir", required=True, type=Path)
    parser.add_argument("--d1-aggregate-json", required=True, type=Path)
    parser.add_argument("--history-window-days", type=int, default=7)
    args = parser.parse_args()

    payload = json.loads(args.d1_aggregate_json.read_text(encoding="utf-8"))
    rows = _normalize_aggregate_rows(_extract_d1_rows(payload))
    summary = patch_static_data(args.static_data_dir, rows, history_window_days=args.history_window_days)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
