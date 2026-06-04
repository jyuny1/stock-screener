# Static Cloudflare Deployment

This project can publish the read-only static snapshot with:

- **GitHub Actions**: builds the market data bundle and frontend.
- **Cloudflare R2**: stores `static-data/*` JSON payloads.
- **Cloudflare Pages**: serves the React/Vite frontend.

The frontend is built in static mode and fetches data from `STATIC_DATA_BASE_URL`.

## 1. Create Cloudflare resources

### R2 bucket

Create an R2 bucket, for example:

```text
stock-screener-static-data
```

The workflow uploads objects under this prefix:

```text
static-data/*
```

### R2 public URL

Expose the bucket with either:

- a custom domain, recommended, for example `https://stock-data.example.com`; or
- the R2 public development URL, for example `https://pub-xxxx.r2.dev`.

The GitHub variable must include the `static-data` prefix:

```text
STATIC_DATA_BASE_URL=https://stock-data.example.com/static-data
```

or:

```text
STATIC_DATA_BASE_URL=https://pub-xxxx.r2.dev/static-data
```

### R2 CORS

If the R2 data origin differs from the Pages frontend origin, configure CORS for the bucket:

```json
[
  {
    "AllowedOrigins": [
      "https://<your-pages-project>.pages.dev",
      "https://<your-custom-frontend-domain>"
    ],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 86400
  }
]
```

### Cloudflare Pages project

Create a Pages project name, for example:

```text
stock-screener
```

The GitHub Actions workflow deploys `frontend/dist` directly via `cloudflare/pages-action`.

## 2. Create Cloudflare credentials

### Cloudflare API token for Pages

Create an API token with permissions sufficient to deploy Pages, typically:

```text
Account - Cloudflare Pages - Edit
```

If Cloudflare's UI labels differ, choose the Pages deploy/edit permission for the target account.

### R2 S3 API credentials

Create R2 S3-compatible access credentials from Cloudflare R2 settings.

These credentials are used by `aws s3 sync`, but they are **Cloudflare R2 keys**, not AWS keys.

## 3. Configure GitHub repository secrets and variables

### GitHub Secrets

Add these in **Settings → Secrets and variables → Actions → Secrets**:

```text
CLOUDFLARE_API_TOKEN=<Cloudflare Pages deploy token>
CLOUDFLARE_ACCOUNT_ID=<Cloudflare account ID>
R2_ACCESS_KEY_ID=<R2 S3 access key ID>
R2_SECRET_ACCESS_KEY=<R2 S3 secret access key>
```

### GitHub Variables

Add these in **Settings → Secrets and variables → Actions → Variables**:

```text
CLOUDFLARE_PAGES_PROJECT=stock-screener
R2_BUCKET=stock-screener-static-data
STATIC_DATA_BASE_URL=https://stock-data.example.com/static-data
```

## 4. Deployment behavior

The workflow is `.github/workflows/static-site.yml`.

On the repository default branch it will:

1. build per-market static artifacts;
2. combine them into `frontend/public/static-data`;
3. build the Vite frontend with:

   ```text
   VITE_STATIC_SITE=true
   VITE_BASE_PATH=/
   VITE_STATIC_DATA_BASE_URL=${STATIC_DATA_BASE_URL}
   ```

4. upload `frontend/public/static-data/*` to R2 under `static-data/*`;
5. remove `frontend/dist/static-data` so Cloudflare Pages only hosts the app shell;
6. deploy `frontend/dist` to Cloudflare Pages.

Non-default branch workflow runs can still build and validate artifacts, but they do not upload to production R2 or deploy Pages.

## 5. Smoke checks after deployment

Open:

```text
https://<your-pages-domain>/
```

Then check browser devtools Network:

- frontend assets load from Cloudflare Pages;
- data JSON loads from `STATIC_DATA_BASE_URL`, for example:

```text
https://stock-data.example.com/static-data/manifest.json
```

Expected static routes use hash routing:

```text
/#/
/#/scan
/#/breadth
/#/groups
```

## 6. Common failures

### `Missing required Cloudflare deployment setting`

A required GitHub secret or variable is missing. Re-check the names above.

### CORS error when loading JSON

Add the Cloudflare Pages domain and any custom frontend domain to the R2 bucket CORS `AllowedOrigins`.

### Frontend loads but data 404s

Confirm:

- `STATIC_DATA_BASE_URL` ends with `/static-data`;
- R2 objects were uploaded under `static-data/manifest.json`;
- the R2 public domain or custom domain is enabled.
