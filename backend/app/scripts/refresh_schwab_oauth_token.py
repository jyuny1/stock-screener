"""Refresh Schwab OAuth tokens for GitHub Actions workflows.

Schwab rotates refresh tokens. Workflows that call Schwab should persist the new
refresh token before making longer-running market-data requests.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from urllib import parse, request


SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def refresh_schwab_token() -> tuple[str, str]:
    client_id = _required_env("SCHWAB_CLIENT_ID")
    client_secret = _required_env("SCHWAB_CLIENT_SECRET")
    refresh_token = _required_env("SCHWAB_REFRESH_TOKEN")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    body = parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode("utf-8")
    req = request.Request(
        SCHWAB_TOKEN_URL,
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    access_token = str(payload.get("access_token") or "")
    new_refresh_token = str(payload.get("refresh_token") or "")
    if not access_token or not new_refresh_token:
        raise RuntimeError("Schwab token refresh response did not include both tokens")
    return access_token, new_refresh_token


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Schwab OAuth token pair")
    parser.add_argument("--access-token-file", required=True, type=Path)
    parser.add_argument("--refresh-token-file", required=True, type=Path)
    args = parser.parse_args()

    access_token, refresh_token = refresh_schwab_token()
    args.access_token_file.parent.mkdir(parents=True, exist_ok=True)
    args.refresh_token_file.parent.mkdir(parents=True, exist_ok=True)
    args.access_token_file.write_text(access_token, encoding="utf-8")
    args.refresh_token_file.write_text(refresh_token, encoding="utf-8")
    print("Schwab token refresh succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
