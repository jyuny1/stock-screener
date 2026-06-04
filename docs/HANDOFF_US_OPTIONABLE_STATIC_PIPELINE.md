# Handoff: US Optionable Static Pipeline

## Objective

Optimize the static deployment for the stock-screener project so the deployed static screener defaults to a US optionable universe instead of the full US universe.

Target production site:

```text
https://ss.ljy.app
```

Target static data host:

```text
https://pub-63141bbf046a4c3b97ef34b7176421eb.r2.dev/static-data
```

---

## Current Deployment State

### Cloudflare

Already configured:

```text
Cloudflare Pages project: stock-screener
Cloudflare Pages default domain: https://stock-screener-ajv.pages.dev
Custom domain: https://ss.ljy.app
R2 bucket: stock-screener
R2 public URL: https://pub-63141bbf046a4c3b97ef34b7176421eb.r2.dev
R2 CORS allowed origins:
  - https://stock-screener-ajv.pages.dev
  - https://ss.ljy.app
```

`ss.ljy.app` was verified as active in Cloudflare Pages.

### GitHub repository variables

Already configured:

```text
CLOUDFLARE_PAGES_PROJECT=stock-screener
R2_BUCKET=stock-screener
STATIC_DATA_BASE_URL=https://pub-63141bbf046a4c3b97ef34b7176421eb.r2.dev/static-data
```

### GitHub repository secrets

Already configured:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
```

Cloudflare Pages deployment token should have:

```text
Account → Cloudflare Pages → Edit
```

R2 upload uses S3-compatible R2 credentials, not the Pages token.

---

## Code Changes Already Landed

The following commits have been pushed to `main`:

```text
a026de52 Deploy static site to Cloudflare Pages and R2
088c43f9 Document Wrangler R2 CORS format
895c2f6d Ignore Wrangler local state
4f09afba Allow US-only static site workflow runs
b786ac9a Default static data workflows to US only
```

### Important files changed

```text
.github/workflows/static-site.yml
docs/STATIC_CLOUDFLARE_DEPLOYMENT.md
frontend/src/config/runtimeMode.js
backend/tests/unit/test_static_site_workflow.py
README.md
.gitignore
.github/workflows/weekly-reference-data.yml
```

### Behavior after these changes

`static-site.yml` now:

1. Builds static data through GitHub Actions.
2. Uploads `frontend/public/static-data/*` to Cloudflare R2 under `static-data/*`.
3. Removes `frontend/dist/static-data` from the Pages bundle.
4. Deploys `frontend/dist` to Cloudflare Pages.
5. Defaults `workflow_dispatch.market` to `US`.
6. Defaults scheduled static runs to US only via the matrix expression.

`weekly-reference-data.yml` now:

1. Defaults `workflow_dispatch.market` to `US`.
2. Defaults scheduled weekly reference runs to US only via the matrix expression.
3. Still keeps `market=all` as a manual option.

`frontend/src/config/runtimeMode.js` now supports:

```text
VITE_STATIC_DATA_BASE_URL
```

so static data can be fetched from R2 instead of same-origin Pages.

---

## Current GitHub Actions State

A US-only weekly reference run was triggered:

```text
Workflow: Weekly Reference Data
Run ID: 26943067427
Market: US
Status at last check: in_progress
Job: publish (US)
```

It was spending a long time hydrating the full US universe. The observed log line was:

```text
[hydrate] chunk 1/50 processed 200/9865 (2.0%) live_price=200 cached_only=0 yahoo_hydrated=200 missing_prices=4 missing_yahoo=199 skipped_yahoo_price=0 skipped_yahoo_fields=0
```

Interpretation:

```text
Full US universe size: ~9865 symbols
Chunk size: ~200 symbols
Total chunks: ~50
```

The first Static Site deployment attempt failed before Cloudflare deployment because weekly reference assets did not exist yet:

```text
Workflow: Static Site
Run ID: 26942709951
Conclusion: failure
Failure reason: weekly-reference-data release assets were missing
```

Commands to check state in a new context:

```bash
gh run view 26943067427 --repo jyuny1/stock-screener --json status,conclusion,updatedAt,jobs \
  --jq '{status, conclusion, updatedAt, jobs: [.jobs[] | {name,status,conclusion,startedAt,completedAt}]}'

gh run list --repo jyuny1/stock-screener --workflow weekly-reference-data.yml --limit 5

gh run list --repo jyuny1/stock-screener --workflow static-site.yml --limit 5
```

---

## Strategic Decision

Do **not** deploy the full US universe by default.

Future static default should be:

```text
US_OPTIONABLE
```

That means the static screener should show only US stocks and ETFs with listed options.

Desired high-level pipeline:

```text
NasdaqTrader symbols
→ clean/filter symbol universe
→ Schwab /chains confirms optionability
→ optionable-symbols-latest-us.json artifact
→ weekly/static pipeline hydrates only optionable symbols
→ existing static export flow continues
→ upload static-data to R2
→ deploy frontend to Cloudflare Pages
```

---

## Why Change the Universe Source

The current US weekly/static path is expensive because it hydrates a broad universe of about 9865 symbols.

Current US weekly reference code path:

```text
backend/app/scripts/build_weekly_reference_bundle.py
  _build_us_bundle()
    stock_universe_service.populate_universe(db)  # Finviz
    provider_snapshot_service.create_snapshot_run(... show_finviz_progress=True)
    provider_snapshot_service.hydrate_published_snapshot(...)
```

Finviz issues:

1. Slow for broad universe.
2. Can include stale / weird / low-quality symbols.
3. Does not align directly with option/sell-put use case.
4. Still requires Yahoo hydration for many symbols.

NasdaqTrader official symbol directory is faster and more deterministic:

```text
https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt
```

A local corrected fetch showed:

```text
nasdaqlisted.txt: 5486
otherlisted.txt: 7302
total raw symbols: 12788
```

`otherlisted.txt` includes non-Nasdaq listings such as:

```text
N = NYSE
P = NYSE Arca
A = NYSE American
Z = Cboe BZX
```

Important: raw NasdaqTrader symbols must be filtered; otherwise the raw universe can be larger than Finviz.

---

## Schwab API Findings

From the local notes file:

```text
/Users/jyuny1/Library/Mobile Documents/iCloud~md~obsidian/Documents/thinking/股票期權/schwab_api.md
```

Relevant Schwab Market Data API facts:

### `/quotes`

Supports batching:

```http
GET /marketdata/v1/quotes?symbols=AAPL,BAC,$DJI,$SPX&fields=quote,reference
```

The `symbols` query parameter is comma-separated and can include multiple symbols.

Useful for:

```text
last price
bid / ask
volume
avg10DaysVolume
avg1YearVolume
PE
dividend yield
reference exchange/name
```

### `/pricehistory`

Does **not** support batching.

Docs say:

```text
Get PriceHistory for a single symbol and date ranges.
```

So it is effectively:

```text
1 symbol = 1 request
```

### `/chains`

Does **not** support batching.

Option chain checking is also effectively:

```text
1 symbol = 1 request
```

At 120 calls/minute:

```text
5000 symbols ≈ 42 minutes minimum
9865 symbols ≈ 82 minutes minimum
```

This is acceptable for a biweekly optionability scan, but not ideal for every static deployment.

---

## Schwab Token Issue

Schwab API requires OAuth refresh tokens.

Key point:

```text
refresh_token is rotated when used
```

So this cannot be treated as a static long-lived GitHub Secret without write-back.

Recommended MVP solution:

```text
GitHub Secret + workflow-level concurrency lock + refresh-token write-back
```

Needed secrets:

```text
SCHWAB_CLIENT_ID
SCHWAB_CLIENT_SECRET
SCHWAB_REFRESH_TOKEN
```

Token manager behavior:

```text
1. Read SCHWAB_REFRESH_TOKEN from GitHub Actions secret.
2. POST to Schwab OAuth token endpoint.
3. Receive access_token + new refresh_token.
4. Use access_token only in memory.
5. Immediately write new refresh_token back to GitHub secret.
6. If write-back fails, fail the job.
```

Workflow concurrency is mandatory:

```yaml
concurrency:
  group: schwab-token-refresh
  cancel-in-progress: false
```

Risk:

```text
Two workflows refreshing the same token concurrently can invalidate one another.
```

---

## Desired New Artifacts

### GitHub Release

Create/use release:

```text
optionable-symbols
```

Main asset:

```text
optionable-symbols-latest-us.json
```

Potential dated asset:

```text
optionable-symbols-us-YYYYMMDD.json
```

Suggested JSON schema:

```json
{
  "schema_version": "optionable-symbols-v1",
  "market": "US",
  "universe_mode": "US_OPTIONABLE",
  "as_of": "2026-06-04",
  "source": {
    "symbols": "nasdaqtrader",
    "option_chain_provider": "schwab"
  },
  "stats": {
    "raw_symbols": 12788,
    "filtered_symbols": 5486,
    "checked": 5486,
    "optionable": 3120,
    "not_optionable": 2366,
    "errors": 42
  },
  "symbols": ["AAPL", "MSFT", "NVDA", "SPY", "QQQ"],
  "failures": {
    "XYZ": "empty_chain",
    "ABC": "rate_limited"
  }
}
```

---

## Desired New Workflow

Add:

```text
.github/workflows/optionable-symbols.yml
```

Schedule:

```text
Every 2 weeks on Sunday morning
```

Implementation note: GitHub cron cannot directly express every two weeks reliably in all cases. Options:

1. Run every Sunday and skip if current ISO week is odd/even.
2. Run on a fixed Sunday schedule and check last successful artifact age.

Recommended simple pattern:

```yaml
on:
  schedule:
    - cron: '20 10 * * 0'
  workflow_dispatch:
```

Then in the job:

```bash
# Skip every other week based on ISO week number unless workflow_dispatch.
```

High-level steps:

```text
1. checkout
2. setup Python
3. install backend deps or a smaller requirements file
4. refresh Schwab token and write back new SCHWAB_REFRESH_TOKEN
5. download NasdaqTrader symbol directories
6. clean/filter symbols
7. scan Schwab /chains at <=120 calls/min
8. checkpoint progress for resume safety
9. write optionable-symbols-latest-us.json
10. ensure GitHub release optionable-symbols exists
11. upload latest and dated assets
```

---

## Desired New Scripts / Services

### 1. NasdaqTrader symbol fetcher

Suggested file:

```text
backend/app/services/nasdaqtrader_universe_service.py
```

Responsibilities:

```text
- Download nasdaqlisted.txt
- Download otherlisted.txt
- Parse pipe-delimited data
- Normalize fields
- Map exchange codes to internal exchange/MIC
- Filter out undesirable instruments
```

Recommended filters:

```text
Test Issue != Y
ETF != Y by default, except allowlist liquid ETFs
Security Name excludes:
  - Warrant
  - Right
  - Unit
  - Preferred
  - Depositary
  - Notes
  - Bond
  - Debenture
  - Trust if not ETF allowlisted
```

Potential exchange mapping:

```text
Nasdaq listed → XNAS
otherlisted N → XNYS
otherlisted A → XASE
otherlisted P → ARCA-like venue; decide whether to keep ETFs only
otherlisted Z → Cboe BZX; probably exclude unless explicitly desired
```

### 2. Schwab token manager

Suggested file:

```text
backend/app/services/schwab_token_service.py
```

or standalone script helper if keeping minimal.

Responsibilities:

```text
- Read client id / secret / refresh token from env
- Refresh access token
- Return access token
- Emit new refresh token for caller to write back
- Never log secrets
```

### 3. Schwab optionable scanner

Suggested script:

```text
backend/app/scripts/build_optionable_symbols.py
```

Responsibilities:

```text
- Read NasdaqTrader clean symbols
- Rate-limit Schwab /chains to <=120 calls/min
- Detect optionability:
    bool(putExpDateMap or callExpDateMap)
- Retry transient failures
- Save checkpoints
- Emit final JSON artifact
```

Minimal `/chains` query:

```text
GET /marketdata/v1/chains
  symbol=AAPL
  contractType=ALL
  strikeCount=1
  includeUnderlyingQuote=false
  strategy=SINGLE
```

---

## Static Pipeline Integration Plan

Goal:

```text
Static default universe = US_OPTIONABLE
```

The existing static workflow should consume `optionable-symbols-latest-us.json` and limit active US universe rows to those symbols before running the existing static data flow.

Possible integration options:

### Option A: seed StockUniverse from optionable artifact

Before weekly/static hydration:

```text
Download optionable-symbols-latest-us.json
Upsert only those symbols into StockUniverse
Mark source_name = optionable_symbols_schwab
Set market = US
Set exchange/MIC from NasdaqTrader metadata
Then run existing hydration/export logic
```

Pros:

```text
Works with existing downstream code expecting StockUniverse rows.
```

Cons:

```text
Need to preserve metadata quality and avoid destructive deactivation of non-optionable symbols in full/server mode.
```

### Option B: add universe mode filters

Add env/config:

```text
US_UNIVERSE_MODE=full | optionable
```

When mode is `optionable`, query active universe symbols intersected with optionable artifact.

Pros:

```text
Less destructive.
Full universe remains available.
```

Cons:

```text
More code paths need to honor the mode.
```

Recommended:

```text
Option B for safety.
```

Use:

```text
US_UNIVERSE_MODE=optionable
```

for static workflows, while preserving full/server mode behavior.

---

## Risks and Open Questions

### 1. Metadata loss

NasdaqTrader does not provide sector, industry, or market cap.

Need enrichment strategy:

```text
- Schwab /quotes fields=fundamental,reference for basic quote/fundamental
- existing yfinance hydration for missing fields
- Finviz as optional metadata fallback
- IBD classification pipeline for industry/group data
```

### 2. Raw NasdaqTrader list is too broad

Raw observed total:

```text
12788 symbols
```

Must filter before Schwab `/chains`.

### 3. Schwab token rotation

Must implement safe token refresh/write-back with workflow concurrency.

### 4. `/chains` is single-symbol

Biweekly full scan is acceptable; do not do it during every static deployment.

### 5. Static UI labeling

If the static screener uses only optionable symbols, UI should clearly say:

```text
US Optionable Universe
```

or:

```text
US stocks and ETFs with listed options
```

Do not imply this is the full US market.

### 6. Current long-running weekly run

At handoff time, `26943067427` was still running. Decide whether to:

```text
- let it finish,
- cancel it and rerun partial publish,
- or proceed with US_OPTIONABLE implementation and stop relying on full US hydrate.
```

---

## Commands Useful in New Context

Check repo state:

```bash
git status -sb
git pull --rebase
```

Check current workflows:

```bash
gh run list --repo jyuny1/stock-screener --limit 10

gh run list --repo jyuny1/stock-screener --workflow weekly-reference-data.yml --limit 5

gh run list --repo jyuny1/stock-screener --workflow static-site.yml --limit 5
```

Check the long-running weekly run:

```bash
gh run view 26943067427 --repo jyuny1/stock-screener --json status,conclusion,updatedAt,jobs \
  --jq '{status, conclusion, updatedAt, jobs: [.jobs[] | {name,status,conclusion,startedAt,completedAt}]}'
```

Trigger US static manually:

```bash
gh workflow run static-site.yml --repo jyuny1/stock-screener -f market=US
```

Trigger US weekly manually:

```bash
gh workflow run weekly-reference-data.yml --repo jyuny1/stock-screener \
  -f market=US \
  -f allow_partial_publish=default \
  -f cn_resume_partial_seed=disable
```

Trigger US weekly with partial publish:

```bash
gh workflow run weekly-reference-data.yml --repo jyuny1/stock-screener \
  -f market=US \
  -f max_runtime_minutes=60 \
  -f allow_partial_publish=enable \
  -f cn_resume_partial_seed=disable
```

---

## Recommended Next Development Task

Start a new context and ask:

```text
Read docs/HANDOFF_US_OPTIONABLE_STATIC_PIPELINE.md and implement the US_OPTIONABLE static pipeline.
Begin by designing and implementing:
1. backend/app/services/nasdaqtrader_universe_service.py
2. backend/app/services/schwab_token_service.py
3. backend/app/scripts/build_optionable_symbols.py
4. .github/workflows/optionable-symbols.yml
5. workflow/static integration to use US_UNIVERSE_MODE=optionable by default.
```

Recommended first implementation checkpoint:

```text
Implement NasdaqTrader clean symbol fetcher and tests first, without touching Schwab.
```

Then implement Schwab optionable scanner with a dry-run mode and checkpoint/resume.
