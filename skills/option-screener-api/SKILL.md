---
name: option-screener-api
description: Call the protected Stock Option Screener JSON API to retrieve the current US Scan table rows. Use when an agent needs machine-readable screener data instead of reading the frontend UI. The API is read-only, artifact-native, token-protected, and defaults to the top 100 rows sorted by volume descending.
---

# Option Screener API Skill

## Purpose

Use this skill when an agent needs JSON data from the current Stock Screener Scan table.

The API is:

- Agent-only
- Read-only
- Bearer-token protected
- Artifact-native from R2 `static-data`
- Not connected to Postgres
- Not a scanner trigger
- Not an option-chain API

Base URL:

```text
https://ss.ljy.app/api/v1
```

Required auth header:

```http
Authorization: Bearer <OPTION_SCREENER_API_TOKEN>
```

Never hardcode the token. Read it from an environment variable or a local secret store.

---

## Endpoints

### Health

```http
GET https://ss.ljy.app/api/v1/health
```

Use this to verify the API and R2 static-data are readable.

Example:

```bash
curl -sS "https://ss.ljy.app/api/v1/health" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

### Manifest

```http
GET https://ss.ljy.app/api/v1/manifest
```

Use this before data queries when you need to confirm:

- total row count
- default query
- available columns
- filterable fields
- sortable fields
- data freshness fields

Important freshness fields:

```text
meta.data_updated_at
meta.generated_at
meta.as_of_date
```

Agent rule: always inspect `meta.data_updated_at` when freshness matters.

Example:

```bash
curl -sS "https://ss.ljy.app/api/v1/manifest" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

### Rows

```http
GET https://ss.ljy.app/api/v1/rows
```

Default behavior:

```text
sort=volume
order=desc
limit=100
offset=0
```

That means the API returns the top 100 current Scan table rows by `volume` descending.

Example:

```bash
curl -sS "https://ss.ljy.app/api/v1/rows" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

---

## Query Parameters

### Pagination and sort

| Param | Default | Description |
|---|---:|---|
| `limit` | `100` | Number of rows to return. Max `500`. |
| `offset` | `0` | Zero-based offset. |
| `sort` | `volume` | Sort field. |
| `order` | `desc` | `asc` or `desc`. |
| `fields` | all table fields | Comma-separated field projection. `symbol` is always included. |

Sorting rule:

```text
null / missing values always sort last
```

This applies to both ascending and descending sorts.

---

## Current Table Fields

The API supports filtering over the current result table fields.

| Field | Type | Filter | Sort | Meaning |
|---|---:|---|---|---|
| `symbol` | string | `symbol=NVDA` substring | yes | Symbol |
| `current_price` | number/null | `min_current_price`, `max_current_price` | yes | Current price |
| `volume` | number/null | `min_volume`, `max_volume` | yes | Share volume |
| `adv_usd` | number/null | `min_adv_usd`, `max_adv_usd` | yes | Dollar volume estimate |
| `price_change_1d` | number/null | `min_price_change_1d`, `max_price_change_1d` | yes | 1-day price change % |
| `rs_trend` | number | `min_rs_trend`, `max_rs_trend` | yes | RS trend, usually `-1`, `0`, `1` |
| `rs_rating` | number/null | `min_rs_rating`, `max_rs_rating` | yes | Relative strength rating |
| `adr_percent` | number/null | `min_adr_percent`, `max_adr_percent` | yes | Average Daily Range % |
| `ma_alignment` | boolean/null | `ma_alignment=true|false` | no | Moving-average alignment |
| `market_cap` | number/null | `min_market_cap`, `max_market_cap` | yes | Market cap / ETF AUM fallback |
| `gics_sector` | string/null | `gics_sector=Technology` | yes | Sector |
| `ibd_industry_group` | string/null | `ibd_industry_group=Semiconductor` | yes | Industry group |

Convenience aliases:

| Alias | Maps to |
|---|---|
| `min_price` / `max_price` | `current_price` |
| `min_rs` / `max_rs` | `rs_rating` |
| `min_adr` / `max_adr` | `adr_percent` |
| `sector` | `gics_sector` |
| `industry` | `ibd_industry_group` |

---

## Common Calls

### Top 100 by volume, default fields

```bash
curl -sS "https://ss.ljy.app/api/v1/rows" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

### Top 200 by volume

```bash
curl -sS "https://ss.ljy.app/api/v1/rows?limit=200" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

### High-liquidity screen

```bash
curl -sS "https://ss.ljy.app/api/v1/rows?min_current_price=5&min_volume=1000000&min_adv_usd=50000000&limit=100" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

### Technology names sorted by RS

```bash
curl -sS "https://ss.ljy.app/api/v1/rows?gics_sector=Technology&min_rs_rating=70&sort=rs_rating&order=desc" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

### Request only compact fields

```bash
curl -sS "https://ss.ljy.app/api/v1/rows?fields=symbol,current_price,volume,adv_usd,rs_rating,gics_sector,ibd_industry_group" \
  -H "Authorization: Bearer $OPTION_SCREENER_API_TOKEN"
```

---

## Response Shape

Successful responses use:

```json
{
  "schema_version": "option-screener-api-v1",
  "data": {
    "rows": [],
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
    "filters": {}
  },
  "meta": {
    "market": "US",
    "rows_total": 5619,
    "generated_at": "2026-06-05T10:08:22Z",
    "as_of_date": "2026-06-05",
    "data_updated_at": "2026-06-05T10:08:22Z",
    "source": {
      "type": "r2-static-data",
      "static_manifest_path": "static-data/manifest.json",
      "scan_manifest_path": "static-data/markets/us/scan/manifest.json"
    }
  }
}
```

---

## Error Handling

Error responses use:

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

Common status codes:

| Status | Code | Meaning |
|---:|---|---|
| `400` | `invalid_request` | Bad query parameter or unsupported field |
| `401` | `unauthorized` | Missing or invalid bearer token |
| `405` | `method_not_allowed` | Only GET is supported |
| `413` | `response_too_large` | Requested `limit` or fields too large |
| `503` | `artifact_unavailable` | Required R2 artifact missing or unreadable |

---

## Agent Guidelines

1. Use `/manifest` first if you need freshness or schema awareness.
2. Use `/rows` directly for normal data retrieval.
3. Default query is already `volume desc limit=100`.
4. Check `meta.data_updated_at` before making time-sensitive decisions.
5. Do not assume the API has option-chain data.
6. Do not expose the bearer token in logs or final answers.
7. If a query returns too many rows, reduce `limit` or add filters.
