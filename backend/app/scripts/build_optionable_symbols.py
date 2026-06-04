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
        self.last_attempts: list[dict[str, Any]] = []
        self.token_events: list[dict[str, Any]] = []

    def is_optionable(self, symbol: str) -> tuple[bool, str | None]:
        self.last_attempts = []
        response = self._chains_request(symbol)
        if response.status_code == 401 and self.token_service is not None:
            self._refresh_access_token(symbol=symbol, trigger_status=response.status_code)
            response = self._chains_request(symbol)
        if response.status_code == 400:
            return False, "bad_request"
        if response.status_code == 404:
            return False, "not_found"
        if response.status_code >= 400:
            return False, f"http_{response.status_code}"
        payload = response.json()
        optionable = bool(payload.get("putExpDateMap") or payload.get("callExpDateMap"))
        return optionable, None if optionable else "empty_chain"

    def _chains_request(self, symbol: str) -> requests.Response:
        self._rate_limit()
        started_at = _utc_now_iso()
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
        self.last_attempts.append(
            {
                "attempt": len(self.last_attempts) + 1,
                "symbol": symbol,
                "started_at": started_at,
                "completed_at": _utc_now_iso(),
                "http_status": response.status_code,
            }
        )
        return response

    def _refresh_access_token(self, *, symbol: str, trigger_status: int) -> None:
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
        event = {
            "refreshed_at": _utc_now_iso(),
            "refresh_count": self.refresh_count,
            "symbol": symbol,
            "trigger_status": trigger_status,
        }
        self.token_events.append(event)
        print(
            f"[token] refreshed Schwab access token during scan "
            f"({self.refresh_count}) symbol={symbol} trigger=http_{trigger_status}",
            flush=True,
        )

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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path | None, record: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _write_retryable_failures_snapshot(
    path: Path | None,
    *,
    round_label: str,
    failures: dict[str, str],
) -> dict[str, str]:
    retryable = {
        symbol: reason
        for symbol, reason in sorted(failures.items())
        if _is_retryable_failure(reason)
    }
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "updated_at": _utc_now_iso(),
                    "round": round_label,
                    "count": len(retryable),
                    "failures": retryable,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    return retryable


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
    diagnostics_dir: Path | None = None,
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
    attempts_log_path = diagnostics_dir / "scan-attempts.jsonl" if diagnostics_dir else None
    token_log_path = diagnostics_dir / "token-events.jsonl" if diagnostics_dir else None
    retryable_snapshot_path = diagnostics_dir / "retryable-failures.json" if diagnostics_dir else None
    summary_path = diagnostics_dir / "scan-summary.json" if diagnostics_dir else None
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
            _append_jsonl(
                token_log_path,
                {
                    "refreshed_at": _utc_now_iso(),
                    "refresh_count": 0,
                    "symbol": None,
                    "trigger_status": None,
                    "trigger": "missing_initial_access_token",
                },
            )
            print("[token] refreshed Schwab access token before scan (missing initial access token)", flush=True)
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
            round_started_at = _utc_now_iso()
            for index, symbol in enumerate(retry_candidates, start=1):
                token_refresh_count_before = scanner.refresh_count
                try:
                    is_optionable, reason = scanner.is_optionable(symbol)
                    attempts = list(scanner.last_attempts)
                    token_events = scanner.token_events[token_refresh_count_before:]
                except Exception as exc:  # pragma: no cover - defensive around remote API
                    is_optionable, reason = False, f"error:{type(exc).__name__}"
                    attempts = [
                        {
                            "attempt": 1,
                            "symbol": symbol,
                            "completed_at": _utc_now_iso(),
                            "exception_type": type(exc).__name__,
                        }
                    ]
                    token_events = []
                if is_optionable:
                    optionable.add(symbol)
                    failures.pop(symbol, None)
                else:
                    failures[symbol] = reason or "not_optionable"
                attempt_record = {
                    "recorded_at": _utc_now_iso(),
                    "round": round_label,
                    "round_index": round_index,
                    "symbol": symbol,
                    "optionable": bool(is_optionable),
                    "reason": reason,
                    "attempts": attempts,
                    "token_refreshes_during_symbol": len(token_events),
                }
                _append_jsonl(attempts_log_path, attempt_record)
                for token_event in token_events:
                    _append_jsonl(token_log_path, token_event)
                if index % 25 == 0 or index == total:
                    print(
                        f"[chains] {round_label} {index}/{total} "
                        f"optionable={len(optionable)} failures={len(failures)}",
                        flush=True,
                    )
                    _write_checkpoint(checkpoint_path, optionable=optionable, failures=failures)

            retryable_after_round_map = _write_retryable_failures_snapshot(
                retryable_snapshot_path,
                round_label=round_label,
                failures={symbol: failures[symbol] for symbol in retry_candidates if symbol in failures},
            )
            retryable_after_round = list(retryable_after_round_map)
            if retryable_after_round_map:
                reason_counts: dict[str, int] = {}
                for reason in retryable_after_round_map.values():
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                print(
                    f"[chains] {round_label} retryable failure reasons: "
                    f"{dict(sorted(reason_counts.items()))}",
                    flush=True,
                )
            if summary_path is not None:
                summary_path.write_text(
                    json.dumps(
                        {
                            "updated_at": _utc_now_iso(),
                            "round": round_label,
                            "round_started_at": round_started_at,
                            "round_completed_at": _utc_now_iso(),
                            "round_candidates": total,
                            "optionable": len(optionable),
                            "failures": len(failures),
                            "retryable_failures_after_round": len(retryable_after_round),
                            "token_refresh_count": scanner.refresh_count,
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
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
        diagnostics_dir=output_dir,
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
