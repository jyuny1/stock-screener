# Schwab Token Broker Worker

Internal Cloudflare Worker that centralizes Schwab OAuth token rotation.
GitHub Actions authenticate with GitHub OIDC JWTs; the Worker only returns short-lived Schwab access tokens and never returns the refresh token.

## Runtime endpoints

- `POST /v1/schwab/access-token` — requires GitHub Actions OIDC JWT with audience `schwab-token-broker`.
- `GET /admin/status` — requires `Authorization: Bearer $ADMIN_BOOTSTRAP_TOKEN`.
- `POST /admin/schwab/refresh-token` — requires `Authorization: Bearer $ADMIN_BOOTSTRAP_TOKEN`; bootstraps or replaces the encrypted Schwab refresh token.
- `GET /health` — health check only.

## Required Worker secrets

Set by `.github/workflows/schwab-token-broker.yml` during deploy:

- `TOKEN_ENCRYPTION_KEY` — 16, 24, or 32 byte AES-GCM key.
- `SCHWAB_CLIENT_ID`
- `SCHWAB_CLIENT_SECRET`
- `ADMIN_BOOTSTRAP_TOKEN`

## Bootstrap refresh token

After deployment, seed the current Schwab refresh token once:

```bash
curl -fsS -X POST "$SCHWAB_TOKEN_BROKER_URL/admin/schwab/refresh-token" \
  -H "Authorization: Bearer $ADMIN_BOOTSTRAP_TOKEN" \
  -H "Content-Type: application/json" \
  --data @- <<'JSON'
{"refresh_token":"paste-current-schwab-refresh-token-here"}
JSON
```

Do not log or commit refresh tokens. After bootstrapping, GitHub Actions should use only `/v1/schwab/access-token`.
