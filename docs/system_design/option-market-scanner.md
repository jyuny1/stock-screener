# option-market-scanner 規劃

建立日期：2026-06-03

相關文件：

- [[option-analysis]]

---

# 一、結論：最佳方案是保留美股完整 screener，疊加 SP Ready filter

目前範圍只看美股，不處理其他國家股票市場。

整體目標是選出「SP（Sell Put）失敗也願意接盤」的美股標的，但不應用 SP 名單取代現有 screener 的美股完整 universe。

最佳方案：

```text
現有 screener 照美股完整 universe 跑
  ↓
保留 group ranking、breadth、scan rows、chart、filter、static export
  ↓
SP scanner 另外計算 SP Ready metadata
  ↓
以 symbol merge 到 screener rows
  ↓
前端在 Rating / Score 區新增 SP Ready：All / Yes / No
  ↓
使用者直接用現有 screener 介面查看 SP Ready 結果
```

此方案一次滿足：

- 保留現有 screener 的美股全市場分析與 group ranking。
- 保留現有 daily scan table、filter、sort、chart、CSV export。
- 顯示我自己計算出的 SP Ready 結果。
- 不把 option chain、IV、delta、DTE 混進 market-scanner。
- 可部署成 Cloudflare Pages + R2 / KV 的 private static-data viewer。

---

# 二、責任邊界

| 模組 | 職責 | 不負責 |
|---|---|---|
| 現有 stock-screener | 美股完整 universe scan、group ranking、breadth、scan rows、static-data export、前端展示 | 判斷股票是否適合 Sell Put 接盤 |
| SP scanner | 依 SEC-first 與 sector/industry-relative 邏輯產生 SP Ready metadata | 產生 option chain 或下單建議 |
| merge layer | 用 symbol 將 SP metadata 合併到 screener rows | 重算 group ranking |
| 前端 SP Ready filter | 用 All / Yes / No 過濾 sp_ready | 改變原始 universe |
| option-strategy / option-analysis | 對 SP Ready 候選做 Sell Put 合約精排 | 公司粗排 |

核心原則：**美股完整 universe 照跑，SP Ready 只是 row-level flag。**

---

# 三、資料流程

```text
GitHub Actions / backend job
  ↓
1. 跑現有 screener US full universe
  ↓
產生 scan rows、group ranking、breadth、static-data
  ↓
2. 跑 SP scanner
  ↓
產生 sp-base metadata
  ↓
3. merge by symbol
  ↓
scan rows 增加 sp_ready / sp_score / sp_reason
  ↓
4. 上傳到 Cloudflare R2
  ↓
5. KV 更新 latest pointer
  ↓
6. Cloudflare Pages 前端讀取 latest static-data
  ↓
7. Rating / Score 區用 SP Ready All / Yes / No 過濾
```

---

# 四、SP scanner 判斷邏輯

SP scanner 的任務是標記哪些股票適合作為「Sell Put 失敗也願意接盤」的基底。

## 4.1 最低可投資性 hard floor

用於排除明顯不適合接盤的標的：

- 主要交易所掛牌普通股。
- 排除 OTC、SPAC、shell、特殊證券與非標準 symbol。
- price >= 10 或 20。
- volume / ADV 達基本流動性門檻。
- marketCap >= 2B 或 5B。

marketCap 只是 hard floor，不是主要排序邏輯。

## 4.2 SEC-first 財務體質

正式 SP Ready 判斷應以 SEC / EDGAR 可驗證資料為主：

- SEC CIK / ticker mapping。
- 10-K。
- 10-Q。
- XBRL companyfacts。

核心評估：

- revenue 是否穩定或成長。
- EPS / net income 是否具備可持續性。
- operating cash flow 是否健康。
- free cash flow 是否穩定。
- debt burden 是否可控。
- interest coverage 是否足夠。
- gross margin / operating margin 是否惡化。
- share dilution 是否嚴重。

若 SEC facts 缺漏，標記 sec_data_status = incomplete。

## 4.3 sector/industry-relative 評估

SP Ready 不應只用全市場固定門檻，而應在 peer group 內比較：

- sector-relative percentile。
- industry-relative percentile。
- peer median comparison。
- peer ranking。

分類來源：

- SEC SIC：官方分類與可追溯基準。
- NASDAQ sector/industry：補充分組與 cross-check。
- 現有 screener 的 GICS / IBD industry：保留前端與 group ranking 脈絡。

## 4.4 行業趨勢

SP scanner 應避免選出財務看似穩健、但所屬行業長期逆風的公司。

觀察項目：

- industry 內營收成長是否普遍正向。
- industry 內獲利能力是否普遍改善或惡化。
- 是否受週期、政策、利率、商品價格或技術替代壓力影響。
- 個別公司改善是否只是單次事件。

---

# 五、SP metadata 與 row schema

## 5.1 SP scanner 輸出

```json
{
  "schema_version": "sp-base-v1",
  "as_of_date": "2026-06-04",
  "rows": [
    {
      "symbol": "MSFT",
      "sp_ready": true,
      "sp_status": "eligible",
      "sp_score": 87,
      "financial_health_score": 90,
      "industry_trend_score": 74,
      "peer_percentile": 91,
      "sec_data_status": "complete",
      "sp_reason": "FCF positive, low debt risk, strong peer profitability"
    }
  ]
}
```

## 5.2 merge 後的 screener row

```json
{
  "symbol": "MSFT",
  "company_name": "Microsoft Corporation",
  "gics_sector": "Technology",
  "ibd_industry_group": "Software",
  "composite_score": 82,
  "se_pattern_primary": "vcp",
  "sp_ready": true,
  "sp_status": "eligible",
  "sp_score": 87,
  "financial_health_score": 90,
  "industry_trend_score": 74,
  "peer_percentile": 91,
  "sec_data_status": "complete",
  "sp_reason": "FCF positive, low debt risk, strong peer profitability"
}
```

非 SP Ready 股票：

```json
{
  "symbol": "XYZ",
  "sp_ready": false,
  "sp_status": "excluded"
}
```

欄位定義：

| 欄位 | 用途 |
|---|---|
| sp_ready | 前端 All / Yes / No filter 的核心 boolean |
| sp_status | eligible / watchlist / excluded，供後續細分 |
| sp_score | SP 接盤基底總分 |
| financial_health_score | SEC-first 財務體質分數 |
| industry_trend_score | 行業趨勢分數 |
| peer_percentile | sector/industry 內相對排名 |
| sec_data_status | complete / incomplete / stale |
| sp_reason | 入選或排除原因 |

---

# 六、前端設計

## 6.1 Rating / Score 區新增 SP Ready

在 Rating / Score 區新增一個三態 button，樣式比照 SE Ready、VCP、Passes：

```text
SP Ready  All / Yes / No
```

狀態：

- All：不套用 SP Ready filter。
- Yes：只顯示 sp_ready = true。
- No：只顯示 sp_ready = false。

前端 filter state：

```js
spReady: null  // All
spReady: true  // Yes
spReady: false // No
```

static filter 邏輯：

```js
if (filters.spReady != null && Boolean(row.sp_ready) !== filters.spReady) {
  return false;
}
```

## 6.2 Result table 顯示

在 symbol 或 rating 附近顯示 SP Ready chip：

```text
SP Ready
```

hover 或 sidebar 顯示：

- sp_score。
- financial_health_score。
- industry_trend_score。
- peer_percentile。
- sec_data_status。
- sp_reason。

---

# 七、需要修改的現有 screener 位置

## 7.1 前端 filter state

檔案：

```text
frontend/src/features/scan/defaultFilters.js
```

新增：

```js
spReady: null
```

## 7.2 Rating / Score UI

檔案：

```text
frontend/src/features/scan/components/filterPanel/RatingFiltersSection.jsx
```

新增 SP Ready button：

```text
SP Ready All / Yes / No
```

## 7.3 static-site filter logic

檔案：

```text
frontend/src/static/scanClient.js
```

新增：

```js
if (filters.spReady != null && Boolean(row.sp_ready) !== filters.spReady) return false;
```

## 7.4 server mode query params

若 server mode 也要支援，需同步修改：

```text
frontend/src/utils/filterUtils.js
backend/app/api/v1/scan_filter_params.py
backend/app/infra/query/scan_result_query.py
```

新增 query param：

```text
sp_ready=true | false
```

## 7.5 static export merge

在 static export 或後處理階段讀取 SP metadata，依 symbol 合併到 scan rows。

合併規則：

```text
if symbol in SP metadata:
  row.sp_ready = metadata.sp_ready
  row.sp_status = metadata.sp_status
  row.sp_score = metadata.sp_score
  row.sp_reason = metadata.sp_reason
else:
  row.sp_ready = false
  row.sp_status = excluded
```

---

# 八、Cloudflare 儲存與部署

部署形態：

```text
GitHub Actions / backend job
  ↓
產生已 merge sp_ready 的 static-data
  ↓
上傳 Cloudflare R2
  ↓
KV 更新 latest pointer
  ↓
Cloudflare Pages 前端讀取 latest result
  ↓
Cloudflare Access 保護頁面
```

儲存分工：

| 儲存位置 | 用途 |
|---|---|
| R2 | static-data、daily scan chunks、SP metadata、歷史 snapshot |
| KV | latest_date、latest_manifest_path、小型狀態資訊 |
| Cloudflare Pages | React/Vite 靜態前端 |
| Cloudflare Worker / Pages Function | API gateway、R2 讀取代理、AI chatbot gateway |

建議路徑：

```text
R2:
market-scanner/2026-06-04/static-data/manifest.json
market-scanner/2026-06-04/static-data/markets/us/scan/manifest.json
market-scanner/2026-06-04/static-data/markets/us/scan/chunk-001.json
market-scanner/2026-06-04/sp-base/metadata.json

KV:
market-scanner:latest_date = 2026-06-04
market-scanner:latest_manifest_path = market-scanner/2026-06-04/static-data/manifest.json
```

---

# 九、screener 美股 universe 來源與對齊方式

## 9.1 screener 目前如何取得美股名單

已檢查現有 screener 代碼後，美股名單的最終基準是資料庫中的 StockUniverse，而不是前端 filter 或 SP scanner 自己產生的名單。

現有流程：

```text
refresh_stock_universe(market = US)
  ↓
優先 sync_weekly_reference_from_github(market = US)
  ↓ 如果 GitHub weekly reference bundle 不可用
fallback 到 Finviz
  ↓
抓 NYSE / NASDAQ / AMEX
  ↓
透過 FinvizUniverseIngestionAdapter + SecurityMasterResolver canonicalize
  ↓
寫入 stock_universe
```

美股 active universe 查詢條件：

```sql
market = 'US'
and is_active = true
```

主要欄位：

```text
symbol
name
market
exchange
sector
industry
market_cap
is_active
status
source
```

因此 SP scanner 的輸入 universe 必須來自：

```text
StockUniverse where market = 'US' and is_active = true
```

而不是另外從 NASDAQ、SEC 或其他來源重建一份最終股票名單。

## 9.2 對齊原則

SP scanner 與 screener 不應直接用原始 symbol 字串硬合併。最佳做法是讓 screener 的 US active universe 成為唯一 symbol source of truth，SP scanner 只對這批 symbols 做評估。

對齊流程：

```text
讀取 screener US active universe
  ↓
取得 StockUniverse.symbol 作為 canonical symbols
  ↓
SP scanner 只評估這批 symbols
  ↓
SP scanner 輸出同一批 symbols 的 SP metadata
  ↓
用 symbol merge 回 screener scan rows
  ↓
輸出 coverage / unmatched 報表
```

確認結果：

- 現有 screener 有 SecurityMasterResolver 作為 symbol / market / exchange / local_code 的 canonical identity 來源。
- US symbol 主要會做 trim、uppercase、移除開頭 $。
- US 不會自動把 BRK.B 與 BRK-B 視為同一個 symbol；兩者在目前 resolver 中是不同 canonical_symbol。
- 非 US market 會依 market / exchange 加 provider suffix，例如 0700 + HKEX 會轉成 0700.HK，7203 + TSE 會轉成 7203.T；但本計畫目前不處理非美股。
- 現有 universe row 使用 StockUniverse.symbol 作為 canonical symbol；static export 與 scan rows 也是依這個 symbol 輸出。

## 9.3 merge 規則

SP scanner 的 symbol merge 規則：

```text
StockUniverse.symbol from screener US active universe
  ↓
SP scanner 評估同一個 symbol
  ↓
SP metadata 使用相同 symbol 輸出
  ↓
用 symbol 對 screener scan rows merge
  ↓
輸出 matched / unmatched 報表
```

若 SP scanner 內部需要從 SEC CIK / ticker mapping 找資料，SEC ticker 必須先對應回 screener 的 StockUniverse.symbol；不能讓 SEC 或 NASDAQ symbol 格式覆蓋 screener canonical symbol。

必要報表：

```json
{
  "as_of_date": "2026-06-04",
  "sp_input_count": 250,
  "matched_count": 247,
  "unmatched_count": 3,
  "unmatched_ratio": 0.012,
  "unmatched_symbols": [
    {
      "raw_symbol": "BRK/B",
      "canonical_symbol": "BRK/B",
      "reason": "not_found_in_screener_rows"
    }
  ]
}
```

驗收門檻：

- unmatched symbols 不得 silently drop。
- unmatched_ratio 必須低於可接受門檻，例如 1%。
- unmatched_symbols 必須保存到 R2，並在 job log 中輸出。
- 若 unmatched_ratio 超過門檻，當日 SP Ready merge 應標記為 warning 或 fail。

特別注意：

- BRK.B / BRK-B / BRK/B 這類 class share symbol 必須明確決定對應格式。
- GOOG / GOOGL 不應自動合併，因為它們是不同 share class。
- ADR、非 US suffix、delisted / inactive symbols 需要出現在 unmatched 報表。
- SP scanner 只做美股，必須固定 market = US，避免錯誤套用非 US suffix。

---

# 十、驗收標準

完成後必須滿足：

- SP scanner 的輸入 universe 來自 screener 的 StockUniverse：market = 'US' and is_active = true。
- SP scanner 不自行用 NASDAQ / SEC 重建最終股票名單。

- 現有 screener 仍跑美股完整 universe。
- group ranking / breadth 不因 SP 名單被截斷。
- daily scan All Stocks 仍可看美股完整市場。
- Rating / Score 區存在 SP Ready All / Yes / No。
- SP Ready = Yes 只顯示 sp_ready = true。
- SP Ready = No 只顯示 sp_ready = false。
- scan rows 帶有 sp_ready、sp_status、sp_score、sp_reason 等欄位。
- SP scanner output symbols 必須與 screener StockUniverse.symbol 對齊，再 merge 到 screener rows。
- unmatched_symbols 報表可追蹤，且不得被靜默丟棄。
- static-data 可上傳 R2，前端可讀 latest 結果。
- 不需要 option chain、IV、delta、DTE、strike。

---

# 十一、不做事項

- 不用 SP 名單取代完整 universe。
- 不破壞 group ranking / breadth 的全市場基準。
- 不把 SP Ready 塞進 SE Pattern。
- 不重寫現有 screener UI。
- 不重寫現有 scan result table。
- 不在 market-scanner 做全市場 option chain 掃描。
- 不在 market-scanner 產生 strike、delta、DTE、premium yield、open interest。
- 不把 Cloudflare Pages 當完整 FastAPI server。

---

# 十二、後續改善方向

- 將 analysis tool 的深度判斷結果回寫 sp_status。
- 將 SP reject reason 回寫，改善下一輪 SP scanner。
- 對不同 sector/industry 設定不同財務權重。
- 建立 SEC facts 缺漏與資料品質報表。
- 建立 SP ranking 歷史變化。
- 將 AI chatbot 接到 Cloudflare Worker / Pages Function，讓靜態前端可查詢 R2 中的 daily scanner 結果。
- option-strategy 只針對 sp_ready = true 或 sp_status = eligible / watchlist 的股票做合約精排。