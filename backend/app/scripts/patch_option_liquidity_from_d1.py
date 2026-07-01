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


PCR_BUCKETS = ("dte0_30", "dte31_60", "dte61_90", "dte0_90_total")
LEGACY_BUCKET = "dte0_90_total"


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


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
        bucket = _as_text(row.get("bucket")) or LEGACY_BUCKET
        if bucket not in PCR_BUCKETS:
            continue
        call_volume = _as_int(row.get("call_volume"))
        put_volume = _as_int(row.get("put_volume"))
        pcr = _as_float(row.get("pcr"))
        if pcr is None and call_volume > 0:
            pcr = put_volume / call_volume
        normalized.append({
            "date": date[:10],
            "symbol": symbol,
            "bucket": bucket,
            "put_volume": put_volume,
            "call_volume": call_volume,
            "put_oi": _as_int(row.get("put_oi")),
            "call_oi": _as_int(row.get("call_oi")),
            "pcr": pcr,
            "put_contract_count": _as_int(row.get("put_contract_count")),
            "call_contract_count": _as_int(row.get("call_contract_count")),
            "contract_count": _as_int(row.get("contract_count")),
            "asof": _as_text(row.get("asof")),
        })
    return sorted(normalized, key=lambda item: (item["date"], item["symbol"], item["bucket"]))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _bucket_payload(entries: list[dict[str, Any]]) -> dict[str, Any]:
    history = [entry.get("pcr") for entry in entries]
    current = history[-1] if history else None
    previous = history[0] if history else None
    change = (current - previous) if current is not None and previous is not None else None
    latest = entries[-1] if entries else {}
    return {
        "current": current,
        "previous30d": previous,
        "change30d": change,
        "changePct30d": (change / previous) if change is not None and previous else None,
        "history": history,
        "putVol": latest.get("put_volume"),
        "callVol": latest.get("call_volume"),
        "putOi": latest.get("put_oi"),
        "callOi": latest.get("call_oi"),
        "contractCount": latest.get("contract_count"),
        "asof": latest.get("asof"),
    }


def _build_trend(history_by_bucket: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    dates = sorted({entry["date"] for entries in history_by_bucket.values() for entry in entries})
    trend = {"dates": dates}
    for bucket in PCR_BUCKETS:
        trend[bucket] = _bucket_payload(history_by_bucket.get(bucket, []))
    return trend


def _patch_row(row: dict[str, Any], latest_by_bucket: dict[str, dict[str, Any]], history_by_bucket: dict[str, list[dict[str, Any]]]) -> bool:
    symbol = str(row.get("symbol") or "").upper().strip()
    if not symbol:
        return False

    changed = False
    if latest_by_bucket:
        trend = _build_trend(history_by_bucket)
        total = latest_by_bucket.get("dte0_90_total")
        updates = {
            "option_pcr_trend_30d": trend,
            "option_pcr_volume_dte0_90_total": total.get("pcr") if total else None,
            "option_put_volume_dte0_90_total": total.get("put_volume") if total else None,
            "option_call_volume_dte0_90_total": total.get("call_volume") if total else None,
            "option_put_oi_dte0_90_total": total.get("put_oi") if total else None,
            "option_call_oi_dte0_90_total": total.get("call_oi") if total else None,
            "option_pcr_volume_dte0_90_total_asof": total.get("asof") if total else None,
            "option_pcr_volume_dte0_90_total_source": "cloudflare-d1",
        }
        for bucket, aggregate in latest_by_bucket.items():
            updates.update({
                f"option_pcr_volume_{bucket}": aggregate.get("pcr"),
                f"option_put_volume_{bucket}": aggregate.get("put_volume"),
                f"option_call_volume_{bucket}": aggregate.get("call_volume"),
                f"option_put_oi_{bucket}": aggregate.get("put_oi"),
                f"option_call_oi_{bucket}": aggregate.get("call_oi"),
                f"option_contracts_{bucket}_count": aggregate.get("contract_count"),
                f"option_pcr_volume_{bucket}_asof": aggregate.get("asof"),
            })
        for key, value in updates.items():
            if row.get(key) != value:
                row[key] = value
                changed = True

    if history_by_bucket:
        dates = sorted({entry["date"] for entries in history_by_bucket.values() for entry in entries})
        updates = {"option_put_liquidity_history_dates": dates}
        for bucket, entries in history_by_bucket.items():
            updates.update({
                f"option_pcr_volume_{bucket}_history": [entry.get("pcr") for entry in entries],
                f"option_put_volume_{bucket}_history": [entry.get("put_volume") for entry in entries],
                f"option_call_volume_{bucket}_history": [entry.get("call_volume") for entry in entries],
                f"option_put_oi_{bucket}_history": [entry.get("put_oi") for entry in entries],
                f"option_call_oi_{bucket}_history": [entry.get("call_oi") for entry in entries],
            })
        for key, value in updates.items():
            if row.get(key) != value:
                row[key] = value
                changed = True

    return changed


def patch_static_data(static_data_dir: Path, aggregate_rows: list[dict[str, Any]], *, history_window_days: int) -> dict[str, Any]:
    if not aggregate_rows:
        raise SystemExit("D1 aggregate result has no rows; refusing to patch static artifacts")

    latest_date = max(row["date"] for row in aggregate_rows)
    latest_by_symbol_bucket: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in aggregate_rows:
        if row["date"] == latest_date:
            latest_by_symbol_bucket[row["symbol"]][row["bucket"]] = row

    history_by_symbol_bucket: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in aggregate_rows:
        history_by_symbol_bucket[row["symbol"]][row["bucket"]].append(row)
    history_by_symbol_bucket = {
        symbol: {
            bucket: entries[-history_window_days:]
            for bucket, entries in buckets.items()
        }
        for symbol, buckets in history_by_symbol_bucket.items()
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
            if _patch_row(row, latest_by_symbol_bucket.get(symbol, {}), history_by_symbol_bucket.get(symbol, {})):
                patched_rows += 1
                changed = True
        if changed:
            _write_json(chunk_path, chunk_payload)

    for field in ("initial_rows", "preview_rows"):
        changed = False
        for row in scan_manifest.get(field) or []:
            symbol = str(row.get("symbol") or "").upper().strip()
            if _patch_row(row, latest_by_symbol_bucket.get(symbol, {}), history_by_symbol_bucket.get(symbol, {})):
                changed = True
        if changed:
            scan_manifest[f"{field}_option_liquidity_source"] = "cloudflare-d1"
    _write_json(scan_manifest_path, scan_manifest)

    history_payload = {
        "schema_version": "option-pcr-trend-history-v2",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "as_of_date": latest_date,
        "window_days": history_window_days,
        "source": "cloudflare-d1",
        "rows": {
            symbol: {
                bucket: [
                    {
                        "date": entry["date"],
                        "put_volume": entry["put_volume"],
                        "call_volume": entry["call_volume"],
                        "put_oi": entry["put_oi"],
                        "call_oi": entry["call_oi"],
                        "pcr": entry.get("pcr"),
                        "put_contract_count": entry["put_contract_count"],
                        "call_contract_count": entry["call_contract_count"],
                        "contract_count": entry["contract_count"],
                        "asof": entry.get("asof"),
                        "source": "cloudflare-d1",
                    }
                    for entry in entries
                ]
                for bucket, entries in sorted(buckets.items())
            }
            for symbol, buckets in sorted(history_by_symbol_bucket.items())
        },
    }
    _write_json(static_data_dir / "markets/us/options/put-liquidity-history.json", history_payload)

    return {
        "latest_date": latest_date,
        "aggregate_symbols": len(latest_by_symbol_bucket),
        "history_symbols": len(history_by_symbol_bucket),
        "patched_rows": patched_rows,
        "history_window_days": history_window_days,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Patch static option liquidity fields from D1 aggregate JSON")
    parser.add_argument("--static-data-dir", required=True, type=Path)
    parser.add_argument("--d1-aggregate-json", required=True, type=Path)
    parser.add_argument("--history-window-days", type=int, default=30)
    args = parser.parse_args()

    payload = json.loads(args.d1_aggregate_json.read_text(encoding="utf-8"))
    rows = _normalize_aggregate_rows(_extract_d1_rows(payload))
    summary = patch_static_data(args.static_data_dir, rows, history_window_days=args.history_window_days)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
