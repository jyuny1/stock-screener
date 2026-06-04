"""Build the US_OPTIONABLE symbol artifact from NasdaqTrader + Schwab /chains."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.services.nasdaqtrader_universe_service import NasdaqTraderUniverseService
from app.services.schwab_token_service import SchwabTokenService

SCHWAB_CHAINS_URL = "https://api.schwabapi.com/marketdata/v1/chains"
SCHEMA_VERSION = "optionable-symbols-v1"


def _default_output_dir() -> Path:
    return Path.cwd() / ".tmp" / "optionable-symbols"


class SchwabOptionableScanner:
    def __init__(
        self,
        *,
        access_token: str,
        chains_url: str = SCHWAB_CHAINS_URL,
        calls_per_minute: int = 110,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.access_token = access_token
        self.chains_url = chains_url
        self.calls_per_minute = max(1, min(int(calls_per_minute), 120))
        self.timeout_seconds = timeout_seconds
        self._last_call_at = 0.0

    def is_optionable(self, symbol: str) -> tuple[bool, str | None]:
        self._rate_limit()
        response = requests.get(
            self.chains_url,
            params={
                "symbol": symbol,
                "contractType": "ALL",
                "strikeCount": 1,
                "includeUnderlyingQuote": "false",
                "strategy": "SINGLE",
            },
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=self.timeout_seconds,
        )
        if response.status_code == 404:
            return False, "not_found"
        if response.status_code >= 400:
            return False, f"http_{response.status_code}"
        payload = response.json()
        optionable = bool(payload.get("putExpDateMap") or payload.get("callExpDateMap"))
        return optionable, None if optionable else "empty_chain"

    def _rate_limit(self) -> None:
        min_interval = 60.0 / float(self.calls_per_minute)
        now = time.monotonic()
        sleep_for = self._last_call_at + min_interval - now
        if sleep_for > 0:
            time.sleep(sleep_for)
        self._last_call_at = time.monotonic()


def _load_checkpoint(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {"optionable": [], "failures": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"optionable": [], "failures": {}}
    return {
        "optionable": list(payload.get("optionable") or []),
        "failures": dict(payload.get("failures") or {}),
    }


def _write_checkpoint(path: Path | None, *, optionable: set[str], failures: dict[str, str]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"optionable": sorted(optionable), "failures": dict(sorted(failures.items()))},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def build_optionable_payload(
    *,
    dry_run: bool,
    symbols: list[str] | None,
    max_symbols: int | None,
    checkpoint_path: Path | None,
    calls_per_minute: int,
) -> dict[str, Any]:
    service = NasdaqTraderUniverseService()
    snapshot = service.fetch_clean_snapshot()
    rows_by_symbol = {row.symbol: row for row in snapshot.rows}
    candidates = [symbol.upper() for symbol in symbols] if symbols else list(rows_by_symbol)
    candidates = [symbol for symbol in candidates if symbol in rows_by_symbol]
    if max_symbols is not None:
        candidates = candidates[: max(0, max_symbols)]

    checkpoint = _load_checkpoint(checkpoint_path)
    optionable = {str(symbol).upper() for symbol in checkpoint["optionable"]}
    failures = {str(symbol).upper(): str(reason) for symbol, reason in checkpoint["failures"].items()}
    checked_before = optionable | set(failures)

    if dry_run:
        optionable.update(candidates)
    else:
        access_token = os.environ.get("SCHWAB_ACCESS_TOKEN")
        if not access_token:
            token_pair = SchwabTokenService.from_env().refresh_from_env()
            access_token = token_pair.access_token
            new_refresh_token_path = os.environ.get("SCHWAB_NEW_REFRESH_TOKEN_FILE")
            if new_refresh_token_path:
                token_path = Path(new_refresh_token_path)
                token_path.parent.mkdir(parents=True, exist_ok=True)
                token_path.write_text(token_pair.new_refresh_token, encoding="utf-8")
        scanner = SchwabOptionableScanner(
            access_token=access_token,
            calls_per_minute=calls_per_minute,
        )
        total = len(candidates)
        for index, symbol in enumerate(candidates, start=1):
            if symbol in checked_before:
                continue
            try:
                is_optionable, reason = scanner.is_optionable(symbol)
            except Exception as exc:  # pragma: no cover - defensive around remote API
                is_optionable, reason = False, f"error:{type(exc).__name__}"
            if is_optionable:
                optionable.add(symbol)
                failures.pop(symbol, None)
            else:
                failures[symbol] = reason or "not_optionable"
            if index % 25 == 0 or index == total:
                print(
                    f"[chains] checked {index}/{total} optionable={len(optionable)} failures={len(failures)}",
                    flush=True,
                )
                _write_checkpoint(checkpoint_path, optionable=optionable, failures=failures)

    selected = sorted(symbol for symbol in optionable if symbol in rows_by_symbol)
    metadata = {symbol: asdict(rows_by_symbol[symbol]) for symbol in selected}
    checked = len((set(candidates) & (set(selected) | set(failures)))) if not dry_run else len(candidates)
    return {
        "schema_version": SCHEMA_VERSION,
        "market": "US",
        "universe_mode": "US_OPTIONABLE",
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "source": {
            "symbols": "nasdaqtrader",
            "option_chain_provider": "schwab" if not dry_run else "dry_run",
        },
        "stats": {
            "raw_symbols": snapshot.raw_symbols,
            "filtered_symbols": snapshot.filtered_symbols,
            "checked": checked,
            "optionable": len(selected),
            "not_optionable": max(checked - len(selected), 0),
            "errors": sum(1 for reason in failures.values() if reason.startswith(("http_", "error:"))),
        },
        "symbols": selected,
        "symbol_metadata": metadata,
        "failures": dict(sorted(failures.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(_default_output_dir()))
    parser.add_argument("--output-name", default="optionable-symbols-latest-us.json")
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--calls-per-minute", type=int, default=110)
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--symbols", default="", help="Optional comma-separated subset for smoke tests.")
    parser.add_argument("--dry-run", action="store_true", help="Skip Schwab and emit filtered candidates as optionable.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = Path(args.checkpoint) if args.checkpoint else output_dir / ".optionable-symbols-checkpoint.json"
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()] or None
    payload = build_optionable_payload(
        dry_run=bool(args.dry_run),
        symbols=symbols,
        max_symbols=args.max_symbols,
        checkpoint_path=checkpoint_path,
        calls_per_minute=args.calls_per_minute,
    )
    latest_path = output_dir / args.output_name
    latest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    dated_path = output_dir / f"optionable-symbols-us-{payload['as_of'].replace('-', '')}.json"
    dated_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {latest_path} ({len(payload['symbols'])} symbols)")
    print(f"Wrote {dated_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
