---
type: architecture-plan
topic: option-ai-worker
date: 2026-06-09
status: revised-after-workflow-research-and-gemini-review
tags:
  - 股票期權
  - Cloudflare
  - Workers-AI
  - Schwab-API
  - Sell-Put
  - R2
  - GitHub-Actions
---

# Option AI Worker 規劃：基於既有 static-data artifact

## 結論

AI Worker **不應重新計算 PCR / Sell Put 指標，也不應在每日批次預設調 Schwab API**。

`stock-screener` 現有 GitHub Actions / static-site pipeline 已經在 R2 static-data 產出 30–45D PCR、Put Vol、Put OI、7 日 put liquidity history 與技術面資料。AI Worker 的最佳職責是：

```text
讀取 R2 canonical static-data
→ 選出需要 AI 分析的 symbols
→ Queue 分發 per-symbol AI inference
→ Workers AI Gemma 產生風險分析 JSON
→ 寫回 R2 ai-analysis artifacts
```

修正後的核心架構：

```text
GitHub Actions static pipeline
→ static-site.yml 產出 R2 static-data
→ Cloudflare AI Producer 讀 R2 manifest / scan chunks
→ Cloudflare Queue: option-ai-symbol-jobs
→ Queue Consumer per symbol 調 Workers AI
→ R2: static-data/options/ai-analysis/{SYMBOL}.json
→ Aggregator Cron 產生 static-data/options/ai-analysis/latest.json
```

---

## 1. 現有 workflow 與 artifact 調研結果

### 1.1 Daily orchestrator

`.github/workflows/static-pipeline-daily.yml`：

- 每個交易日透過 UTC cron 覆蓋美東約 `08:00/09:00` 與 `18:00/19:00`。
- 依序 dispatch：

```text
daily-price.yml
scan-metrics.yml
group-rank.yml
static-site.yml
```

- 產出 `pipeline-run-manifest` release。
- Latest manifest：

```text
pipeline-run-latest-us.json
```

- manifest 內含 component artifacts：

```text
foundation_update
daily_price
scan_metrics
group_rank
listing_profile
etf_profile
```

每個 artifact 都包含：

```text
release
manifest_asset
bundle_asset_name
sha256
schema_version
market
as_of_date
symbol_count
symbol_coverage
```

### 1.2 scan-metrics artifact

`.github/workflows/scan-metrics.yml`：

- 下載 `foundation-update-data` 與 `daily-price-data`。
- 執行：

```text
python -m app.scripts.build_scan_metrics_artifact
```

- 產出：

```text
scan-metrics-latest-us.json
scan-metrics-us-{yyyymmdd}.json.gz
```

- 內容包含 technical / RS / ADR / setup metrics。
- **不呼叫 live provider，不產生 option PCR。**

### 1.3 static-site artifact

`.github/workflows/static-site.yml`：

- 下載 foundation / daily / scan / group / listing / ETF artifacts。
- 執行：

```text
python -m app.scripts.build_static_site_from_artifacts
```

- 產生 `frontend/public/static-data`。
- 透過 rclone sync 到 Cloudflare R2：

```text
static-data/
```

重要 R2 paths：

```text
static-data/manifest.json
static-data/markets/us/scan/manifest.json
static-data/markets/us/scan/chunks/chunk-0001.json
static-data/markets/us/scan/chunks/chunk-0002.json
...
static-data/markets/us/options/put-liquidity-history.json
```

### 1.4 Option PCR enrichment 已在 static build 做完

`backend/app/scripts/build_static_site_from_artifacts.py` 會在 static-site build 階段：

- 對排序後 top 300 rows 呼叫 Schwab `/marketdata/v1/chains`。
- 範圍：30–45 DTE。
- 合約類型：ALL。
- 寫入 scan row：

```text
option_pcr_volume_30_45dte
option_put_volume_30_45dte
option_call_volume_30_45dte
option_put_oi_30_45dte
option_call_oi_30_45dte
option_pcr_volume_30_45dte_expirations
option_pcr_volume_30_45dte_contracts
option_pcr_volume_30_45dte_min_dte
option_pcr_volume_30_45dte_max_dte
option_pcr_volume_30_45dte_asof
option_pcr_volume_30_45dte_provider
option_pcr_volume_30_45dte_error
```

同時產生 7 日歷史：

```text
static-data/markets/us/options/put-liquidity-history.json
```

schema：

```text
option-put-liquidity-history-v1
```

每個 symbol history 包含：

```text
date
put_volume
put_oi
pcr
asof
```

---

## 2. AI Worker 的正確定位

### 2.1 不再做的事

AI Worker 不做：

- 不重新抓 Schwab option chain。
- 不重新計算 PCR。
- 不重新計算 Put Vol / Put OI。
- 不重新計算 RS / ADR / MA alignment。
- 不作為第二套 screener engine。

原因：

1. 避免 GitHub Action 與 Worker 產生不同數據口徑。
2. 避免重複消耗 Schwab API quota。
3. 避免在 Cloudflare Worker 中處理 Schwab OAuth rotation 複雜度。
4. 讓 R2 static-data 成為 single source of truth。

### 2.2 AI Worker 負責的事

AI Worker 只做：

- 讀取 R2 canonical artifacts。
- 根據已有欄位挑選需要分析的 symbols。
- 將 row 摘要送入 Workers AI Gemma。
- 強制輸出結構化風險 memo JSON。
- 寫回 R2 `ai-analysis` artifacts。

---

## 3. 最推薦架構

```text
Static Pipeline Daily / Full
        ↓
static-site.yml uploads R2 static-data
        ↓
AI Producer Worker
        ↓
Cloudflare Queue: option-ai-symbol-jobs
        ↓
AI Consumer Worker
        ↓
R2 per-symbol analysis JSON
        ↓
Aggregator Cron
        ↓
R2 latest.json index
```

### 3.1 Producer Worker

觸發方式：**GitHub Actions 在 static-site R2 sync 後 webhook 觸發**。

Producer 流程：

1. 讀取：

```text
static-data/manifest.json
static-data/markets/us/scan/manifest.json
```

2. 檢查 freshness：

```text
manifest.generated_at
manifest.scan_as_of_date
manifest.price_as_of_date
scan_manifest.generated_at
scan_manifest.as_of_date
```

3. 掃描 scan chunks：

```text
static-data/markets/us/scan/chunks/*.json
```

4. 選出候選 symbols。
5. 檢查今日是否已存在 AI analysis，避免重複生成。
6. 將 per-symbol snapshot 投遞到 Queue。

### 3.2 Queue Consumer

Consumer 每次處理一個或少量 symbols：

1. 接收 Producer 提供的 row snapshot。
2. 組 prompt。
3. 呼叫 Workers AI Gemma。
4. 驗證 AI JSON shape。
5. 寫入 R2：

```text
static-data/options/ai-analysis/{SYMBOL}.json
```

### 3.3 Aggregator Cron

Consumer **不要同時更新 `latest.json`**，避免 R2 並發覆蓋。

建議新增一個 Aggregator Cron：

```text
AI Aggregator Cron
→ R2 list static-data/options/ai-analysis/*.json
→ 收集今日成功結果
→ 一次性覆蓋寫入 latest.json
```

輸出：

```text
static-data/options/ai-analysis/latest.json
```

---

## 4. 是否需要 Queue？

結論：**需要。**

原因：

- LLM inference wall-clock time 不穩定。
- 單一 Worker loop 50–300 檔容易 timeout。
- Queue 可提供 retry / DLQ / backpressure。
- per-symbol 任務失敗不會拖垮整批。

不建議：

```text
Producer Worker 直接 loop 全部 symbols 並逐一呼叫 AI
```

建議：

```text
Producer 只掃 R2 + 投 Queue
Consumer 才做 AI inference
```

---

## 5. Symbol 選擇策略

第一版建議只分析 canonical static build 已 enrichment 的 symbols，也就是通常為排序後 top 300 rows 且具備：

```text
option_pcr_volume_30_45dte_asof != null
```

候選條件可採以下規則：

```text
有 option PCR asof
且 Put Vol / Put OI 不為 null
且 current_price 不為 null
且 adv_usd / volume 足夠
```

可加分排序：

```text
rs_rating desc
adv_usd desc
option_put_volume_30_45dte desc
option_put_oi_30_45dte desc
adr_percent desc
```

建議第一版限制：

```text
max_symbols_per_run = 100
```

原因：

- 控制 Workers AI 成本。
- 避免每天產生太多低價值 memo。
- 先覆蓋最有流動性與最高關注度的 symbols。

---

## 6. 避免重複 AI 生成

Producer 投 Queue 前檢查 R2：

```text
static-data/options/ai-analysis/{SYMBOL}.json
```

若已存在，讀取 metadata 或 body 中：

```text
source.scan_generated_at
source.scan_as_of_date
source.option_pcr_asof
source.git_push_hash
```

若與目前 scan artifact 相同，跳過。

建議 per-symbol artifact 包含：

```json
{
  "schema_version": "option-ai-analysis-v1",
  "symbol": "MU",
  "status": "ok",
  "generated_at": "...",
  "source": {
    "type": "r2-static-data",
    "static_manifest_generated_at": "...",
    "scan_manifest_generated_at": "...",
    "scan_as_of_date": "...",
    "price_as_of_date": "...",
    "option_pcr_asof": "...",
    "git_push_hash": "..."
  },
  "input": {},
  "analysis": {}
}
```

---

## 7. AI input schema

AI input 只來自 R2 canonical row，不重新查 Schwab。

```ts
type OptionAiInput = {
  symbol: string;
  company_name?: string | null;
  current_price: number | null;
  price_change_1d: number | null;
  volume: number | null;
  adv_usd: number | null;
  rs_rating: number | null;
  rs_trend: number | null;
  adr_percent: number | null;
  ma_alignment: boolean | null;
  gics_sector: string | null;
  ibd_industry_group: string | null;

  option_pcr_volume_30_45dte: number | null;
  option_put_volume_30_45dte: number | null;
  option_call_volume_30_45dte: number | null;
  option_put_oi_30_45dte: number | null;
  option_call_oi_30_45dte: number | null;
  option_put_volume_30_45dte_history: Array<number | null> | null;
  option_put_oi_30_45dte_history: Array<number | null> | null;
  option_put_liquidity_history_dates: string[] | null;
  option_pcr_volume_30_45dte_asof: string | null;
  option_pcr_volume_30_45dte_provider: string | null;
  option_pcr_volume_30_45dte_error?: string | null;

  source_freshness: {
    static_manifest_generated_at: string | null;
    scan_manifest_generated_at: string | null;
    scan_as_of_date: string | null;
    price_as_of_date: string | null;
    git_push_hash: string | null;
  };
};
```

---

## 8. AI output schema

第一版就應固定 JSON，不要讓前端解析 markdown。

```ts
type OptionAiAnalysisOutput = {
  summary: string;
  risk_level: "low" | "medium" | "high" | "very_high";
  suitable_for_sell_put: "yes" | "watchlist" | "no";
  confidence: "low" | "medium" | "high";
  reasons: string[];
  preferred_profile: {
    delta_range?: string;
    dte_range?: string;
    liquidity_requirement?: string;
    notes: string[];
  };
  avoid_conditions: string[];
  data_quality: {
    option_metrics_available: boolean;
    stale: boolean;
    warnings: string[];
  };
};
```

外層 artifact：

```ts
type OptionAiAnalysisArtifact = {
  schema_version: "option-ai-analysis-v1";
  symbol: string;
  status: "ok" | "error";
  generated_at: string;
  source: Record<string, unknown>;
  input: OptionAiInput;
  analysis?: OptionAiAnalysisOutput;
  error?: {
    code: string;
    message: string;
  };
};
```

---

## 9. Prompt 原則

System prompt：

```text
你是嚴謹的美股期權 Sell Put 風險分析師。你只根據提供的 JSON 分析，不得臆測未提供的 option chain、新聞、財報或即時價格。你不提供下單建議，只做風險分析。輸出必須是有效 JSON。
```

User prompt 要求：

```text
請根據 input JSON 判斷該 symbol 是否適合列入 Sell Put 觀察。
請分析：PCR、Put Vol、Put OI、7 日流動性歷史、RS、ADR、MA alignment、價格動能、資料新鮮度。
不得自行計算未提供的 strike-level 報酬，不得編造合約、bid/ask、delta 或財報日期。
```

---

## 10. Schwab API / TokenManager 的決策

### 10.1 每日批次不使用 Schwab

每日 AI analysis pipeline 不使用 Schwab API。

Schwab API 已由 GitHub Actions `static-site.yml` / `build_static_site_from_artifacts.py` 負責。

### 10.2 第一版不實作 fallback Schwab DO

Gemini review 後建議：在每日批次已不依賴 Schwab 的前提下，第一版不要實作 TokenManager Durable Object，以降低複雜度。

若 R2 option metrics stale / missing，AI Worker 應：

- 標記 `data_quality.stale = true`。
- 降低 confidence。
- 不自行補抓 Schwab。
- 等待下一次 GitHub Actions pipeline 更新。

### 10.3 未來 ad-hoc 即時分析才考慮 TokenManager DO

若未來要支援「單檔即時分析」，且仍不使用 D1，才新增：

```text
TokenManager Durable Object
```

用途：

- DO Storage 保存動態 `refresh_token` / `access_token`。
- Cloudflare Secrets 保存 `SCHWAB_CLIENT_ID` / `SCHWAB_CLIENT_SECRET`。
- DO 用 single-flight `refreshPromise` 避免 refresh token rotation race。

但這不屬於第一版。

---

## 11. R2 output paths

Per-symbol：

```text
static-data/options/ai-analysis/{SYMBOL}.json
```

歷史版本可後續新增：

```text
static-data/options/ai-analysis/{YYYY-MM-DD}/{SYMBOL}.json
```

聚合索引：

```text
static-data/options/ai-analysis/latest.json
```

建議 latest schema：

```json
{
  "schema_version": "option-ai-analysis-index-v1",
  "generated_at": "...",
  "source_scan_generated_at": "...",
  "source_scan_as_of_date": "...",
  "count": 100,
  "rows": [
    {
      "symbol": "MU",
      "risk_level": "high",
      "suitable_for_sell_put": "watchlist",
      "confidence": "medium",
      "summary": "...",
      "path": "static-data/options/ai-analysis/MU.json"
    }
  ]
}
```

---

## 12. 與現有 option-screener-api 的關係

現有 Worker：

```text
workers/option-screener-api
```

目前是 agent-only read API，讀：

```text
static-data/markets/us/scan/*
```

但它的 `TABLE_FIELDS` 目前只暴露：

```text
symbol
current_price
volume
adv_usd
price_change_1d
rs_trend
rs_rating
adr_percent
ma_alignment
market_cap
gics_sector
ibd_industry_group
```

尚未暴露 option PCR 欄位。

因此 AI Worker 第一版應直接綁定同一個 R2 bucket 讀 static-data，不透過 `option-screener-api`。

未來可選擇擴充 read API，但不是 AI Worker 的必要依賴。

---

## 13. Wrangler bindings 建議

```jsonc
{
  "name": "option-ai-analyzer",
  "main": "src/index.ts",
  "compatibility_date": "2026-06-09",
  "ai": {
    "binding": "AI"
  },
  "queues": {
    "producers": [
      {
        "binding": "OPTION_AI_QUEUE",
        "queue": "option-ai-symbol-jobs"
      }
    ],
    "consumers": [
      {
        "queue": "option-ai-symbol-jobs",
        "max_batch_size": 1,
        "max_retries": 3,
        "dead_letter_queue": "option-ai-symbol-jobs-dlq"
      }
    ]
  },
  "r2_buckets": [
    {
      "binding": "STATIC_DATA_BUCKET",
      "bucket_name": "stock-screener"
    }
  ],
  "triggers": {
    "crons": [
      "30 13,23 * * 1-5"
    ]
  },
  "observability": {
    "enabled": true
  },
  "vars": {
    "STATIC_DATA_PREFIX": "static-data",
    "OPTION_AI_MAX_SYMBOLS": "100"
  }
}
```

> 實際 cron 應配合 static pipeline 完成時間調整；若改用 GitHub webhook 觸發，cron 可只保留為補償性 fallback。

---

## 14. 錯誤處理與可觀測性

Producer 應輸出 structured logs：

```json
{
  "event": "option_ai_producer_complete",
  "scan_generated_at": "...",
  "candidates": 100,
  "queued": 72,
  "skipped_existing": 28
}
```

Consumer 應輸出：

```json
{
  "event": "option_ai_consumer_complete",
  "symbol": "MU",
  "status": "ok",
  "duration_ms": 1234
}
```

失敗時寫 error artifact：

```text
static-data/options/ai-analysis/errors/{SYMBOL}.json
```

Queue 應設定：

```text
max_retries = 3
DLQ = option-ai-symbol-jobs-dlq
```

---

## 15. Gemini review 摘要

Gemini 對修正版架構的主要回饋：

1. 直接使用 R2 static-data 是正確方向，因為 existing pipeline 已經把 PCR、Put Vol、Put OI、7 日 liquidity history 與技術面資料打包進 scan chunks。
2. 仍然需要 Queue；單一 Worker loop 多檔 AI inference 容易 timeout。
3. Producer 應在投遞 Queue 前檢查 per-symbol AI artifact 的 source freshness，避免重複生成。
4. Consumer 不應更新 `latest.json`；應用獨立 Aggregator Cron 一次性聚合，避免 R2 並發覆蓋。
5. 第一版不建議保留 Schwab fallback / TokenManager DO，因為每日批次已不需要 Schwab；若未來做 ad-hoc 即時分析再新增。

---

## 16. 最小可行版本

第一版完成以下即可：

1. 建立 `workers/option-ai-analyzer`。
2. 綁定 Workers AI、R2、Queue。
3. Producer 讀 R2 manifest + scan chunks。
4. 根據 option PCR / liquidity / RS / ADR 選出最多 100 檔。
5. Producer 檢查已存在且同 freshness 的 `{SYMBOL}.json` 後跳過。
6. Consumer per symbol 呼叫 Gemma。
7. Consumer 寫 per-symbol JSON。
8. Aggregator Cron 產生 `latest.json`。
9. 前端或 agent 讀 `latest.json` 與 `{SYMBOL}.json`。

---

## 17. 明確不納入第一版

第一版不做：

- 不調 Schwab API。
- 不做 strike-level Sell Put candidate ranking。
- 不算 bid/ask 年化。
- 不整合新聞 / 財報 / SEC。
- 不做 D1。
- 不做 TokenManager DO。
- 不讓 AI 產生不存在於 input 的數字。

這些可作為後續版本，但不應阻塞第一版。
