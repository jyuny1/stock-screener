# Finnhub API - 搭配 Schwab Option 評估系統

來源頁面：

- https://finnhub.io/docs/api
- https://finnhub.io/docs/api/earnings-calendar
- https://finnhub.io/docs/api/company-profile
- https://finnhub.io/docs/api/news-sentiment
- https://finnhub.io/docs/api/recommendation-trends

整理日期：2026-06-02

> 本文件整理 Finnhub API 中可補足 Charles Schwab Trader API 不足的資料，重點是 Sell Put / 選擇權標的選擇系統需要的財報、公司分類、新聞、基本面與分析師資料。若 Finnhub 官方文件更新，應以官方文件為準。

---

# 一、結論

Finnhub 很適合與 Schwab API 搭配：

```text
Schwab：交易與行情資料主來源
- quote
- option chain
- Greeks
- bid / ask / mark
- volume / OI
- price history / MA / HV

Finnhub：公司與事件風險補強來源
- earnings calendar
- company profile
- sector / industry
- company news
- market news
- basic financial metrics
- analyst recommendation
```

對 Sell Put 系統最重要的是：

1. 用 Finnhub `/calendar/earnings` 補 Schwab 沒有的財報日期。
2. 用 Finnhub `/stock/profile2` 補 sector / industry / market cap。
3. 用 Finnhub `/company-news` 補近期重大新聞。
4. 用 Finnhub `/stock/metric` 補基本財務與 52 週位置。
5. 用 Finnhub `/stock/recommendation` 補分析師共識。

---

# 二、認證與基本調用

Base URL：

```text
https://finnhub.io/api/v1
```

認證方式二選一：

```bash
# URL query token
curl "https://finnhub.io/api/v1/stock/profile2?symbol=AAPL&token=YOUR_API_KEY"

# Header token
curl "https://finnhub.io/api/v1/stock/profile2?symbol=AAPL" \
  -H "X-Finnhub-Token: YOUR_API_KEY"
```

免費額度與可用範圍會隨 Finnhub 政策變動。根據目前文件與整理結果：

```text
部分 endpoint 免費可用。
部分 endpoint 有免費歷史範圍限制。
部分 endpoint 需要 Premium。
```

---

# 三、最適合搭配 Schwab 的 Endpoint

| 用途 | Endpoint | 對 Sell Put 系統的價值 | 免費狀態 |
|---|---|---|---|
| 財報日期 | `/calendar/earnings` | 判斷合約到期日前是否踩財報 | 免費，約 1 個月歷史 + upcoming |
| 歷史 EPS surprise | `/stock/earnings` | 評估財報波動與 surprise 風險 | 免費近 4 季 |
| 公司基本資料 | `/stock/profile2` | 補 sector / industry / market cap | 免費 |
| 公司新聞 | `/company-news` | 檢查近期重大新聞 | 免費，北美公司 |
| 市場新聞 | `/news` | 檢查大盤事件與市場情緒 | 免費 |
| 基本財務指標 | `/stock/metric` | P/E、EPS、52W high/low 等 | 免費 |
| 同業公司 | `/stock/peers` | 同產業比較、候選池擴展 | 免費 |
| 分析師推薦 | `/stock/recommendation` | 補市場共識風險 | 免費 |
| Insider sentiment | `/stock/insider-sentiment` | 補內部人交易情緒 | 免費 |
| News sentiment | `/news-sentiment` | 新聞情緒分數 | 需 Premium |
| EPS estimate | `/stock/eps-estimate` | EPS 預估 | 需 Premium |
| Price target | `/stock/price-target` | 分析師目標價 | 需 Premium |
| Upgrade / downgrade | `/stock/upgrade-downgrade` | 評級升降 | 需 Premium |

---

# 四、財報資料

## 1. Earnings Calendar

Endpoint：

```text
GET /calendar/earnings
```

用途：取得歷史與即將公布的財報日期、EPS 與營收資料。

官方描述重點：

```text
Get historical and coming earnings release.
EPS and Revenue are non-GAAP adjusted numbers.
Estimates are sourced from sell-side and buy-side analysts.
```

常用參數：

| 參數 | 必填 | 說明 |
|---|---:|---|
| `from` | 否 | 起始日期，格式 yyyy-MM-dd |
| `to` | 否 | 結束日期，格式 yyyy-MM-dd |
| `symbol` | 否 | 股票代號，例如 AAPL |
| `international` | 否 | 是否包含國際股票 |

範例：

```bash
curl "https://finnhub.io/api/v1/calendar/earnings?from=2026-06-01&to=2026-07-31&symbol=AAPL&token=YOUR_API_KEY"
```

常見回傳欄位：

```text
symbol
date
hour
quarter
year
epsEstimate
epsActual
revenueEstimate
revenueActual
```

對 Sell Put 系統用途：

```text
1. 找出候選合約持有期間是否包含財報。
2. 若財報日在 option expiry 前 7～14 天內，標記 earnings risk。
3. 若策略不做事件交易，可直接排除。
4. 若保留，應降低評分或提高要求的報酬 / 安全邊際。
```

建議規則：

```text
earnings_before_expiry = earnings_date <= option_expiry
days_to_earnings = earnings_date - today

若 0 <= days_to_earnings <= 14：高財報風險
若 earnings_date <= option_expiry：合約持有期間踩財報
若 earnings_date 在 option_expiry 後：財報風險較低
```

## 2. Historical Earnings / EPS Surprise

Endpoint：

```text
GET /stock/earnings
```

用途：取得公司歷史季度 earnings surprise。

免費狀態：

```text
免費近 4 季；更長歷史可能受限制。
```

參數：

| 參數 | 必填 | 說明 |
|---|---:|---|
| `symbol` | 是 | 股票代號 |
| `limit` | 否 | 回傳筆數 |

範例：

```bash
curl "https://finnhub.io/api/v1/stock/earnings?symbol=AAPL&limit=4&token=YOUR_API_KEY"
```

對 Sell Put 系統用途：

```text
1. 觀察公司過去 EPS surprise 是否波動大。
2. 若過去財報常大幅 surprise，財報前 Sell Put 應更保守。
3. 可作為 earnings_risk_score 的補充因素。
```

---

# 五、公司 Profile 與分類

## Company Profile 2

Endpoint：

```text
GET /stock/profile2
```

用途：取得公司基本資料。可用 symbol、ISIN 或 CUSIP 查詢。

參數：

| 參數 | 必填 | 說明 |
|---|---:|---|
| `symbol` | 否 | 股票代號 |
| `isin` | 否 | ISIN |
| `cusip` | 否 | CUSIP |

範例：

```bash
curl "https://finnhub.io/api/v1/stock/profile2?symbol=AAPL&token=YOUR_API_KEY"
```

常見回傳欄位：

```text
name
ticker
country
currency
exchange
finnhubIndustry
marketCapitalization
shareOutstanding
logo
weburl
```

對 Schwab 的補強：

Schwab Market Data 有 instrument / reference / fundamental，但沒有明確提供完整 company profile，例如：

```text
sector
industry
market cap
website
country
logo
```

Finnhub `/stock/profile2` 可補：

```text
sector / industry proxy：finnhubIndustry
market cap：marketCapitalization
country
currency
exchange
weburl
```

對 Sell Put 系統用途：

```text
1. 市值過小排除。
2. 產業集中度控管。
3. 區分大型股、ETF、ADR、特殊標的。
4. 對同業標的做相對比較。
```

---

# 六、新聞與事件風險

## 1. Company News

Endpoint：

```text
GET /company-news
```

用途：取得特定公司近期新聞。官方文件指出此 endpoint 主要適用北美公司。

參數：

| 參數 | 必填 | 說明 |
|---|---:|---|
| `symbol` | 是 | 股票代號 |
| `from` | 是 | 起始日期 yyyy-MM-dd |
| `to` | 是 | 結束日期 yyyy-MM-dd |

範例：

```bash
curl "https://finnhub.io/api/v1/company-news?symbol=AAPL&from=2026-06-01&to=2026-06-02&token=YOUR_API_KEY"
```

常見回傳欄位：

```text
category
datetime
headline
id
image
related
source
summary
url
```

對 Sell Put 系統用途：

```text
1. 顯示候選標的最近新聞標題。
2. 若近期新聞量異常增加，標記 event risk。
3. 若標題包含 investigation、lawsuit、FDA、guidance、SEC 等關鍵字，標記高風險。
4. 不建議只靠新聞文字自動下單，但可作為人工審查提示。
```

## 2. Market News

Endpoint：

```text
GET /news
```

用途：取得市場新聞。

參數：

| 參數 | 必填 | 說明 |
|---|---:|---|
| `category` | 是 | 市場類別，例如 general、forex、crypto、merger |
| `minId` | 否 | 用於分頁 / 增量抓取 |

範例：

```bash
curl "https://finnhub.io/api/v1/news?category=general&token=YOUR_API_KEY"
```

對 Sell Put 系統用途：

```text
1. 補充市場大環境事件。
2. 若市場有宏觀風險新聞，可降低整體 Sell Put 掃描分數。
3. 可搭配 Schwab 的 $SPX / $VIX / QQQ price history 做 regime filter。
```

## 3. News Sentiment

Endpoint：

```text
GET /news-sentiment
```

狀態：

```text
Premium Access Required
```

用途：取得公司新聞情緒與統計。

參數：

| 參數 | 必填 | 說明 |
|---|---:|---|
| `symbol` | 是 | 股票代號 |

可用欄位概念：

```text
bullish / bearish percentages
sentiment score
buzz rate
industry comparison
```

對 Sell Put 系統用途：

```text
若未來有 Premium，可直接將 sentiment score、buzz score 納入事件風險評分。
免費版可先用 /company-news 的 headline + count 做簡化替代。
```

---

# 七、基本財務與品質資料

## 1. Basic Financials

Endpoint：

```text
GET /stock/metric
```

用途：取得公司基本財務指標，例如 P/E、EPS、市值、52 週高低點等。

參數：

| 參數 | 必填 | 說明 |
|---|---:|---|
| `symbol` | 是 | 股票代號 |
| `metric` | 是 | 指標集合，例如 `all` |

範例：

```bash
curl "https://finnhub.io/api/v1/stock/metric?symbol=AAPL&metric=all&token=YOUR_API_KEY"
```

對 Sell Put 系統用途：

```text
1. 補 Schwab fundamental 欄位不足。
2. 估算 52 週位置。
3. 基本品質與估值初篩。
4. 輔助判斷是否願意被指派接股。
```

可納入評分的概念欄位：

```text
pe ratio
eps
market cap
52 week high / low
revenue growth
profitability metrics
leverage metrics
```

> 實際欄位名稱需依 Finnhub 回傳 JSON 確認。

## 2. Company Peers

Endpoint：

```text
GET /stock/peers
```

用途：取得同國家、同 sector / industry 的 peers。

參數：

| 參數 | 必填 | 說明 |
|---|---:|---|
| `symbol` | 是 | 股票代號 |
| `grouping` | 否 | 分組方式 |

範例：

```bash
curl "https://finnhub.io/api/v1/stock/peers?symbol=AAPL&token=YOUR_API_KEY"
```

對 Sell Put 系統用途：

```text
1. 建立候選股票池。
2. 對同業標的做相對比較。
3. 避免只看單一 ticker，擴展到同產業更好的標的。
```

---

# 八、分析師與內部人資料

## 1. Analysts Recommendation Trends

Endpoint：

```text
GET /stock/recommendation
```

用途：取得分析師推薦趨勢。

參數：

| 參數 | 必填 | 說明 |
|---|---:|---|
| `symbol` | 是 | 股票代號 |

範例：

```bash
curl "https://finnhub.io/api/v1/stock/recommendation?symbol=AAPL&token=YOUR_API_KEY"
```

常見欄位概念：

```text
period
strongBuy
buy
hold
sell
strongSell
```

對 Sell Put 系統用途：

```text
1. 分析師共識惡化時降低標的分數。
2. 強賣 / 賣出數量增加時標記風險。
3. 不應單獨作為交易依據，只作為風險補充。
```

## 2. Insider Sentiment

Endpoint：

```text
GET /stock/insider-sentiment
```

用途：取得美股公司內部人交易情緒。

參數：

| 參數 | 必填 | 說明 |
|---|---:|---|
| `symbol` | 是 | 股票代號 |
| `from` | 是 | 起始日期 yyyy-MM-dd |
| `to` | 是 | 結束日期 yyyy-MM-dd |

範例：

```bash
curl "https://finnhub.io/api/v1/stock/insider-sentiment?symbol=AAPL&from=2026-01-01&to=2026-06-01&token=YOUR_API_KEY"
```

官方描述重點：

```text
MSPR ranges from -100 to 100.
May signal price changes in coming 30-90 days.
```

對 Sell Put 系統用途：

```text
1. 內部人大量賣出時降低信心。
2. 可作為公司風險補充分數。
3. 不應單獨作為排除條件。
```

---

# 九、Premium 但有價值的 Endpoint

以下 endpoint 對 Sell Put 風險評估有價值，但目前官方文件顯示需要 Premium：

| Endpoint | 用途 | 狀態 |
|---|---|---|
| `/news-sentiment` | 公司新聞情緒與 buzz 指標 | Premium |
| `/stock/eps-estimate` | EPS 預估 | Premium |
| `/stock/price-target` | 分析師目標價 | Premium |
| `/stock/upgrade-downgrade` | 分析師升降評 | Premium |
| `/stock/filings-sentiment` | SEC 10-K / 10-Q 情緒分析 | Premium |
| `/sector/metrics` | sector / region 指標 | Premium |
| `/press-releases` | 公司重大 press releases | Premium |

MVP 不應依賴這些 endpoint。

---

# 十、與 schwab-option extension 的整合設計

## 1. 資料分工

```text
Schwab Market Data:
- underlying quote
- option chain
- Greeks
- bid / ask / mark
- OI / volume
- price history
- market hours

Finnhub:
- earnings calendar
- historical earnings surprise
- company profile
- sector / industry / market cap
- company news
- market news
- basic financial metrics
- analyst recommendations
- insider sentiment
```

## 2. 建議新增風險欄位

```text
earnings_risk:
  earnings_date
  days_to_earnings
  earnings_before_expiry
  earnings_risk_level

company_profile:
  market_cap
  industry
  country
  exchange
  currency

company_quality:
  pe_ratio
  eps
  52w_position
  basic_metric_source

event_risk:
  recent_news_count
  latest_headlines
  event_keywords

analyst_risk:
  strong_buy
  buy
  hold
  sell
  strong_sell

insider_risk:
  mspr
  insider_sentiment_period
```

## 3. Sell Put 評分建議

```text
若財報在合約到期日前：
  - earnings_risk_level = high
  - 排除或大幅降分

若財報距今天 0～14 天：
  - 高風險

若 market cap 過小：
  - 排除或降分

若 recent news 中出現重大負面關鍵字：
  - event risk 提醒

若 analyst recommendation 明顯惡化：
  - 降分

若 insider sentiment 明顯負面：
  - 降分或提示
```

## 4. MVP 實作順序

第一階段：

```text
1. schwab_sell_put_scan：Schwab 取得 option chain 與 quote
2. finnhub_earnings_calendar：Finnhub 取得財報日期
3. 合約表格新增 earnings 欄位與風險標記
```

第二階段：

```text
1. finnhub_company_profile：補 industry / market cap
2. finnhub_company_news：補近期新聞
3. 掃描結果加上 company risk / event risk
```

第三階段：

```text
1. finnhub_stock_metric：補基本面品質
2. finnhub_recommendation：補分析師共識
3. finnhub_insider_sentiment：補內部人交易情緒
```

---

# 十一、注意事項

```text
1. Finnhub 免費資料足夠 MVP，但正式策略應檢查 endpoint 是否穩定與額度是否足夠。
2. 財報日期是 Sell Put 很重要的風險因子，建議優先整合。
3. Finnhub 的新聞與基本面資料應作為風險提示，不應單獨觸發下單。
4. Schwab 與 Finnhub 的 symbol / exchange / asset type 可能有差異，系統應做 symbol normalization。
5. ETF、ADR、基金、特殊股可能沒有完整 company profile 或財報資料。
6. 若 Finnhub endpoint 回傳空值，不代表標的無風險，只代表資料源沒有資料。
```
