# Option Screener JSON API Contract

## 結論

Option Screener API 是一個 **agent-only、artifact-native、read-only JSON API**。第一版只提供「目前 Scan result table」的 JSON 讀取能力：

```text
GET /api/v1/rows
```

預設行為：

```text
回傳目前 US Scan table 中 volume 倒排前 100 筆
```

Agent 可以指定：

- 回傳筆數 `limit`
- 分頁 `offset`
- 排序欄位 `sort` / `order`
- 目前 result table 所有欄位的 filter 範圍

API 不連 Postgres、不呼叫 provider、不啟動 scanner，只讀 R2 static-data。

---

## Scope

### In scope

- 讀取目前 static scan table 結果。
- 預設回傳 `volume desc limit=100`。
- 允許 agent 指定 `limit`，上限 500。
- 允許 filter/sort 目前 result table 的所有資料欄位。
- 使用 Bearer token 驗證，只給 agent 使用。

### Out of scope

- 不提供瀏覽器登入流程。
- 不提供公開匿名存取。
- 不提供寫入、watchlist、收藏、任務觸發。
- 不提供 symbol detail page API。
- 不提供 option chain / Greeks。
- 不觸發 GitHub Actions workflows。
- 不讀舊 FastAPI/Postgres scan tables。

---

## Architecture

```text
Agent
  → HTTPS JSON API
  → Authorization: Bearer <OPTION_SCREENER_API_TOKEN>
  → Cloudflare Worker
  → R2 static-data/markets/us/scan/*
  → JSON response
```

Data source：

```text
R2 static-data/
  manifest.json
  markets/us/scan/manifest.json
  markets/us/scan/chunks/chunk-0001.json
  markets/us/scan/chunks/chunk-0002.json
  ...
```

Current expected row count：

```text
5619 rows
```

---

## Authentication

所有 endpoint 都必須帶：

```http
Authorization: Bearer <OPTION_SCREENER_API_TOKEN>
```

Token 以 Cloudflare Worker secret 保存：

```bash
wrangler secret put OPTION_SCREENER_API_TOKEN
```

Implementation requirements：

- 使用 constant-time comparison。
- Token 不得出現在 git、logs、response body。
- CORS 預設不開，因為不是給瀏覽器使用。
- 未授權固定回 `401 unauthorized`。

Auth error：

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

```text
https://ss.ljy.app/api/v1
```

---

## Endpoints

## 1. Health

```http
GET /api/v1/health
```

用途：確認 Worker 可讀取 R2 manifest。

Response：

```json
{
  "schema_version": "option-screener-api-v1",
  "data": {
    "status": "ok",
    "source": "r2-static-data"
  },
  "meta": {
    "market": "US"
  }
}
```

---

## 2. Manifest

```http
GET /api/v1/manifest
```

用途：讓 agent 知道目前 scan table 的資料版本、總筆數、可用欄位、預設排序與資料更新時間。

Response：

```json
{
  "schema_version": "option-screener-api-v1",
  "data": {
    "market": "US",
    "rows_total": 5619,
    "default_query": {
      "sort": "volume",
      "order": "desc",
      "limit": 100,
      "nulls": "last"
    },
    "columns": [
      "symbol",
      "current_price",
      "volume",
      "adv_usd",
      "price_change_1d",
      "rs_trend",
      "rs_rating",
      "adr_percent",
      "ma_alignment",
      "market_cap",
      "gics_sector",
      "ibd_industry_group"
    ],
    "filterable_fields": [
      "symbol",
      "current_price",
      "volume",
      "adv_usd",
      "price_change_1d",
      "rs_trend",
      "rs_rating",
      "adr_percent",
      "ma_alignment",
      "market_cap",
      "gics_sector",
      "ibd_industry_group"
    ],
    "sortable_fields": [
      "symbol",
      "current_price",
      "volume",
      "adv_usd",
      "price_change_1d",
      "rs_trend",
      "rs_rating",
      "adr_percent",
      "market_cap",
      "gics_sector",
      "ibd_industry_group"
    ]
  },
  "meta": {
    "market": "US",
    "rows_total": 5619,
    "generated_at": "2026-06-05T10:08:31Z",
    "as_of_date": "2026-06-05",
    "data_updated_at": "2026-06-05T10:08:31Z",
    "source": {
      "type": "r2-static-data",
      "static_manifest_path": "static-data/manifest.json",
      "scan_manifest_path": "static-data/markets/us/scan/manifest.json"
    }
  }
}
```

---

## 3. Rows

```http
GET /api/v1/rows
```

這是第一版唯一主要資料 endpoint。

### Default behavior

不帶任何 query 時等同：

```http
GET /api/v1/rows?sort=volume&order=desc&limit=100&offset=0
```

也就是：

```text
目前 Scan table 中 VOL 倒排前 100 筆
```

### Query parameters

#### Pagination / sort

| Param | Type | Default | Limit | Description |
|---|---:|---:|---:|---|
| `limit` | integer | `100` | `1..500` | 回傳筆數 |
| `offset` | integer | `0` | `>=0` | 分頁 offset |
| `sort` | string | `volume` | sortable fields | 排序欄位 |
| `order` | string | `desc` | `asc` / `desc` | 排序方向 |
| `fields` | comma string | all table fields | max 32 fields | 欄位投影；`symbol` 永遠保留 |

#### Filter convention

所有 filter 使用 query parameters。

數值欄位支援：

```text
min_<field>
max_<field>
```

布林欄位支援：

```text
<field>=true|false
```

文字欄位支援：

```text
<field>=exact value
```

Symbol 另支援 substring search：

```text
symbol=nv
```

### Current table fields

目前 result table 的資料欄位如下；`chart` 是 UI 操作欄，不是 API 欄位。

| Field | Type | Filter | Sort | 中文 |
|---|---:|---|---|---|
| `symbol` | string | `symbol=NVDA` substring | yes | 代號 |
| `current_price` | number/null | `min_current_price`, `max_current_price` | yes | 現價 |
| `volume` | number/null | `min_volume`, `max_volume` | yes | 成交量 |
| `adv_usd` | number/null | `min_adv_usd`, `max_adv_usd` | yes | 日均成交額 |
| `price_change_1d` | number/null | `min_price_change_1d`, `max_price_change_1d` | yes | 價格趨勢 / 日漲跌 |
| `rs_trend` | number | `min_rs_trend`, `max_rs_trend` | yes | RS 趨勢 |
| `rs_rating` | number/null | `min_rs_rating`, `max_rs_rating` | yes | RS |
| `adr_percent` | number/null | `min_adr_percent`, `max_adr_percent` | yes | 平均日振幅 |
| `ma_alignment` | boolean/null | `ma_alignment=true|false` | no | 均線排列 |
| `market_cap` | number/null | `min_market_cap`, `max_market_cap` | yes | 市值 / AUM |
| `gics_sector` | string/null | `gics_sector=Technology` | yes | 板塊 |
| `ibd_industry_group` | string/null | `ibd_industry_group=Semiconductor` | yes | 產業 |

### Additional implementation filters

雖然目前 table 只顯示上述 12 個資料欄位，API 實作時可以額外支援這些常用 alias，方便 agent：

| Alias | Maps to |
|---|---|
| `min_price` / `max_price` | `current_price` |
| `min_rs` / `max_rs` | `rs_rating` |
| `min_adr` / `max_adr` | `adr_percent` |
| `sector` | `gics_sector` |
| `industry` | `ibd_industry_group` |

Alias 是方便用法，不取代 canonical field filters。

### Null sorting rule

排序必須固定：

```text
null / missing values always sort last
```

不論 `asc` 或 `desc`，空值都不能排到最前面。

### Example requests

Default top 100 by VOL：

```bash
curl "https://ss.ljy.app/api/v1/rows" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

Top 200 by VOL：

```bash
curl "https://ss.ljy.app/api/v1/rows?limit=200" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

Filter by liquidity and price：

```bash
curl "https://ss.ljy.app/api/v1/rows?min_current_price=5&min_volume=1000000&min_adv_usd=50000000&limit=100" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

Filter by sector and RS：

```bash
curl "https://ss.ljy.app/api/v1/rows?gics_sector=Technology&min_rs_rating=70&sort=rs_rating&order=desc" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

Only selected fields：

```bash
curl "https://ss.ljy.app/api/v1/rows?fields=symbol,current_price,volume,adv_usd,rs_rating,gics_sector,ibd_industry_group" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

### Response

```json
{
  "schema_version": "option-screener-api-v1",
  "data": {
    "rows": [
      {
        "symbol": "NVDA",
        "current_price": 214.75,
        "volume": 160900000,
        "adv_usd": 34500000000,
        "price_change_1d": -3.6,
        "rs_trend": 1,
        "rs_rating": 80,
        "adr_percent": 3.4,
        "ma_alignment": true,
        "market_cap": 5200000000000,
        "gics_sector": "Technology",
        "ibd_industry_group": "Semiconductor"
      }
    ],
    "pagination": {
      "limit": 100,
      "offset": 0,
      "returned": 100,
      "total_filtered": 5619,
      "has_more": true
    },
    "sort": {
      "field": "volume",
      "order": "desc",
      "nulls": "last"
    },
    "filters": {
      "min_current_price": null,
      "min_volume": null,
      "gics_sector": null
    }
  },
  "meta": {
    "market": "US",
    "rows_total": 5619,
    "generated_at": "2026-06-05T10:08:31Z",
    "as_of_date": "2026-06-05",
    "data_updated_at": "2026-06-05T10:08:31Z",
    "source": {
      "type": "r2-static-data",
      "static_manifest_path": "static-data/manifest.json",
      "scan_manifest_path": "static-data/markets/us/scan/manifest.json"
    }
  }
}
```

---

## Response conventions

### Success envelope

```json
{
  "schema_version": "option-screener-api-v1",
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

---

## HTTP status codes

| Status | Code | Meaning |
|---:|---|---|
| 200 | | Success |
| 400 | `invalid_request` | Bad query parameter / unsupported field |
| 401 | `unauthorized` | Missing or invalid bearer token |
| 405 | `method_not_allowed` | Unsupported HTTP method |
| 413 | `response_too_large` | Requested limit/fields too large |
| 429 | `rate_limited` | Too many requests |
| 500 | `internal_error` | Unexpected Worker error |
| 503 | `artifact_unavailable` | Required R2 artifact missing or unreadable |

---

## Limits

第一版限制：

```text
limit default: 100
limit max: 500
offset min: 0
fields max: 32
methods: GET only
request body: none
```

目前 scan rows 約 5619 筆，Worker 可以在短 TTL 記憶體 cache 中載入所有 chunks 後 filter/sort/paginate。

---

## Caching

Recommended headers：

```http
Cache-Control: private, max-age=60
Content-Type: application/json; charset=utf-8
```

API 有 Bearer token，因此不使用 public cache。

---

## Data freshness fields

所有成功 response 的 `meta` 都應包含：

| Field | Description |
|---|---|
| `generated_at` | Static scan manifest 產生時間，優先取 `markets/us/scan/manifest.json.generated_at` |
| `as_of_date` | 資料 as-of date，通常是 foundation/daily artifact 對應日期 |
| `data_updated_at` | Agent 判斷資料新鮮度用；第一版等同 `generated_at` |

Agent 應優先看：

```text
meta.data_updated_at
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
OPTION_SCREENER_API_TOKEN
```

Optional env：

```text
STATIC_DATA_PREFIX=static-data
MAX_ROWS_LIMIT=500
DEFAULT_ROWS_LIMIT=100
DEFAULT_SORT=volume
DEFAULT_ORDER=desc
```

### R2 paths

```text
${STATIC_DATA_PREFIX}/manifest.json
${STATIC_DATA_PREFIX}/markets/us/scan/manifest.json
${STATIC_DATA_PREFIX}/markets/us/scan/chunks/chunk-0001.json
```

### Field projection

If `fields` is supplied：

- `symbol` must always be included。
- Unknown fields should return `400 invalid_request`。
- Projection applies after filtering/sorting。

### Validation

Worker must reject：

- Unsupported sort field。
- Unsupported filter field。
- Non-numeric values for numeric filters。
- Invalid booleans for boolean filters。
- `limit > 500`。
- Any non-GET method。

---

## Future extension boundaries

不要在第一版加入：

```text
/api/v1/symbol/{symbol}
/api/v1/top-candidates
/api/v1/pipeline/latest
/api/options/*
```

如果未來要做 option chain，應另開：

```text
GET /api/options/chains/{symbol}
GET /api/options/sell-put-candidates/{symbol}
```

避免把 stock/ETF initial screening 與 strike-level option evaluation 混在一起。
