"""Schwab OAuth token refresh helper.

The refresh token is rotated by Schwab. Callers must persist ``new_refresh_token``
immediately after a successful refresh and before using the access token in a
long-running workflow.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import requests


SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"


@dataclass(frozen=True)
class SchwabTokenPair:
    access_token: str
    new_refresh_token: str
    expires_in: int | None = None


class SchwabTokenService:
    def __init__(self, *, token_url: str = SCHWAB_TOKEN_URL, timeout_seconds: float = 30.0) -> None:
        self.token_url = token_url
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "SchwabTokenService":
        return cls()

    def refresh_from_env(self) -> SchwabTokenPair:
        return self.refresh(
            client_id=_required_env("SCHWAB_CLIENT_ID"),
            client_secret=_required_env("SCHWAB_CLIENT_SECRET"),
            refresh_token=_required_env("SCHWAB_REFRESH_TOKEN"),
        )

    def refresh(self, *, client_id: str, client_secret: str, refresh_token: str) -> SchwabTokenPair:
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        response = requests.post(
            self.token_url,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Schwab token refresh failed with HTTP {response.status_code}")
        payload: dict[str, Any] = response.json()
        access_token = str(payload.get("access_token") or "")
        new_refresh_token = str(payload.get("refresh_token") or "")
        if not access_token or not new_refresh_token:
            raise RuntimeError("Schwab token refresh response did not include both tokens")
        expires_in = payload.get("expires_in")
        return SchwabTokenPair(
            access_token=access_token,
            new_refresh_token=new_refresh_token,
            expires_in=int(expires_in) if expires_in is not None else None,
        )


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
