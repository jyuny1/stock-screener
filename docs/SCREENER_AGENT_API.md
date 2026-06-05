# Screener Agent JSON API Contract

## 結論

Screener Agent API 是一個 **agent-only、artifact-native、read-only JSON API**。它只讀取既有 R2 `static-data` 與 `pipeline-run-manifest`，不連 Postgres、不呼叫 provider、不啟動 scanner。

推薦實作：Cloudflare Worker + Bearer token。

```text
Agent
  → HTTPS JSON API
  → Authorization: Bearer <SCREENER_AGENT_API_TOKEN>
  → Cloudflare Worker
  → R2 static-data / pipeline manifest
  → JSON response
```

---

## Non-goals

這個 API 不做：

- 不提供瀏覽器登入流程。
- 不支援公開匿名存取。
- 不連接舊 FastAPI / Postgres。
- 不觸發 GitHub Actions workflows。
- 不抓 Schwab/Yahoo/任何外部 provider。
- 不寫入 R2 或修改 artifacts。
- 不替代 Cloudflare Pages 前端。

---

## Authentication

### Required header

所有 `/api/screener/*` endpoint 都必須帶：

```http
Authorization: Bearer <SCREENER_AGENT_API_TOKEN>
```

### Token storage

Token 必須以 Cloudflare Worker secret 保存：

```bash
wrangler secret put SCREENER_AGENT_API_TOKEN
```

### Validation requirements

Worker 實作必須：

- 使用 constant-time comparison 比對 token。
- Token 不得出現在 git、logs、response body。
- 未帶 token 或 token 錯誤都回同樣錯誤格式。
- 不啟用 browser CORS，除非未來明確要給瀏覽器使用。

### Auth error

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
```

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Unauthorized"
  }
}
```

---

## Base URL

Production 建議：

```text
https://screener-api.<domain>/api/screener
```

Workers preview / development 可用：

```text
https://<worker>.<account>.workers.dev/api/screener
```

---

## Data sources

API 只讀：

```text
R2 static-data/
  manifest.json
  markets/us/scan/manifest.json
  markets/us/scan/chunks/chunk-0001.json
  markets/us/home.json
  markets/us/breadth.json
  markets/us/groups.json

GitHub release / R2 mirrored metadata, optional:
  pipeline-run-manifest
```

第一版可以只讀 R2 `static-data`；若 `pipeline-run-manifest` 尚未 mirror 到 R2，`/pipeline/latest` 可先回 static manifest 中可得的 metadata，或回 `not_available`。

---

## Response conventions

### Success envelope

單筆或集合 response 使用：

```json
{
  "schema_version": "screener-agent-api-v1",
  "data": {},
  "meta": {}
}
```

### Error envelope

```json
{
  "error": {
    "code": "invalid_request",
    "message": "limit must be between 1 and 500",
    "details": {
      "field": "limit"
    }
  }
}
```

### Common metadata

每個成功 response 建議包含：

```json
{
  "meta": {
    "market": "US",
    "rows_total": 5619,
    "generated_at": "2026-06-05T10:08:30Z",
    "static_manifest_path": "static-data/manifest.json",
    "scan_manifest_path": "static-data/markets/us/scan/manifest.json",
    "pipeline_manifest_asset": "pipeline-run-daily-20260605-...json"
  }
}
```

若 metadata 不可得，欄位可為 `null`，但 key 應保留。

---

## Pagination, sorting, and null handling

### Pagination

Use offset pagination：

```text
limit: 1..500, default 100
offset: >= 0, default 0
```

### Sorting

```text
sort: field name, default composite_score
order: asc | desc, default desc
```

### Null sorting rule

所有排序都必須遵守：

```text
null / missing values always sort last
```

不論 `asc` 或 `desc`，空值都不能排到最前面。

---

## Row field contract

API row 直接來自 static scan row，至少應支援以下欄位。

### Core fields

| Field | Type | Description |
|---|---:|---|
| `symbol` | string | Symbol，例如 `NVDA` |
| `company_name` | string | 公司/ETF 名稱 |
| `market` | string | 固定 `US` |
| `exchange` | string/null | 交易所 |
| `security_type` | string/null | `ETF` / stock 類型等 |
| `is_etf` | boolean | 是否 ETF |

### Sell-put screening fields

| Field | Type | Description |
|---|---:|---|
| `current_price` | number/null | 現價 |
| `volume` | number/null | 最新交易日成交股數 |
| `adv_usd` | number/null | 最新交易日成交額，美金估算 |
| `price_change_1d` | number/null | 日漲跌幅 % |
| `price_sparkline_data` | number[] | 近 30 筆價格序列 |
| `price_trend` | number | `-1 / 0 / 1` |
| `rs_sparkline_data` | number[] | 近 30 筆 RS 序列 |
| `rs_trend` | number | `-1 / 0 / 1` |
| `rs_rating` | number/null | RS rating |
| `adr_percent` | number/null | Average Daily Range % |
| `ma_alignment` | boolean/null | 均線排列是否較佳 |
| `market_cap` | number/null | 股票市值；ETF 可視為 AUM fallback |
| `market_cap_usd` | number/null | USD 市值/AUM |
| `gics_sector` | string/null | 板塊 |
| `ibd_industry_group` | string/null | 產業/群組 |

### Additional fields may be present

API 不應刪除 unknown fields，除非 endpoint 明確提供 `fields=` projection。

---

## Endpoints

## 1. Get API health

```http
GET /api/screener/health
```

### Response

```json
{
  "schema_version": "screener-agent-api-v1",
  "data": {
    "status": "ok",
    "source": "r2-static-data"
  },
  "meta": {
    "market": "US"
  }
}
```

### Notes

- Requires auth.
- Should only verify Worker can read required R2 manifest files.
- Should not scan all chunks.

---

## 2. Get manifest

```http
GET /api/screener/manifest
```

### Response

```json
{
  "schema_version": "screener-agent-api-v1",
  "data": {
    "static_site_schema_version": "static-site-v2",
    "scan_schema_version": "static-scan-v1",
    "market": "US",
    "rows_total": 5619,
    "chunks": [
      {
        "path": "markets/us/scan/chunks/chunk-0001.json",
        "count": 1000
      }
    ],
    "default_sort": {
      "field": "composite_score",
      "order": "desc"
    },
    "available_endpoints": [
      "/api/screener/rows",
      "/api/screener/symbol/{symbol}",
      "/api/screener/top-candidates"
    ]
  },
  "meta": {
    "market": "US",
    "rows_total": 5619,
    "generated_at": "2026-06-05T10:08:30Z",
    "pipeline_manifest_asset": "pipeline-run-daily-20260605-...json"
  }
}
```

---

## 3. List rows

```http
GET /api/screener/rows
```

### Query parameters

| Param | Type | Default | Description |
|---|---:|---:|---|
| `limit` | integer | `100` | Max `500` |
| `offset` | integer | `0` | Zero-based offset |
| `sort` | string | `composite_score` | Sort field |
| `order` | string | `desc` | `asc` / `desc` |
| `symbol` | string | | Symbol substring search |
| `sector` | string | | Exact `gics_sector` match |
| `industry` | string | | Exact `ibd_industry_group` match |
| `is_etf` | boolean | | `true` / `false` |
| `min_price` | number | | `current_price >= min_price` |
| `max_price` | number | | `current_price <= max_price` |
| `min_volume` | number | | `volume >= min_volume` |
| `min_adv_usd` | number | | `adv_usd >= min_adv_usd` |
| `min_rs` | number | | `rs_rating >= min_rs` |
| `min_adr` | number | | `adr_percent >= min_adr` |
| `max_adr` | number | | `adr_percent <= max_adr` |
| `ma_alignment` | boolean | | match `ma_alignment` |
| `fields` | comma string | | Optional field projection |

### Response

```json
{
  "schema_version": "screener-agent-api-v1",
  "data": {
    "rows": [
      {
        "symbol": "NVDA",
        "company_name": "NVIDIA Corporation",
        "market": "US",
        "is_etf": false,
        "current_price": 214.75,
        "volume": 160900000,
        "adv_usd": 34500000000,
        "price_change_1d": -3.6,
        "rs_rating": 80,
        "adr_percent": 3.4,
        "ma_alignment": true,
        "market_cap_usd": 5200000000000,
        "gics_sector": "Technology",
        "ibd_industry_group": "Semiconductor"
      }
    ],
    "pagination": {
      "limit": 100,
      "offset": 0,
      "returned": 1,
      "total_filtered": 1234,
      "has_more": true
    },
    "sort": {
      "field": "volume",
      "order": "desc",
      "nulls": "last"
    }
  },
  "meta": {
    "market": "US",
    "rows_total": 5619,
    "generated_at": "2026-06-05T10:08:30Z"
  }
}
```

---

## 4. Get one symbol

```http
GET /api/screener/symbol/{symbol}
```

### Example

```http
GET /api/screener/symbol/NVDA
```

### Response

```json
{
  "schema_version": "screener-agent-api-v1",
  "data": {
    "row": {
      "symbol": "NVDA",
      "company_name": "NVIDIA Corporation",
      "market": "US",
      "current_price": 214.75,
      "volume": 160900000,
      "adv_usd": 34500000000,
      "rs_rating": 80,
      "adr_percent": 3.4,
      "gics_sector": "Technology",
      "ibd_industry_group": "Semiconductor"
    }
  },
  "meta": {
    "market": "US"
  }
}
```

### Not found

```http
HTTP/1.1 404 Not Found
```

```json
{
  "error": {
    "code": "symbol_not_found",
    "message": "Symbol not found: NVDA"
  }
}
```

---

## 5. Get sell-put top candidates

```http
GET /api/screener/top-candidates?preset=sell-put
```

### Query parameters

| Param | Type | Default | Description |
|---|---:|---:|---|
| `preset` | string | `sell-put` | First version only supports `sell-put` |
| `limit` | integer | `50` | Max `200` |
| `offset` | integer | `0` | Zero-based offset |
| `include_etf` | boolean | `true` | Include ETFs |
| `min_price` | number | `5` | Initial liquidity sanity default |
| `min_volume` | number | `1000000` | Initial liquidity sanity default |
| `min_adv_usd` | number | `50000000` | Initial liquidity sanity default |
| `min_rs` | number | | Optional |
| `max_adr` | number | | Optional |
| `sector` | string | | Optional sector filter |
| `industry` | string | | Optional industry filter |

### Sell-put default logic

第一版 `sell-put` preset 應以「資料完整 + 流動性 + 趨勢上下文」為主，不加入 option Greeks，因為 Greeks 屬於未來 option-chain API。

Required non-null fields：

```text
current_price
volume
adv_usd
price_sparkline_data
rs_sparkline_data
rs_rating
adr_percent
gics_sector
ibd_industry_group
```

Default filters：

```text
current_price >= 5
volume >= 1,000,000
adv_usd >= 50,000,000
```

Default sort：

```text
sort = adv_usd desc, nulls last
secondary = rs_rating desc
tertiary = symbol asc
```

### Response

Same shape as `/rows`，但 `data.preset` 應標示 preset logic。

```json
{
  "schema_version": "screener-agent-api-v1",
  "data": {
    "preset": {
      "id": "sell-put",
      "description": "Initial stock/ETF screen for sell-put research; option-chain metrics are intentionally excluded."
    },
    "rows": [],
    "pagination": {
      "limit": 50,
      "offset": 0,
      "returned": 50,
      "total_filtered": 842,
      "has_more": true
    },
    "sort": {
      "field": "adv_usd",
      "order": "desc",
      "nulls": "last"
    }
  },
  "meta": {
    "market": "US"
  }
}
```

---

## 6. Get latest pipeline metadata

```http
GET /api/screener/pipeline/latest
```

### Response

```json
{
  "schema_version": "screener-agent-api-v1",
  "data": {
    "pipeline_manifest": {
      "schema_version": "pipeline-run-manifest-v1",
      "pipeline_type": "daily",
      "workflow_run_id": 27008771095,
      "generated_at": "2026-06-05T10:09:30Z",
      "component_runs": {
        "daily_price": "...",
        "scan_metrics": "...",
        "group_rank": "...",
        "static_site": "..."
      },
      "artifacts": {
        "foundation_update": {
          "release": "foundation-update-data",
          "bundle_asset_name": "foundation-update-us-20260605.json.gz",
          "sha256": "..."
        }
      }
    }
  },
  "meta": {
    "market": "US"
  }
}
```

If unavailable：

```json
{
  "schema_version": "screener-agent-api-v1",
  "data": {
    "pipeline_manifest": null,
    "status": "not_available"
  },
  "meta": {
    "market": "US"
  }
}
```

---

## HTTP status codes

| Status | Code | Meaning |
|---:|---|---|
| 200 | | Success |
| 400 | `invalid_request` | Bad query parameter |
| 401 | `unauthorized` | Missing or invalid bearer token |
| 404 | `symbol_not_found` / `not_found` | Resource not found |
| 405 | `method_not_allowed` | Unsupported HTTP method |
| 413 | `response_too_large` | Requested field/page too large |
| 429 | `rate_limited` | Too many requests |
| 500 | `internal_error` | Unexpected Worker error |
| 503 | `artifact_unavailable` | Required R2 artifact missing or unreadable |

---

## Limits

第一版建議限制：

```text
limit max: 500 for /rows
limit max: 200 for /top-candidates
symbol length max: 32
fields count max: 80
request body: none; GET only
methods: GET, HEAD optional
```

Rate limit 可先用 Cloudflare WAF / rate limiting rules；Worker 內不必第一版自建 stateful limiter。

---

## Caching

Recommended response headers：

```http
Cache-Control: private, max-age=60
Content-Type: application/json; charset=utf-8
```

Notes：

- API 有 Bearer token，因此不使用 public cache。
- Worker 可在記憶體快取 manifest/chunks，但必須允許短 TTL refresh。
- R2 static artifacts 本身是 immutable-ish chunks + latest manifests；API response 可短暫 cache。

---

## Agent usage examples

### Get top sell-put candidates

```bash
curl "https://screener-api.example.com/api/screener/top-candidates?preset=sell-put&limit=50" \
  -H "Authorization: Bearer $SCREENER_AGENT_API_TOKEN"
```

### Get high-liquidity rows

```bash
curl "https://screener-api.example.com/api/screener/rows?min_adv_usd=100000000&sort=adv_usd&order=desc&limit=100" \
  -H "Authorization: Bearer $SCREENER_AGENT_API_TOKEN"
```

### Get one symbol

```bash
curl "https://screener-api.example.com/api/screener/symbol/NVDA" \
  -H "Authorization: Bearer $SCREENER_AGENT_API_TOKEN"
```

---

## Implementation notes for Worker

### Required bindings

```toml
[[r2_buckets]]
binding = "STATIC_DATA_BUCKET"
bucket_name = "<R2_BUCKET>"
```

Required secret：

```text
SCREENER_AGENT_API_TOKEN
```

Optional env：

```text
STATIC_DATA_PREFIX=static-data
MAX_ROWS_LIMIT=500
MAX_TOP_CANDIDATES_LIMIT=200
```

### R2 paths

Worker should normalize paths under configured prefix：

```text
${STATIC_DATA_PREFIX}/manifest.json
${STATIC_DATA_PREFIX}/markets/us/scan/manifest.json
${STATIC_DATA_PREFIX}/markets/us/scan/chunks/chunk-0001.json
```

### Chunk strategy

First version can load all scan chunks per request because current data is about 5,619 rows and 6 chunks. Still implement a max limit and simple in-memory cache to avoid repeated R2 reads.

### Field projection

If `fields` is supplied，always preserve `symbol` unless explicitly impossible；agents need symbol identity.

---

## Future extension: option chain API

Do not put option Greeks into this initial screener API. Future API should be separate：

```text
GET /api/options/chains/{symbol}
GET /api/options/sell-put-candidates/{symbol}
```

Future fields：

```text
delta
theta
IV / IV rank
open interest
option volume
bid/ask spread
premium yield
DTE
distance to strike/support
earnings risk
```

This keeps initial stock/ETF screening separate from strike-level option evaluation.
