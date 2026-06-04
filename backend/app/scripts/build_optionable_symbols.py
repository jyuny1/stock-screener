"""Build the US_OPTIONABLE symbol artifact from NasdaqTrader + Schwab /chains."""

from __future__ import annotations

import argparse
import json
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
        token_service: SchwabTokenService | None = None,
        new_refresh_token_path: Path | None = None,
    ) -> None:
        self.access_token = access_token
        self.chains_url = chains_url
        self.calls_per_minute = max(1, min(int(calls_per_minute), 120))
        self.timeout_seconds = timeout_seconds
        self.token_service = token_service
        self.new_refresh_token_path = new_refresh_token_path
        self.refresh_count = 0
        self._last_call_at = 0.0

    def is_optionable(self, symbol: str) -> tuple[bool, str | None]:
        response = self._chains_request(symbol)
        if response.status_code == 401 and self.token_service is not None:
            self._refresh_access_token()
            response = self._chains_request(symbol)
        if response.status_code == 404:
            return False, "not_found"
        if response.status_code >= 400:
            return False, f"http_{response.status_code}"
        payload = response.json()
        optionable = bool(payload.get("putExpDateMap") or payload.get("callExpDateMap"))
        return optionable, None if optionable else "empty_chain"

    def _chains_request(self, symbol: str) -> requests.Response:
        self._rate_limit()
        return requests.get(
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

    def _refresh_access_token(self) -> None:
        if self.token_service is None:
            raise RuntimeError("Schwab access token expired and no token service is configured")
        token_pair = self.token_service.refresh_from_env()
        self.access_token = token_pair.access_token
        os.environ["SCHWAB_ACCESS_TOKEN"] = token_pair.access_token
        os.environ["SCHWAB_REFRESH_TOKEN"] = token_pair.new_refresh_token
        if self.new_refresh_token_path is not None:
            self.new_refresh_token_path.parent.mkdir(parents=True, exist_ok=True)
            self.new_refresh_token_path.write_text(token_pair.new_refresh_token, encoding="utf-8")
        self.refresh_count += 1
        print(f"[token] refreshed Schwab access token during scan ({self.refresh_count})", flush=True)

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
    # Checkpoints written by this script use ``optionable``. Published latest
    # artifacts use ``symbols``. Support both so retry_errors_from_latest can
    # seed prior successful symbols instead of rescanning them and accidentally
    # replacing the artifact with only the retried subset.
    optionable_symbols = payload.get("optionable")
    if optionable_symbols is None:
        optionable_symbols = payload.get("symbols")
    return {
        "optionable": list(optionable_symbols or []),
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


def _is_retryable_failure(reason: str | None) -> bool:
    return str(reason or "").startswith(("http_", "error:"))


def build_optionable_payload(
    *,
    dry_run: bool,
    symbols: list[str] | None,
    max_symbols: int | None,
    checkpoint_path: Path | None,
    calls_per_minute: int,
    max_retry_rounds: int = 3,
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
    retryable_failures = {
        symbol
        for symbol, reason in failures.items()
        if _is_retryable_failure(reason)
    }
    checked_before = optionable | (set(failures) - retryable_failures)

    if dry_run:
        optionable.update(candidates)
    else:
        token_service = SchwabTokenService.from_env()
        new_refresh_token_path = (
            Path(os.environ["SCHWAB_NEW_REFRESH_TOKEN_FILE"])
            if os.environ.get("SCHWAB_NEW_REFRESH_TOKEN_FILE")
            else None
        )
        access_token = os.environ.get("SCHWAB_ACCESS_TOKEN")
        if not access_token:
            token_pair = token_service.refresh_from_env()
            access_token = token_pair.access_token
            os.environ["SCHWAB_ACCESS_TOKEN"] = token_pair.access_token
            os.environ["SCHWAB_REFRESH_TOKEN"] = token_pair.new_refresh_token
            if new_refresh_token_path is not None:
                new_refresh_token_path.parent.mkdir(parents=True, exist_ok=True)
                new_refresh_token_path.write_text(token_pair.new_refresh_token, encoding="utf-8")
        scanner = SchwabOptionableScanner(
            access_token=access_token,
            calls_per_minute=calls_per_minute,
            token_service=token_service,
            new_refresh_token_path=new_refresh_token_path,
        )
        retry_candidates = [symbol for symbol in candidates if symbol not in checked_before]
        retry_budget = max(0, int(max_retry_rounds))
        round_index = 0
        while retry_candidates:
            round_index += 1
            total = len(retry_candidates)
            round_label = "initial" if round_index == 1 else f"retry {round_index - 1}/{retry_budget}"
            print(f"[chains] starting {round_label} round for {total} symbols", flush=True)
            for index, symbol in enumerate(retry_candidates, start=1):
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
                        f"[chains] {round_label} {index}/{total} "
                        f"optionable={len(optionable)} failures={len(failures)}",
                        flush=True,
                    )
                    _write_checkpoint(checkpoint_path, optionable=optionable, failures=failures)

            retryable_after_round = [
                symbol
                for symbol in retry_candidates
                if _is_retryable_failure(failures.get(symbol))
            ]
            if not retryable_after_round or round_index > retry_budget:
                break
            print(
                f"[chains] {len(retryable_after_round)} retryable API failures remain; "
                "starting another retry round",
                flush=True,
            )
            retry_candidates = retryable_after_round

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
    parser.add_argument(
        "--max-errors",
        type=int,
        default=0,
        help="Maximum allowed http_/error: scan failures before exiting non-zero (default 0).",
    )
    parser.add_argument(
        "--max-retry-rounds",
        type=int,
        default=3,
        help="Additional rounds for retryable http_/error: scan failures before finalizing (default 3).",
    )
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
        max_retry_rounds=max(0, int(args.max_retry_rounds)),
    )
    latest_path = output_dir / args.output_name
    latest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    dated_path = output_dir / f"optionable-symbols-us-{payload['as_of'].replace('-', '')}.json"
    dated_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {latest_path} ({len(payload['symbols'])} symbols)")
    print(f"Wrote {dated_path}")
    errors = int((payload.get("stats") or {}).get("errors") or 0)
    if not args.dry_run and errors > max(0, int(args.max_errors)):
        api_failures = {
            symbol: reason
            for symbol, reason in sorted((payload.get("failures") or {}).items())
            if _is_retryable_failure(str(reason))
        }
        print("Remaining retryable API failures:", flush=True)
        for symbol, reason in api_failures.items():
            print(f"  - {symbol}: {reason}", flush=True)
        raise SystemExit(f"Optionable scan had {errors} API errors (max {args.max_errors}); refusing success.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
