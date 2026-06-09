# option-analysis pi extension 規劃

建立日期：2026-06-02
更新日期：2026-06-03

相關文件：

- [[schwab_api]]
- [[finnhub_api]]
- [[選擇權標的選擇系統]]
- [[option-market-scanner]]
- [[option-strategy]]
- SEC EDGAR / edgartools：https://github.com/dgunning/edgartools

---

# 零、範圍說明

全市場掃描已拆到獨立 project 文件：

```text
[[option-market-scanner]]
```

本文件只聚焦 `option-analysis` pi extension：針對使用者指定 symbol 做即時 Sell Put 合約與標的風險分析。

策略模板、部位大小、帳戶曝險與 Wheel 規則已拆到：

```text
[[option-strategy]]
```

---

# 一、命名決策

原本討論的 extension 名稱：

```text
schwab-option
```

正式改名為：

```text
option-analysis
```

原因：

```text
1. 系統不只使用 Schwab API，也會整合 Finnhub 與 SEC EDGAR。
2. 功能目標不只是查 option chain，而是做 Sell Put 合約與標的風險分析。
3. option-analysis 更適合未來擴充到其他資料源，例如 edgartools、FMP、Alpha Vantage、yfinance。
```

建議 pi extension 檔案位置：

```text
.pi/extensions/option-analysis.ts
```

建議主要工具名稱：

```text
option_analysis_sell_put_scan
option_analysis_quote
option_analysis_earnings
option_analysis_news
option_analysis_profile
option_analysis_sec_filings
option_analysis_sec_financials
```

---

# 二、系統定位

`option-analysis` 是一個 Sell Put 標的與選擇權合約分析 extension。

第一版定位：

```text
Schwab 官方 market data + Finnhub 事件與公司資料 + SEC 正式財報資料的 Sell Put 評估器。
```

不是第一版目標：

```text
1. 不自動下單。
2. 不做完整投資建議。
3. 不保證新聞與財報資料完整。
4. 不做全市場高頻掃描。
5. 不取代下單前人工確認。
```

---

# 三、資料源分工

## 1. Schwab API

- Schwab 是主要行情與選擇權資料源
- API 有 120 次/分鐘調用的次數限制

使用 endpoint：

| 功能 | Endpoint | 用途 |
|---|---|---|
| Quote | `/marketdata/v1/quotes` | 股票 / ETF / 指數 / option quote |
| Option Chain | `/marketdata/v1/chains` | 取得 Put chain、Greeks、IV、OI、Volume、Bid/Ask |
| Expiration Chain | `/marketdata/v1/expirationchain` | 取得可用到期日與 DTE |
| Price History | `/marketdata/v1/pricehistory` | 取得 K 線，計算 MA、HV、近期報酬 |
| Market Hours | `/marketdata/v1/markets` | 確認市場是否開盤 |
| Instruments | `/marketdata/v1/instruments` | 查 symbol / CUSIP / instrument 基本資訊 |

Schwab 負責：

```text
- underlying price
- option bid / ask / mark
- delta / theta / gamma / vega / IV
- open interest / volume
- DTE / strike / moneyness
- price history
- market status
```

## 2. Finnhub API

Finnhub 補 Schwab 不足的公司與事件資料。

使用 endpoint：

| 功能 | Endpoint | 用途 |
|---|---|---|
| 財報行事曆 | `/calendar/earnings` | 判斷合約持有期間是否踩財報 |
| 歷史 EPS surprise | `/stock/earnings` | 評估財報波動風險 |
| Company Profile | `/stock/profile2` | 補 industry、market cap、country、exchange |
| Company News | `/company-news` | 檢查近期重大新聞與事件熱度 |
| Market News | `/news` | 補市場大環境事件 |
| Basic Financials | `/stock/metric` | P/E、EPS、52W high/low、market cap 等 |
| Recommendation | `/stock/recommendation` | 分析師推薦趨勢 |
| Insider Sentiment | `/stock/insider-sentiment` | 內部人交易情緒 |

Finnhub 負責：

```text
- earnings date
- earnings risk
- sector / industry proxy
- market cap
- recent company news
- analyst recommendation trend
- insider sentiment
- basic company quality metrics
```

### Finnhub TypeScript 開發選型

Finnhub 官方提供多種 library：

```text
Python
Go
Javascript / NPM
Ruby
Kotlin
PHP
```

但 `option-analysis` 是 pi TypeScript extension，建議不要直接依賴官方 Finnhub NPM client，原因：

```text
1. 官方 Javascript client 偏 callback / OpenAPI generated style，不夠適合 pi extension 的 async/await 流程。
2. 官方 NPM client 對 TypeScript 型別支援不如直接自定 interface 清楚。
3. option-analysis 只需要少數 endpoint，直接 fetch 更輕量、可控、易除錯。
4. pi extension 可避免額外 package 安裝與 runtime dependency 問題。
```

建議做法：

```text
使用原生 fetch + 自定 TypeScript interface + 統一 Finnhub client wrapper。
```

示意：

```ts
async function finnhubGet<T>(path: string, params: Record<string, string | number | boolean>): Promise<T> {
  const url = new URL(`https://finnhub.io/api/v1${path}`);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value));
  url.searchParams.set("token", process.env.FINNHUB_API_KEY ?? "");
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Finnhub HTTP ${response.status}`);
  return await response.json() as T;
}
```

Finnhub 使用限制：

```text
30 API calls / second limit
```

對 option-analysis 的實作要求：

```text
1. 加入簡單 rate limiter，建議保守設為 20 calls / second。
2. 多 symbol 掃描時限制並發，例如 concurrency = 3～5。
3. 對 profile / earnings / news 加 session cache，避免同一 symbol 重複查。
4. 遇到 HTTP 429 時做 retry with backoff。
5. 不要為每個 option contract 呼叫 Finnhub；Finnhub 只在 symbol 層呼叫。
```

可考慮但不優先：

| 選項 | 評估 |
|---|---|
| 官方 `finnhub` NPM | 可用，但 TypeScript 型別與 async/await 體驗較差 |
| `finnhub-ts` | 型別較好，但增加第三方依賴；若未來 endpoint 變多可重新評估 |
| `@stoqey/finnhub` | Promise wrapper 類型較現代，但仍是額外依賴 |
| 原生 `fetch` | MVP 最推薦，最輕量、最可控 |

結論：

```text
option-analysis MVP 使用原生 fetch，不使用 Finnhub 官方 NPM library。
```

## 3. SEC EDGAR / edgartools

SEC EDGAR 是正式申報資料源；`edgartools` 是 Python 套件，用來降低手刻 SEC API、CIK、accession URL、XBRL tag 與財報表準化的成本。

專案：

```text
https://github.com/dgunning/edgartools
```

安裝：

```bash
python3 -m pip install --user edgartools
```

使用時必須設定 SEC identity / User-Agent：

```python
from edgar import *
set_identity("option-analysis your.email@example.com")
company = Company("AAOI")
```

edgartools 負責：

```text
- ticker → CIK
- 最新 10-K / 10-Q / 8-K 查詢
- 10-K / 10-Q 財務表解析
- income statement / balance sheet / cash flow statement
- company facts / XBRL 標準化
- filing text / MD&A / Risk Factors / 8-K 事件文字抽取
```

對 Schwab / Finnhub 的補強：

| 資料 | Schwab | Finnhub | SEC / edgartools |
|---|---:|---:|---:|
| option chain / Greeks | 主要來源 | 無 | 無 |
| 財報日期 | 無 | 主要來源 | filing 日期，不等於財報日 |
| 公司新聞 | 無 | 主要來源 | 8-K 正式重大事件 |
| company profile | 部分 instrument | 主要來源 | CIK、SIC、申報分類 |
| 正式財報內容 | 無 | 摘要 / 指標 | 主要來源 |
| 三大財報表 | 無 | 指標型 | 主要來源 |
| 風險因素 / MD&A | 無 | 無 | 主要來源 |

實測 AAOI 可取得：

```text
Company: APPLIED OPTOELECTRONICS, INC.
CIK: 1158114
Industry: Semiconductors & Related Devices
Latest 10-K: 2026-02-26
Latest 10-Q: 2026-05-07
Latest 8-K: 2026-05-14
```

AAOI edgartools 測試摘要：

```text
FY2025 revenue: $455.7M
FY2025 net income: -$38.2M
FY2025 operating cash flow: -$174.4M
Q1 2026 revenue: $151.1M
Q1 2026 net income: -$14.3M
Q1 2026 cash: $439.7M
Q1 2026 liabilities/assets: 約 29.4%
```

對 Sell Put 的用途：

```text
1. 判斷公司是否值得被指派接股。
2. 補正式營收、獲利、現金流、資產負債趨勢。
3. 掃描 8-K 找融資、會計師變更、重大合約等事件。
4. 摘取 10-K / 10-Q 的 Risk Factors 與 MD&A 作為人工審查材料。
```

---

# 四、核心使用情境

## 情境 1：單一股票 Sell Put 掃描

輸入：

```text
AAPL
```

流程：

```text
1. Schwab /quotes 取得現價、成交量、bid/ask。
2. Schwab /chains 取得 30～45 DTE Put chain。
3. 篩 Delta -0.15～-0.30 的 OTM Put。
4. 篩 OI、Volume、Bid-Ask Spread。
5. 計算 breakeven、cash required、annualized return、OTM%。
6. Finnhub /calendar/earnings 檢查是否踩財報。
7. Finnhub /profile2 補 industry、market cap。
8. Finnhub /company-news 補近期新聞。
9. edgartools 補最新 10-K / 10-Q / 8-K 與正式財報趨勢。
10. 輸出候選合約表格與風險提示。
```

輸出：

```text
- Top Sell Put candidates
- 合約流動性
- 報酬 / 風險
- 財報風險
- 新聞事件風險
- 公司基本資訊
- SEC 正式財報摘要
- 最新 8-K 重大事件
```

## 情境 2：多股票候選比較

輸入：

```text
AAPL,BAC,AMD,NVDA,AAOI
```

流程：

```text
1. 對每個 symbol 執行單一股票掃描。
2. 每個 symbol 只保留前 N 個合約。
3. 依總分排序。
4. 輸出跨標的候選清單。
```

用途：

```text
快速比較多檔股票中，哪一個 Sell Put 合約更適合。
```

---

# 五、MVP 篩選規則

## 1. 合約硬條件

預設規則：

```text
contractType = PUT
DTE = 30～45
Delta = -0.15 ～ -0.30
OTM only
OI >= 100
Volume >= 10
Bid > 0
Ask > Bid
Spread% <= 15%
```

可調參數：

```text
symbols
minDte
maxDte
minDelta
maxDelta
minOpenInterest
minVolume
maxSpreadPct
minAnnualizedReturn
includeEarnings

註：accountSize、maxCashPctPerTrade、position sizing 與曝險控制屬於 [[option-strategy]]，不放在 option-analysis 內。
```

## 2. 計算欄位

對每個 Put 合約計算：

```text
mid = (bid + ask) / 2
premium = mark || mid || bid
spread = ask - bid
spread_pct = spread / mid
breakeven = strike - premium
cash_required = strike * 100
premium_income = premium * 100
cash_secured_return = premium / strike
annualized_return = premium / strike * 365 / DTE
otm_pct = (underlying_price - strike) / underlying_price
assignment_buffer = underlying_price - breakeven
assignment_buffer_pct = assignment_buffer / underlying_price
```

## 3. 財報風險

Finnhub `/calendar/earnings`：

```text
若 earnings_date <= option_expiry：踩財報
若 0 <= days_to_earnings <= 14：高財報風險
若 earnings_date 在 expiry 後：財報風險較低
若沒有資料：earnings_risk = unknown
```

預設行為：

```text
includeEarnings = false
```

也就是預設排除持有期間踩財報的合約。

## 4. 新聞事件風險

Finnhub `/company-news`：

```text
news_window_days = 7
recent_news_count = 最近 7 天新聞數
latest_headlines = 最新 3～5 則新聞標題
```

風險關鍵字初版：

```text
lawsuit
investigation
SEC
DOJ
FDA
bankruptcy
fraud
recall
guidance cut
downgrade
short seller
halt
restatement
```

若命中：

```text
event_risk = elevated
輸出警告，但不一定自動排除
```

## 5. SEC 正式財報與 8-K 風險

edgartools：

```text
latest_10k = Company(symbol).latest_tenk
latest_10q = Company(symbol).latest_tenq
latest_8k = Company(symbol).get_filings(form="8-K").latest()
income_statement = Company(symbol).income_statement(...)
balance_sheet = Company(symbol).balance_sheet(...)
cash_flow = Company(symbol).cash_flow_statement(...)
```

建議抽取欄位：

```text
revenue_trend
net_income_trend
operating_cash_flow_trend
cash_balance
liabilities_to_assets
gross_margin
operating_margin
latest_10k_url
latest_10q_url
latest_8k_url
latest_8k_items
```

風險規則初版：

```text
若最近 4 季持續虧損：profitability_risk = high
若 operating cash flow 連續為負：cash_flow_risk = high
若 liabilities/assets > 60%：balance_sheet_risk = elevated
若應收帳款或庫存快速增加：working_capital_risk = elevated
若最新 8-K 包含股權發行 / ATM / auditor change / going concern：sec_event_risk = elevated
```

AAOI 實測風險標籤：

```text
growth_score: 高
profitability_risk: 高
operating_cash_flow_risk: 高
cash_buffer: 尚可
dilution_risk: 高（$600M ATM）
auditor_change_watch: 中低（文件稱無 disagreement）
sell_put_risk_profile: 風險偏高，策略層需另行判斷是否適合
```

---

# 六、評分模型初版

總分 100：

| 類別 | 權重 | 說明 |
|---|---:|---|
| 合約流動性 | 25 | OI、Volume、Spread% |
| 安全邊際 | 25 | Delta、OTM%、breakeven buffer |
| 報酬 | 20 | premium、annualized return |
| 財報 / 事件風險 | 15 | earnings、news keywords、8-K event |
| 標的品質 | 15 | market cap、industry、SEC 財報趨勢、basic metrics |

第一版可先用規則分數，不做複雜模型。

示意：

```text
liquidity_score:
  OI >= 1000 +10
  Volume >= 100 +10
  Spread% <= 5% +5

safety_score:
  Delta between -0.15 and -0.25 +10
  OTM% >= 5% +10
  breakeven buffer >= 8% +5

return_score:
  annualized_return >= 20% +10
  annualized_return >= 30% +15
  premium enough after spread +5

event_score:
  no earnings before expiry +10
  no negative keywords +5

quality_score:
  market cap >= 10B +5
  industry available +2
  revenue growth positive +3
  net income positive +3
  operating cash flow positive +3
  liabilities/assets reasonable +2
  PE / EPS acceptable +2
```

---

# 七、建議工具設計

## 1. `option_analysis_sell_put_scan`

用途：主要工具，掃描一個或多個 symbol 的 Sell Put 候選合約。

參數：

```ts
{
  symbols: string[];
  minDte?: number;              // default 30
  maxDte?: number;              // default 45
  minDelta?: number;            // default -0.30
  maxDelta?: number;            // default -0.15
  minOpenInterest?: number;     // default 100
  minVolume?: number;           // default 10
  maxSpreadPct?: number;        // default 0.15
  limit?: number;               // default 10
  includeEarnings?: boolean;    // default false
  includeNews?: boolean;        // default true
}
```

輸出：

```text
終端友善 Unicode 表格
- symbol
- expiry
- DTE
- strike
- delta
- bid / ask / mark
- OI / volume
- spread%
- annualized return
- breakeven
- cash required
- earnings risk
- score
```

## 2. `option_analysis_earnings`

用途：查指定 symbol 的財報日期。

參數：

```ts
{
  symbol: string;
  from?: string;
  to?: string;
}
```

## 3. `option_analysis_news`

用途：查指定 symbol 最近新聞。

參數：

```ts
{
  symbol: string;
  days?: number;
  limit?: number;
}
```

## 4. `option_analysis_profile`

用途：查公司 profile 與基本財務。

參數：

```ts
{
  symbol: string;
}
```

## 5. `option_analysis_quote`

用途：查 Schwab quote。

參數：

```ts
{
  symbols: string[];
  fields?: string[];
}
```

## 6. `option_analysis_price_history`

用途：查指定 symbol 的 Schwab price history，提供技術位置與近期價格脈絡，讓大模型執行 [[option-strategy]] 時能判斷 strike / breakeven 是否有技術安全邊際。

參數：

```ts
{
  symbols: string[];
  period?: "1m" | "3m" | "6m" | "1y";  // default "6m"
  frequency?: "daily" | "weekly";        // default "daily"
  includeMovingAverages?: boolean;        // default true
  includeVolatility?: boolean;            // default true
}
```

輸出：

```text
- symbol
- current price
- 5D / 20D / 60D return
- 20D / 50D / 200D moving average
- price vs 20D / 50D / 200D MA
- 20D high / low
- 60D high / low
- 52W high / low（若 period 足夠）
- 20D / 60D realized volatility
- recent drawdown from high
- technical_context 標籤，例如 extended_upside、near_support、below_50dma、high_realized_volatility
```

對 Sell Put 的用途：

```text
1. 判斷 breakeven 是否落在近期支撐或均線附近。
2. 判斷目前是否短期漲幅過大，避免追高賣 Put。
3. 判斷近期 realized volatility 是否異常。
4. 幫助比較多 ticker 時辨識技術位置更好的標的。
```

## 7. `option_analysis_sec_filings`

用途：用 edgartools 查指定 symbol 最新 SEC filings。

參數：

```ts
{
  symbol: string;
  forms?: string[];  // default ["10-K", "10-Q", "8-K"]
  limit?: number;    // default 5
}
```

輸出：

```text
- company name
- CIK
- SIC / industry
- latest 10-K / 10-Q / 8-K
- filing date
- accession number
- SEC URL
- 8-K item 摘要（若可抽取）
```

## 8. `option_analysis_sec_financials`

用途：用 edgartools 查指定 symbol 的正式財報摘要。

參數：

```ts
{
  symbol: string;
  periods?: number;       // default 4
  period?: "annual" | "quarterly";
  includeFilings?: boolean;
}
```

輸出：

```text
- revenue
- gross profit
- operating income
- net income
- EPS
- operating cash flow
- cash
- assets / liabilities / equity
- liabilities/assets
- 風險標籤
```

---

# 八、環境變數

不應把 API key 或 token 寫死在 extension。

建議：

```bash
export SCHWAB_ACCESS_TOKEN='...'
export SCHWAB_REFRESH_TOKEN='...'
export SCHWAB_CLIENT_ID='...'
export SCHWAB_CLIENT_SECRET='...'
export FINNHUB_API_KEY='...'
export SEC_EDGAR_IDENTITY='option-analysis your.email@example.com'
```

本地 token 檔也可支援：

```text
./schwab_tokens.json      # access_token / refresh_token / expires_in
./finnhub_token.json      # API_KEY
```

extension 讀取：

```ts
process.env.SCHWAB_ACCESS_TOKEN
process.env.SCHWAB_REFRESH_TOKEN
process.env.SCHWAB_CLIENT_ID
process.env.SCHWAB_CLIENT_SECRET
process.env.FINNHUB_API_KEY
process.env.SEC_EDGAR_IDENTITY
```

Schwab token 規則：

```text
1. Schwab access token 的 expires_in 約 1800 秒。
2. 因為 pi tool 可能隔很久才被呼叫，不能假設檔案中的 access_token 仍有效。
3. 最佳做法：每次執行任何 Schwab API tool 前，先用 refresh_token refresh 一次 access token。
4. refresh 成功後，更新 schwab_tokens.json，寫入新的 access_token / refresh_token / expires_in / refreshed_at。
5. 若 refresh 失敗，才 fallback 使用現有 SCHWAB_ACCESS_TOKEN / schwab_tokens.json.access_token，並在 HTTP 401 時提示使用者重新授權。
6. 不應只依 expires_in 做記憶體快取判斷，因為 pi extension 可能 reload 或 token 檔可能由外部流程更新。
```

Schwab OAuth refresh flow 是第一版必要能力，不再視為未來擴充。

MVP fallback：若缺少 Schwab refresh 所需 client credentials，仍可暫時使用既有 access token，但應明確提示 token 可能已過期。

## 混合語言架構結論

`option-analysis` 採用 **TypeScript 為主、Python 僅作為 SEC / edgartools helper** 的架構。

結論：

```text
Schwab API：TypeScript fetch
Finnhub API：TypeScript fetch
SEC / edgartools：Python helper，透過 child_process 橋接回 TypeScript
```

原因：

```text
1. Schwab 與 Finnhub 都是簡單 REST API，用 TypeScript fetch 最輕量、最可控。
2. Finnhub 官方 NPM client 對 TypeScript 與 async/await 體驗不如自寫 fetch wrapper。
3. edgartools 是 Python 套件，且在 SEC filing / XBRL / 財報表準化上明顯優於手刻 TypeScript。
4. TypeScript 保持為 orchestration layer，負責資料整合、評分、表格輸出與 pi tool interface。
5. Python helper 僅輸出 JSON，不處理 Schwab / Finnhub / 最終格式化。
```

建議檔案結構：

```text
.pi/extensions/option-analysis.ts
  ├─ Schwab fetch client
  ├─ Finnhub fetch client
  ├─ rate limiter / cache / retry
  ├─ scoring model
  ├─ Unicode table formatter
  └─ child_process 呼叫 Python helper

.pi/extensions/option-analysis/sec_edgar.py
  └─ edgartools:
     - set_identity()
     - Company(symbol)
     - get_filings()
     - income_statement()
     - balance_sheet()
     - cash_flow_statement()
     - output JSON
```

TypeScript 負責：

```text
- 驗證 pi tool 參數
- 呼叫 Schwab / Finnhub REST API
- 控制 Finnhub rate limit / cache / retry
- 呼叫 sec_edgar.py
- 整合 Schwab + Finnhub + SEC 結果
- 計算 Sell Put 分數與風險標籤
- 統一格式化表格與風險摘要
```

Python helper 負責：

```text
- import edgar
- set_identity(process env SEC_EDGAR_IDENTITY)
- Company(symbol)
- get_filings / income_statement / balance_sheet / cash_flow_statement
- 將結果轉成 JSON 給 TypeScript
```

注意：

```text
edgartools 產生財報表時可能印出 data quality warnings。
正式整合時應將 stdout 僅保留 JSON，warnings 導到 stderr 或在 helper 中抑制。
```

---

# 九、輸出格式

因 pi terminal 對 Markdown 表格與 emoji 顯示可能錯位，建議使用：

```text
Unicode box-drawing table
```

例如：

```text
┌────┬──────┬────────────┬─────┬────────┬────────┬────────┬────────┐
│ #  │ 代碼 │ 到期       │ DTE │ Strike │ Delta  │ 年化   │ 風險   │
├────┼──────┼────────────┼─────┼────────┼────────┼────────┼────────┤
│  1 │ AAPL │ 2026-07-10 │  38 │ 300.00 │ -0.279 │ 13.4%  │ 財報後 │
└────┴──────┴────────────┴─────┴────────┴────────┴────────┴────────┘
```

避免使用：

```text
Markdown table
emoji
粗體 markdown
過長 headline 直接塞進表格
```

新聞標題應放在表格下方的 details 區塊。

---

# 十、一次完整實作範圍

`option-analysis` 不採分階段交付；第一版即完成文件中定義的全部工具與整合能力。

## 1. TypeScript extension 主體

```text
1. 建立 .pi/extensions/option-analysis.ts。
2. 註冊所有 pi tools。
3. 讀取必要環境變數。
4. 實作 Schwab / Finnhub client wrapper。
5. 實作 retry、rate limit、timeout、錯誤訊息格式化。
6. 實作 session cache，避免同一 symbol 重複呼叫 Finnhub / SEC。
7. 實作 Unicode table formatter。
```

## 2. Schwab market data 功能

```text
1. 實作 Schwab token refresh：每次執行 Schwab API tool 前，先 refresh access token 並更新 schwab_tokens.json。
2. 實作 /quotes client：option_analysis_quote。
3. 實作 /chains client：option_analysis_sell_put_scan。
4. 實作 /expirationchain client（必要時輔助 DTE / expiry 選擇）。
5. 實作 /pricehistory client：option_analysis_price_history。
6. 實作 market status 檢查，標記即時 / 延遲行情風險。
```

`option_analysis_sell_put_scan` 必須完成：

```text
- 支援一個或多個 symbols。
- 篩選 Put option chain。
- 計算 premium、spread、spread_pct。
- 計算 breakeven、cash_required、premium_income。
- 計算 cash_secured_return、annualized_return。
- 計算 OTM%、assignment_buffer、assignment_buffer_pct。
- 整合 earnings / news / profile / SEC / price history 摘要。
- 輸出候選合約表格與 details 區塊。
```

`option_analysis_price_history` 必須完成：

```text
- 5D / 20D / 60D return。
- 20D / 50D / 200D moving average。
- price vs moving averages。
- 20D / 60D high-low。
- 52W high-low（資料足夠時）。
- 20D / 60D realized volatility。
- recent drawdown from high。
- technical_context 標籤：near_support、extended_upside、below_50dma、high_realized_volatility。
```

## 3. Finnhub 事件與公司資料功能

```text
1. 實作 /calendar/earnings：option_analysis_earnings。
2. 實作 /stock/profile2：option_analysis_profile。
3. 實作 /company-news：option_analysis_news。
4. 實作 /stock/metric。
5. 實作 /stock/recommendation。
6. 實作 /stock/insider-sentiment。
7. 對 Finnhub 加入 20 calls/sec 保守 rate limit。
8. 對 HTTP 429 做 retry with backoff。
```

必須完成的風險判斷：

```text
- earnings_before_expiry。
- days_to_earnings。
- recent_news_count。
- latest_headlines。
- negative keyword event_risk。
- market cap / industry / basic metrics 摘要。
- analyst recommendation trend 摘要。
- insider sentiment 摘要。
```

## 4. SEC / edgartools 正式財報功能

```text
1. 建立 .pi/extensions/option-analysis/sec_edgar.py。
2. Python helper 只輸出 JSON，warnings 導到 stderr 或抑制。
3. TypeScript 透過 child_process 呼叫 Python helper。
4. 實作 option_analysis_sec_filings。
5. 實作 option_analysis_sec_financials。
```

`option_analysis_sec_filings` 必須輸出：

```text
- company name
- CIK
- SIC / industry
- latest 10-K / 10-Q / 8-K
- filing date
- accession number
- SEC URL
- 8-K item 摘要（若可抽取）
```

`option_analysis_sec_financials` 必須輸出：

```text
- revenue
- gross profit
- operating income
- net income
- EPS
- operating cash flow
- cash
- assets / liabilities / equity
- liabilities/assets
- revenue_trend
- net_income_trend
- operating_cash_flow_trend
- 風險標籤
```

## 5. 評分與風險整合

```text
1. 完成 liquidity_score。
2. 完成 safety_score。
3. 完成 return_score。
4. 完成 event_score。
5. 完成 quality_score。
6. 完成 total_score。
7. 輸出 score breakdown，而不只輸出總分。
```

風險標籤至少包含：

```text
earnings_risk
event_risk
technical_risk
profitability_risk
cash_flow_risk
balance_sheet_risk
sec_event_risk
liquidity_risk
spread_risk
```

## 6. 完整驗收標準

```text
option_analysis_sell_put_scan({ symbols: ["AAPL"] })
能回傳 30～45 DTE Sell Put 候選，包含合約報酬、流動性、breakeven、財報風險、技術位置與總分。

option_analysis_sell_put_scan({ symbols: ["AAPL", "AMD", "NVDA"] })
能跨多 ticker 比較候選合約並排序。

option_analysis_price_history({ symbols: ["AAPL", "AMD"], period: "6m" })
能輸出近期報酬、均線位置、區間高低點、realized volatility 與 technical_context。

option_analysis_earnings({ symbol: "AAPL" })
能輸出下一次財報日期與財報風險判斷。

option_analysis_news({ symbol: "AAOI", days: 7 })
能輸出最近新聞與事件風險關鍵字命中。

option_analysis_profile({ symbol: "AAPL" })
能輸出公司 profile、market cap、industry 與 basic metrics。

option_analysis_sec_filings({ symbol: "AAOI" })
能輸出最新 10-K、10-Q、8-K 與 SEC URL。

option_analysis_sec_financials({ symbol: "AAOI", period: "quarterly" })
能輸出最近季度財報摘要與風險標籤。
```

AAOI 實測結果應可重現：

```text
Q1 2026 revenue: $151.1M
Q1 2026 net income: -$14.3M
Q1 2026 cash: $439.7M
FY2025 operating cash flow: -$174.4M
latest 8-K: $600M ATM equity distribution agreement
```

---

# 十一、限制與風險

```text
1. Schwab access token 約 1800 秒過期；extension 應每次執行 Schwab API tool 前先 refresh token。
2. Schwab refresh token / client credentials 若失效，需重新走 OAuth 授權；HTTP 401 應明確提示重新授權。
3. Schwab API 有 120 requests/min 調用限制，extension 應保守限速，例如 100 requests/min，並處理 HTTP 429 / Retry-After。
4. Finnhub 免費 API 有額度限制。
5. Finnhub 新聞不是完整新聞源，缺資料不代表無事件。
6. Schwab option chain 欄位可能依權限與行情授權不同。
7. 即時 / 延遲行情需檢查 Schwab 回傳狀態。
8. 財報日期可能變動，應在下單前重新查詢。
9. edgartools 依賴 SEC EDGAR，必須設定 identity，且不可高頻打 SEC。
10. edgartools 財報表準化可能產生 data quality warnings；正式評分要保留人工覆核入口。
11. SEC 10-K / 10-Q 是正式申報，但不是即時新聞；8-K 才較接近重大事件公告。
12. 本 extension 只做分析，不構成投資建議。
```

---

# 十二、最終建議

`option-analysis` 第一版即應一次完成文件中定義的全部功能，不採 MVP 分階段交付。

完整版本應同時覆蓋：

```text
1. Schwab quote / chain / pricehistory。
2. Finnhub earnings / news / profile / metric / recommendation / insider sentiment。
3. SEC / edgartools filings 與正式財報摘要。
4. Sell Put 合約篩選、計算、評分與風險標籤。
5. 多 ticker 合約比較。
6. pi terminal 友善輸出。
```

目標是讓大模型在讀取 [[option-strategy]] 後，有足夠的 `option-analysis` tools 可以完成指定 ticker 的 Sell Put 策略判斷。
