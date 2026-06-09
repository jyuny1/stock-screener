# Schwab Trader API - Market Data Production

來源頁面：https://developer.schwab.com/products/trader-api--individual/details/documentation/Market%20Data%20Production?docId=market-data-production--trader-api--individual--documentation

擷取日期：2026-06-02

> 本文件由 Schwab Developer Portal 頁面擷取整理。內容包含 Specifications 與 Documentation 兩個分頁的文字。若 Schwab 官方文件更新，應以官方頁面為準。

---

# 一、跟美股期權相關的重點整理

## 1. REST Market Data API

Base URL：

```text
https://api.schwabapi.com/marketdata/v1
```

跟 Sell Put 篩選器最相關的 endpoint：

| 功能 | Endpoint | 用途 |
|---|---|---|
| Quotes | `GET /quotes`、`GET /{symbol_id}/quotes` | 查股票或特定 option contract 的 quote |
| Option Chains | `GET /chains` | 查某標的的選擇權鏈，包含 Put / Call、bid / ask、Greeks、IV、OI、Volume 等 |
| Option Expiration Chain | `GET /expirationchain` | 查某標的可用到期日與 DTE |
| PriceHistory | `GET /pricehistory` | 查標的歷史價格，用於 HV、均線、趨勢判斷 |
| MarketHours | `GET /markets`、`GET /markets/{market_id}` | 查市場時間，避免非交易時間誤判 quote |
| Instruments | `GET /instruments`、`GET /instruments/{cusip_id}` | 查商品 / symbol / CUSIP 資訊 |

## 2. `/chains` 對 Sell Put 最重要

用途：取得某個 optionable symbol 的 option chain。

常用參數：

```text
symbol：標的，例如 AAPL、SPY
contractType：CALL / PUT / ALL；Sell Put 主要用 PUT
strikeCount：回傳 ATM 上下多少履約價
includeUnderlyingQuote：是否包含 underlying quote
strategy：SINGLE / ANALYTICAL / COVERED / VERTICAL / CALENDAR / STRANGLE / STRADDLE / BUTTERFLY / CONDOR / DIAGONAL / COLLAR / ROLL
range：ITM / NTM / OTM 等
fromDate：起始到期日 yyyy-MM-dd
toDate：結束到期日 yyyy-MM-dd
expMonth：JAN～DEC 或 ALL
optionType：Option Type
entitlement：PN / NP / PP
```

`strategy=ANALYTICAL` 時可搭配：

```text
volatility
underlyingPrice
interestRate
daysToExpiration
```

## 3. `/chains` 回傳中可用於 Sell Put 的欄位

```text
putCall
symbol
description
exchangeName
bidPrice
askPrice
lastPrice
markPrice
bidSize
askSize
lastSize
highPrice
lowPrice
openPrice
closePrice
totalVolume
quoteTimeInLong
tradeTimeInLong
netChange
volatility
delta
gamma
theta
vega
rho
timeValue
openInterest
isInTheMoney
theoreticalOptionValue
theoreticalVolatility
strikePrice
expirationDate
daysToExpiration
expirationType
lastTradingDay
multiplier
settlementType
isIndexOption
percentChange
markChange
markPercentChange
intrinsicValue
optionRoot
```

## 4. `/expirationchain`

用途：只取某標的可用到期日，不包含 individual options contracts。

回傳欄位範例：

```text
expirationDate
daysToExpiration
expirationType
standard
```

## 5. `/quotes` 可以查 option symbol

`GET /quotes` 的 `symbols` 可帶股票、指數、基金、期權 symbol 等。官方範例包含：

```text
AMZN  230317C01360000
DJX   231215C00290000
```

Option quote 回傳中可用：

```text
assetMainType = OPTION
reference.contractType
reference.daysToExpiration
reference.expirationDay / Month / Year
reference.multiplier
reference.settlementType
reference.strikePrice
reference.underlying
quote.bidPrice
quote.askPrice
quote.lastPrice
quote.mark
quote.delta
quote.gamma
quote.theta
quote.vega
quote.rho
quote.volatility
quote.openInterest
quote.totalVolume
quote.theoreticalOptionValue
quote.underlyingPrice
quote.moneyIntrinsicValue
quote.timeValue
```

## 6. Streamer API 中與期權有關的 service

| Service Name | Description | Delivery Type |
|---|---|---|
| `LEVELONE_OPTIONS` | Level 1 Options | Change |
| `OPTIONS_BOOK` | Level Two book for Options | Whole |
| `SCREENER_OPTION` | Advances and Decliners for Options | Whole |
| `LEVELONE_FUTURES_OPTIONS` | Level 1 Futures Options | Change |

Sell Put MVP 建議先用 REST API，不必一開始做 streaming。

## 7. 建議 Sell Put MVP 呼叫流程

```text
1. 用 /expirationchain 取得 symbol 的所有到期日
2. 篩 30～45 DTE
3. 用 /chains?symbol=XXX&contractType=PUT&fromDate=...&toDate=... 抓 Put chain
4. 用 delta、OTM、openInterest、totalVolume、bid/ask spread 篩合約
5. 計算 breakeven、cash_required、annualized_return、assignment_risk
6. 必要時用 /quotes 補單一合約最新 quote
7. 用 /pricehistory 補 HV、均線、趨勢與支撐
```

## 8. 查單一股票 tick / 交易量 / 最近情況

Schwab Market Data 可查「即時 quote、當日總量、歷史 K 線、即時 streaming 更新」，但本頁沒有看到完整逐筆成交明細歷史 tick-by-tick endpoint。

可用資料分三層：

| 需求 | Endpoint / Service | 可取得內容 | 適合用途 |
|---|---|---|---|
| 單一股票目前狀態 | `GET /quotes` 或 `GET /{symbol_id}/quotes` | last、bid、ask、mark、open、high、low、close、totalVolume、tradeTime、quoteTime、netChange、percentChange | 查某檔股票今天交易量與即時狀態 |
| 單一股票近期走勢 | `GET /pricehistory` | candles：open、high、low、close、volume、datetime | 算最近 N 天量價、均線、HV、支撐壓力 |
| 市場異動清單 | `GET /movers/{symbol_id}` | 指數或市場中的 top movers，可依 VOLUME、TRADES、PERCENT_CHANGE_UP、PERCENT_CHANGE_DOWN 排序 | 找當天異常成交量或漲跌異動標的 |
| 即時股票 tick-like 更新 | Streamer `LEVELONE_EQUITIES` | bid、ask、last、totalVolume、high、low、netChange、quoteTime、tradeTime 等變動推送 | 盤中監控，不一定是完整逐筆成交 |
| 即時 K 線更新 | Streamer `CHART_EQUITY` | chart candle for equities | 盤中即時 K 線 / 分鐘資料監控 |

### `/quotes` 可用於單股監控的欄位

`GET /quotes?symbols=AAPL&fields=quote,reference,regular,fundamental` 可用來查單一股票或多檔股票。

重點欄位：

```text
assetMainType
symbol
quoteType
realtime
reference.description
reference.exchangeName
quote.bidPrice
quote.askPrice
quote.lastPrice
quote.mark
quote.openPrice
quote.highPrice
quote.lowPrice
quote.closePrice
quote.totalVolume
quote.tradeTime
quote.quoteTime
quote.netChange
quote.netPercentChange
quote.markChange
quote.markPercentChange
quote.securityStatus
quote.volatility
regular.regularMarketLastPrice
regular.regularMarketNetChange
regular.regularMarketPercentChange
fundamental.avg10DaysVolume
fundamental.avg1YearVolume
fundamental.peRatio
fundamental.divYield
```

對 Sell Put 股票層可計算：

```text
當日量 = quote.totalVolume
相對 10 日均量 = quote.totalVolume / fundamental.avg10DaysVolume
當日漲跌幅 = quote.netPercentChange
bid_ask_spread = quote.askPrice - quote.bidPrice
bid_ask_spread_pct = (ask - bid) / mark
是否停牌 / 異常 = quote.securityStatus
```

### `/pricehistory` 可用於近期走勢

`GET /pricehistory` 回傳歷史 OHLCV candles。

常用參數：

```text
symbol：AAPL
periodType：day / month / year / ytd
period：區間長度
frequencyType：minute / daily / weekly / monthly
frequency：1、5、10、15、30 分鐘或 daily 等
startDate / endDate：epoch milliseconds
needExtendedHoursData：是否包含盤前盤後
needPreviousClose：是否回傳 previous close
```

回傳 candle 欄位：

```text
open
high
low
close
volume
datetime
```

對 Sell Put 股票層可計算：

```text
avg_volume_20d / avg_volume_30d
volume_ratio = today_volume / avg_volume_20d
historical_volatility_20d / 30d
MA20 / MA50 / MA200
距離 52 週高低點
最近 5 / 20 / 60 日報酬
是否跌破支撐或均線
```

### `/movers/{symbol_id}` 可用於找市場異動

可用 `symbol_id`：

```text
$DJI
$COMPX
$SPX
NYSE
NASDAQ
OTCBB
INDEX_ALL
EQUITY_ALL
OPTION_ALL
OPTION_PUT
OPTION_CALL
```

可用排序：

```text
VOLUME
TRADES
PERCENT_CHANGE_UP
PERCENT_CHANGE_DOWN
```

用途：

```text
1. 找當日成交量異常股票
2. 找大漲 / 大跌股票，提醒不要只因 IV 高就 Sell Put
3. 找期權市場異動，例如 OPTION_PUT / OPTION_CALL
```

### Streamer `LEVELONE_EQUITIES` 可用於盤中監控

常用欄位編號：

```text
0 Symbol
1 Bid Price
2 Ask Price
3 Last Price
4 Bid Size
5 Ask Size
8 Total Volume
9 Last Size
10 High Price
11 Low Price
12 Close Price
17 Open Price
18 Net Change
29 Regular Market Last Price
31 Regular Market Net Change
32 Security Status
33 Mark Price
34 Quote Time in Long
35 Trade Time in Long
42 Net Percent Change
43 Regular Market Percent Change
50 Post-Market Net Change
51 Post-Market Percent Change
```

適合：

```text
盤中監控候選股票是否急跌
監控成交量是否突然放大
監控 quote 是否延遲 delayed
監控 securityStatus 是否 Halted / Closed / Normal
```

### 結論

```text
如果只想知道單一股票交易量與最近情況：
優先用 /quotes + /pricehistory。

如果想找全市場異動：
用 /movers/{symbol_id}。

如果想盤中即時監控：
再加 Streamer LEVELONE_EQUITIES / CHART_EQUITY。

如果想要完整逐筆成交 tick-by-tick 歷史：
本頁文件沒有明確提供，需另查 Schwab 是否有其他 tick data 產品，或改用專門資料商。
```

## 9. Streamer API 使用限制與注意事項

Schwab Streamer API 可以用 WebSocket 串流 market data / account activity，但文件中有幾個重要限制。

### 明確寫在文件中的限制

| 限制 | 文件線索 | 影響 |
|---|---|---|
| 單一使用者同時只能有 1 條 streamer connection | Response code `12 CLOSE_CONNECTION`：`A limit of 1 Streamer connection at any given time from a given user is available.` | 不能同時開多個程式各自連 streamer；應集中成一個 streaming service |
| 有訂閱 symbol 數量上限 | Response code `19 REACHED_SYMBOL_LIMIT`：Subscribe / Add 達到 total subscription symbol limit | 不能無限制訂閱全市場；候選池要先縮小 |
| 可能因 inactivity / slowness 被停止 | Response code `30 STOP_STREAMING`：terminated due to administrator action, inactivity, or slowness | client 要處理斷線、重連、heartbeat |
| 必須先 LOGIN 成功再 SUBS / ADD | `STREAM_CONN_NOT_FOUND` 常見原因包含 LOGIN 尚未成功就送 SUBS | 實作時要等 login response code 0 |
| 同時處理多個 command 可能失敗 | `FAILED_COMMAND_SUBS` 常見原因：two or more commands are processed in parallel | SUBS / ADD / VIEW 應序列化處理，不要並發亂送 |
| entitlement 會影響即時 / 延遲資料 | login response status：PN / NP / PP；若無 entitlement 會拿 NFL / delayed quotes | 需要檢查 `delayed` 欄位，不可假設都是即時 |
| 某些服務可能不可用 | `SERVICE_NOT_AVAILABLE` | 依帳戶權限與 Schwab 當時服務狀態而定 |

### Entitlement / delayed 資料注意

文件提到 streamer 會回傳：

```text
key
 delayed
 assetMainType
 assetSubType
 cusip
```

其中 `delayed` 很重要：

```text
delayed = false：資料來自 SIP，較接近即時 consolidated market data
delayed = true：資料來自 NFL，可能是延遲資料，或只包含部分交易所即時資料
```

文件說明：

```text
NFL 可能代表 options、futures、futures options 的延遲資料；
也可能代表 equity 是部分交易所即時資料，不一定包含全市場 NBBO。
```

對 Sell Put 系統的影響：

```text
1. 若 delayed=true，不應拿來做精準盤中下單決策。
2. 若只做日更篩選或候選監控，延遲資料仍可能可用。
3. 每次接收 streamer 資料都應記錄 delayed 狀態。
```

### 對本系統的建議

```text
MVP：先不用 Streamer，用 REST /quotes + /pricehistory + /chains 即可。

進階監控：
只對已篩出的少量 watchlist 開 LEVELONE_EQUITIES / LEVELONE_OPTIONS。

不要：
用 Streamer 訂閱整個股票池或全市場期權鏈。
```

建議架構：

```text
1. REST API 每日 / 每小時產生候選清單
2. Streamer 只監控 Top N 候選標的與持倉
3. 單一程式集中管理 WebSocket connection
4. 實作 heartbeat、reconnect、resubscribe
5. 所有資料寫入時記錄 delayed / timestamp
```

## 10. Schwab 是否提供 ticker 的行業分類

結論：根據目前 Market Data Production 文件，Schwab API 有提供 symbol / instrument / quote / fundamental 資料，但沒有明確提供可直接用於股票池分類的 `sector` / `industry` / `GICS` / `SIC` / `NAICS` 欄位。

Schwab 可提供的分類較偏「資產類型」，不是「公司行業分類」：

```text
assetMainType：EQUITY、ETF、OPTION、INDEX、MUTUAL_FUND 等
assetSubType：ADR、CEF、COE、ETF、ETN、PRF 等
exchange / exchangeName：NASDAQ、NYSE 等
cusip
symbol
description
```

相關 endpoint：

| Endpoint / Service | 可取得內容 | 是否等於行業分類 |
|---|---|---|
| `GET /quotes` | quote、reference、regular、fundamental | 否；fundamental 偏成交量、估值、股息等，不是 sector / industry |
| `GET /instruments` | symbol、CUSIP、description、exchange、assetType；`projection=fundamental` 可取更細資料 | 文件中沒有明確 sector / industry |
| Streamer `LEVELONE_EQUITIES` | assetMainType、assetSubType、cusip、quote fields | 否；只適合即時行情監控 |

對本系統的設計建議：

```text
Schwab：負責行情、期權鏈、價格歷史、帳戶相關資料
FMP / Finnhub / yfinance：負責 sector / industry / SIC / NAICS 分類
```

建議資料表合併方式：

```text
symbol 作為主 key
Schwab 欄位：price、volume、option_chain、greeks、quote_time
外部分類欄位：sector、industry、sub_industry、sic_code、classification_source
```

實務結論：

```text
不要期待 Schwab 直接幫你按行業分類掃 ticker。
應先從 FMP / Finnhub / yfinance 建立股票池與行業分類，再用 Schwab 查 quote / pricehistory / option chain。
```

## 11. REST API 要如何調用

> 這裡的 REST API 是指 HTTP request / response，不是 Streamer WebSocket。

### 10.0 如何取得 OAuth access token

來源文件：[Retail Trader API Production / Accounts and Trading Production](https://developer.schwab.com/products/trader-api--individual/details/documentation/Retail%20Trader%20API%20Production?docId=retail-trader-api-production--trader-api--individual--documentation)。

Schwab Trader API 使用 OAuth 2 authorization_code grant，也就是 three-legged OAuth。流程是：

```text
1. 建立 / 取得 Schwab Developer App
2. 用 authorization URL 讓使用者登入 Schwab 並同意授權
3. 從 callback URL 取得 authorization code
4. 用 authorization code 換 access_token + refresh_token
5. 用 access_token 呼叫 Market Data / Account / Order API
6. access_token 過期後，用 refresh_token 換新的 access_token
7. refresh_token 過期後，重新走一次使用者授權流程
```

#### Token 有效期

```text
access_token：30 分鐘
refresh_token：7 天
```

注意：

```text
refresh_token 過期或失效後，不能再 refresh。
必須重新從 Step 1 authorization URL 開始，讓使用者再次登入與授權。
```

#### Step 1：產生 authorization URL

格式：

```text
https://api.schwabapi.com/v1/oauth/authorize?client_id={CLIENT_ID}&redirect_uri={APP_CALLBACK_URL}
```

範例：

```text
https://api.schwabapi.com/v1/oauth/authorize?client_id=YOUR_CLIENT_ID&redirect_uri=https://127.0.0.1
```

使用方式：

```text
1. 在瀏覽器打開 authorization URL
2. 登入 Schwab
3. 選擇 / 同意授權帳戶
4. Schwab 會 redirect 到 callback URL
5. 即使頁面顯示 404，也要從網址列複製 code 參數
```

redirect 後網址會像：

```text
https://127.0.0.1/?code={AUTHORIZATION_CODE}&session={SESSION_ID}
```

重要：

```text
code 在送 token request 前要 URL decode。
例如結尾若是 %40，實際應轉成 @。
```

#### Step 2：用 authorization code 換 token

Endpoint：

```text
POST https://api.schwabapi.com/v1/oauth/token
```

Header：

```http
Authorization: Basic {BASE64_ENCODED_CLIENT_ID_COLON_CLIENT_SECRET}
Content-Type: application/x-www-form-urlencoded
```

`Basic` 的值是：

```text
base64("CLIENT_ID:CLIENT_SECRET")
```

cURL 範例：

```bash
curl -X POST "https://api.schwabapi.com/v1/oauth/token" \
  -H "Authorization: Basic <BASE64_CLIENT_ID_COLON_CLIENT_SECRET>" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "code=<AUTHORIZATION_CODE>" \
  -d "redirect_uri=https://127.0.0.1"
```

成功回傳範例：

```json
{
  "expires_in": 1800,
  "token_type": "Bearer",
  "scope": "api",
  "refresh_token": "<REFRESH_TOKEN>",
  "access_token": "<ACCESS_TOKEN>",
  "id_token": "<JWT>"
}
```

#### Step 3：用 access token 呼叫 API

之後所有 REST API 都用：

```http
Authorization: Bearer <ACCESS_TOKEN>
```

#### Swagger UI Authorization 資訊

Schwab Developer 文件頁面內的 Swagger UI 使用：

```text
Authorization type：oauth
OAuth type：OAuth2, authorizationCode
Authorization URL：https://api.schwabapi.com/v1/oauth/authorize
Token URL：https://api.schwabapi.com/v1/oauth/token
Flow：authorizationCode
Scope：readonly
Redirect URI：https://developer.schwab.com/oauth2-redirect.html
```

注意：

```text
不要把 access_token、refresh_token、client_secret 寫進 Obsidian。
如果 token 曾經貼到聊天或筆記，應視為已暴露，建議登出 Swagger UI 或重新 refresh / revoke。
```

Swagger UI 產生的 quotes request 範例格式：

```bash
curl -X 'GET' \
  'https://api.schwabapi.com/marketdata/v1/quotes?symbols=AAPL,BAC,$DJI,$SPX&fields=quote,reference&indicative=false' \
  -H 'accept: application/json' \
  -H 'Authorization: Bearer <ACCESS_TOKEN>'
```

其中：

```text
symbols：可放多個 symbol，用逗號分隔；URL 中逗號會被編碼成 %2C
fields：可指定 quote、reference、regular、fundamental、extended 或 all
indicative=false：不額外回傳 ETF indicative quote
```

範例：

```bash
curl -X GET "https://api.schwabapi.com/marketdata/v1/quotes?symbols=AAPL" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Accept: application/json"
```

#### Step 4：用 refresh token 更新 access token

Endpoint：

```text
POST https://api.schwabapi.com/v1/oauth/token
```

cURL 範例：

```bash
curl -X POST "https://api.schwabapi.com/v1/oauth/token" \
  -H "Authorization: Basic <BASE64_CLIENT_ID_COLON_CLIENT_SECRET>" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=refresh_token" \
  -d "refresh_token=<REFRESH_TOKEN>"
```

成功後會取得新的：

```text
access_token
refresh_token
```

建議每次 refresh 成功後，都覆蓋保存新的 refresh_token。

#### Python 範例：產生 Basic auth 字串

```python
import base64

client_id = "YOUR_CLIENT_ID"
client_secret = "YOUR_CLIENT_SECRET"
raw = f"{client_id}:{client_secret}"
basic = base64.b64encode(raw.encode()).decode()
print(basic)
```

#### Python 範例：authorization code 換 token

```python
import base64
import requests

CLIENT_ID = "YOUR_CLIENT_ID"
CLIENT_SECRET = "YOUR_CLIENT_SECRET"
CODE = "AUTHORIZATION_CODE_FROM_CALLBACK"
REDIRECT_URI = "https://127.0.0.1"

basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

resp = requests.post(
    "https://api.schwabapi.com/v1/oauth/token",
    headers={
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    },
    data={
        "grant_type": "authorization_code",
        "code": CODE,
        "redirect_uri": REDIRECT_URI,
    },
)
print(resp.status_code)
print(resp.json())
```

#### Python 範例：refresh token

```python
import base64
import requests

CLIENT_ID = "YOUR_CLIENT_ID"
CLIENT_SECRET = "YOUR_CLIENT_SECRET"
REFRESH_TOKEN = "YOUR_REFRESH_TOKEN"

basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

resp = requests.post(
    "https://api.schwabapi.com/v1/oauth/token",
    headers={
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    },
    data={
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    },
)
print(resp.status_code)
print(resp.json())
```

#### 對本系統的實務建議

```text
1. 把 client_secret、access_token、refresh_token 放在 .env 或系統 keychain，不要寫入 Obsidian。
2. access_token 只有 30 分鐘，程式呼叫 API 前要檢查是否過期。
3. refresh_token 只有 7 天，若要長期使用，至少每幾天執行一次 refresh。
4. 若 refresh 失敗，提示使用者重新走 authorization URL。
5. Sell Put MVP 初期只需要 Market Data 權限，不要急著做下單。
```

---

### 基本呼叫結構

Schwab Market Data REST API 的 base URL：

```text
https://api.schwabapi.com/marketdata/v1
```

每次呼叫都要帶 OAuth access token：

```http
Authorization: Bearer <ACCESS_TOKEN>
Accept: application/json
```

基本格式：

```bash
curl -X GET "https://api.schwabapi.com/marketdata/v1/<endpoint>?參數=值" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Accept: application/json"
```

### 1. 查單一股票 quote

用途：查現價、bid / ask、當日成交量、漲跌幅。

```bash
curl -X GET "https://api.schwabapi.com/marketdata/v1/quotes?symbols=AAPL&fields=quote,reference,regular,fundamental" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Accept: application/json"
```

重要欄位：

```text
quote.lastPrice
quote.bidPrice
quote.askPrice
quote.mark
quote.totalVolume
quote.netPercentChange
quote.tradeTime
quote.quoteTime
regular.regularMarketLastPrice
fundamental.avg10DaysVolume
fundamental.avg1YearVolume
```

### 2. 查股票歷史價格 / 成交量

用途：抓 OHLCV，用來算均線、歷史波動率、成交量均值。

查 AAPL 最近 1 個月 daily candles：

```bash
curl -X GET "https://api.schwabapi.com/marketdata/v1/pricehistory?symbol=AAPL&periodType=month&period=1&frequencyType=daily&frequency=1&needExtendedHoursData=false&needPreviousClose=true" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Accept: application/json"
```

回傳 candle 欄位：

```text
open
high
low
close
volume
datetime
```

### 3. 查可用期權到期日

用途：先找 30～45 DTE 的到期日。

```bash
curl -X GET "https://api.schwabapi.com/marketdata/v1/expirationchain?symbol=AAPL" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Accept: application/json"
```

重要欄位：

```text
expirationDate
daysToExpiration
expirationType
standard
```

### 4. 查 Put option chain

用途：抓 Sell Put 候選合約。

```bash
curl -X GET "https://api.schwabapi.com/marketdata/v1/chains?symbol=AAPL&contractType=PUT&strikeCount=20&includeUnderlyingQuote=true&strategy=SINGLE&fromDate=2026-07-01&toDate=2026-07-31" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Accept: application/json"
```

重要欄位：

```text
putCall
symbol
bidPrice
askPrice
markPrice
lastPrice
totalVolume
openInterest
volatility
delta
theta
gamma
vega
rho
strikePrice
expirationDate
daysToExpiration
isInTheMoney
intrinsicValue
timeValue
theoreticalOptionValue
```

### 5. Python requests 範例

```python
import requests

ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"
BASE_URL = "https://api.schwabapi.com/marketdata/v1"

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json",
}

# 股票 quote
resp = requests.get(
    f"{BASE_URL}/quotes",
    headers=headers,
    params={
        "symbols": "AAPL",
        "fields": "quote,reference,regular,fundamental",
    },
)
print(resp.status_code)
print(resp.json())

# Put option chain
resp = requests.get(
    f"{BASE_URL}/chains",
    headers=headers,
    params={
        "symbol": "AAPL",
        "contractType": "PUT",
        "strikeCount": 20,
        "includeUnderlyingQuote": "true",
        "strategy": "SINGLE",
        "fromDate": "2026-07-01",
        "toDate": "2026-07-31",
    },
)
print(resp.status_code)
print(resp.json())
```

### 6. 調用順序建議

```text
1. 先完成 OAuth，取得 access token
2. 用 /quotes 測試 AAPL 是否可正常回傳
3. 用 /pricehistory 測試 OHLCV
4. 用 /expirationchain 找可用到期日
5. 用 /chains 抓 PUT chain
6. 把回傳資料轉成系統欄位：DTE、Delta、OI、Volume、Spread%、Breakeven、Annualized Return
```

### 7. 常見錯誤方向

```text
401 Unauthorized：access token 無效、過期，或 Authorization header 錯誤
400 Bad Request：參數格式錯誤，例如日期格式、fields 值、symbol 缺失
404 Not Found：symbol / endpoint 不存在
429 Too Many Requests：呼叫太頻繁，需降低頻率或加入 retry / backoff
500 Internal Server Error：Schwab 端服務錯誤，稍後重試並記錄 Schwab-Client-CorrelId
```

---

# 二、Specifications 分頁原始擷取文字

```text
Developer Portal
Home
API Products
User Guides
Dashboard
Profile
Sign Out
Home
API Products
User Guides
Dashboard
Profile
Sign Out
Specifications
Specifications
toggle

Specifications

Documentation
toggle

Documentation

API Products
Trader API - Individual
Market Data Production
Market Data Production
APIs to access Market Data
Market Data
 1.0.0 
OAS3

Trader API - Market data

Contact Schwab Trader API team
Servers
https://api.schwabapi.com/marketdata/v1
Authorize
Available authorizations

Scopes are used to grant an application different levels of access to data on behalf of the end user. Each API may declare one or more scopes.

API requires the following scopes. Select which ones you want to grant to Swagger UI.

oauth (OAuth2, authorizationCode)

Authorization URL: https://api.schwabapi.com/v1/oauth/authorize?response_type=code&client_id=fnB6k1X6JSFlQHravRt6T9m86AZlkD04&scope=readonly&redirect_uri=https://developer.schwab.com/oauth2-redirect.html

Token URL: https://api.schwabapi.com/v1/oauth/token

Flow: authorizationCode

client_id:
client_secret:
Authorize
Close
Quotes

Get Quotes Web Service.

GET
/quotes
Get Quotes by list of symbols.
Parameters
Try it out
Name	Description

symbols
string
(query)
	

Comma separated list of symbol(s) to look up a quote

Example : MRAD,EATOF,EBIZ,AAPL,BAC,AAAHX,AAAIX,$DJI,$SPX,MVEN,SOBS,TOITF,CNSWF,AMZN 230317C01360000,DJX 231215C00290000,/ESH23,./ADUF23C0.55,AUD/CAD



fields
string
(query)
	

Request for subset of data by passing coma separated list of root nodes, possible root nodes are quote, fundamental, extended, reference, regular. Sending quote, fundamental in request will return quote and fundamental data in response. Dont send this attribute for full response.

Default value : all



indicative
boolean
(query)
	

Include indicative symbol quotes for all ETF symbols in request. If ETF symbol ABC is in request and indicative=true API will return quotes for ABC and its corresponding indicative quote for $ABC.IV

Available values : true, false

Example : false

--
true
false
Responses
Code	Description	Links
200	

Quote Response

Media type
application/json
Controls Accept header.
Examples
Search by Symbols+Cusips+SSIDs
Example Value
Schema
{
  "AAPL": {
    "assetMainType": "EQUITY",
    "symbol": "AAPL",
    "quoteType": "NBBO",
    "realtime": true,
    "ssid": 1973757747,
    "reference": {
      "cusip": "037833100",
      "description": "Apple Inc",
      "exchange": "Q",
      "exchangeName": "NASDAQ"
    },
    "quote": {
      "52WeekHigh": 169,
      "52WeekLow": 1.1,
      "askMICId": "MEMX",
      "askPrice": 168.41,
      "askSize": 400,
      "askTime": 1644854683672,
      "bidMICId": "IEGX",
      "bidPrice": 168.4,
      "bidSize": 400,
      "bidTime": 1644854683633,
      "closePrice": 177.57,
      "highPrice": 169,
      "lastMICId": "XADF",
      "lastPrice": 168.405,
      "lastSize": 200,
      "lowPrice": 167.09,
      "mark": 168.405,
      "markChange": -9.164999999999992,
      "markPercentChange": -5.161344821760428,
      "netChange": -9.165,
      "netPercentChange": -5.161344821760428,
      "openPrice": 167.37,
      "quoteTime": 1644854683672,
      "securityStatus": "Normal",
      "totalVolume": 22361159,
      "tradeTime": 1644854683408,
      "volatility": 0.0347
    },
    "regular": {
      "regularMarketLastPrice": 168.405,
      "regularMarketLastSize": 2,
      "regularMarketNetChange": -9.165,
      "regularMarketPercentChange": -5.161344821760428,
      "regularMarketTradeTime": 1644854683408
    },
    "fundamental": {
      "avg10DaysVolume": 1,
      "avg1YearVolume": 0,
      "divAmount": 1.1,
      "divFreq": 0,
      "divPayAmount": 0,
      "divYield": 1.1,
      "eps": 0,
      "fundLeverageFactor": 1.1,
      "peRatio": 1.1
    }
  },
  "AAAIX": {
    "assetMainType": "MUTUAL_FUND",
    "symbol": "AAAIX",
    "realtime": true,
    "ssid": -1,
    "reference": {
      "cusip": "025085853",
      "description": "American Century Strategic Allocation: Aggressive Fund - I Class",
      "exchange": "3",
      "exchangeName": "Mutual Fund"
    },
    "quote": {
      "52WeekHigh": 9.24,
      "52WeekLow": 7.48,
      "closePrice": 9.12,
      "nAV": 0,
      "netChange": -0.03,
      "netPercentChange": -0.32894736842104566,
      "securityStatus": "Normal",
      "totalVolume": 0,
      "tradeTime": 0
    },
    "fundamental": {
      "avg10DaysVolume": 0,
      "avg1YearVolume": 0,
      "divAmount": 0,
      "divFreq": 0,
      "divPayAmount": 0,
      "divYield": 0.83059,
      "eps": 0,
      "fundLeverageFactor": 0,
      "peRatio": 0
    }
  },
  "AAAHX": {
    "assetMainType": "MUTUAL_FUND",
    "symbol": "AAAHX",
    "realtime": true,
    "ssid": -1,
    "reference": {
      "cusip": "02507J789",
      "description": "One Choice Blend+ 2015 Portfolio  I Class",
      "exchange": "3",
      "exchangeName": "Mutual Fund"
    },
    "quote": {
      "52WeekHigh": 10.64,
      "52WeekLow": 9.95,
      "closePrice": 10.53,
      "nAV": 0,
      "netChange": 0,
      "netPercentChange": 0,
      "securityStatus": "Normal",
      "totalVolume": 0,
      "tradeTime": 0
    },
    "fundamental": {
      "avg10DaysVolume": 0,
      "avg1YearVolume": 0,
      "divAmount": 0,
      "divFreq": 0,
      "divPayAmount": 0,
      "divYield": 0,
      "eps": 0,
      "fundLeverageFactor": 0,
      "peRatio": 0
    }
  },
  "BAC": {
    "assetMainType": "EQUITY",
    "symbol": "BAC",
    "quoteType": "NBBO",
    "realtime": true,
    "ssid": 851234497,
    "reference": {
      "cusip": "060505104",
      "description": "Bank Of America Corp",
      "exchange": "N",
      "exchangeName": "NYSE"
    },
    "quote": {
      "52WeekHigh": 48.185,
      "52WeekLow": 22.95,
      "askMICId": "XNYS",
      "askPrice": 47.2,
      "askSize": 2100,
      "askTime": 1644854683639,
      "bidMICId": "XNYS",
      "bidPrice": 47.19,
      "bidSize": 3700,
      "bidTime": 1644854683640,
      "closePrice": 44.49,
      "highPrice": 48.185,
      "lastMICId": "ARCX",
      "lastPrice": 47.195,
      "lastSize": 200,
      "lowPrice": 47.06,
      "mark": 47.195,
      "markChange": 2.7049999999999983,
      "markPercentChange": 6.080017981568888,
      "netChange": 2.705,
      "netPercentChange": 6.080017981568888,
      "openPrice": 48.02,
      "quoteTime": 1644854683640,
      "securityStatus": "Normal",
      "totalVolume": 13573182,
      "tradeTime": 1644854683638,
      "volatility": 0.0206
    },
    "regular": {
      "regularMarketLastPrice": 47.195,
      "regularMarketLastSize": 2,
      "regularMarketNetChange": 2.705,
      "regularMarketPercentChange": 6.080017981568888,
      "regularMarketTradeTime": 1644854683638
    },
    "fundamental": {
      "avg10DaysVolume": 43411957,
      "avg1YearVolume": 40653250,
      "declarationDate": "2021-07-21T05:00:00Z",
      "divAmount": 0.75,
      "divExDate": "2021-09-02T05:00:00Z",
      "divFreq": 4,
      "divPayAmount": 0.75,
      "divPayDate": "2021-09-24T05:00:00Z",
      "divYield": 1.77,
      "eps": 2.996,
      "fundLeverageFactor": 0,
      "nextDivExDate": "2021-12-27T06:00:00Z",
      "nextDivPayDate": "2021-12-27T06:00:00Z",
      "peRatio": 13.50133
    }
  },
  "$SPX": {
    "assetMainType": "INDEX",
    "symbol": "$SPX",
    "realtime": true,
    "ssid": 1819771877,
    "reference": {
      "description": "S&P DOW JONES INDEX            S&P 500",
      "exchange": "0",
      "exchangeName": "Index"
    },
    "quote": {
      "52WeekHigh": 4423.46,
      "52WeekLow": 4385.52,
      "closePrice": 4766.18,
      "highPrice": 4423.46,
      "lastPrice": 4396.2,
      "lowPrice": 4385.52,
      "netChange": -369.98,
      "netPercentChange": -7.762610728088331,
      "openPrice": 4412.61,
      "securityStatus": "Unknown",
      "totalVolume": 628009977,
      "tradeTime": 1644854683056
    }
  },
  "MRAD": {
    "assetMainType": "EQUITY",
    "assetSubType": "ETF",
    "symbol": "MRAD",
    "quoteType": "NBBO",
    "realtime": true,
    "ssid": 67229687,
    "reference": {
      "cusip": "402031868",
      "description": "Guinness Atkinson Fds SMART ETFS ADVERTISING MKT TCH ETF",
      "exchange": "P",
      "exchangeName": "NYSE Arca"
    },
    "quote": {
      "52WeekHigh": 31.96,
      "52WeekLow": 22.18,
      "askMICId": "IEGX",
      "askPrice": 22.29,
      "askSize": 500,
      "askTime": 1644854676848,
      "bidMICId": "EDGX",
      "bidPrice": 22.22,
      "bidSize": 500,
      "bidTime": 1644854681062,
      "closePrice": 26.8633,
      "highPrice": 22.18,
      "lastPrice": 22.18,
      "lastSize": 100,
      "lowPrice": 22.18,
      "mark": 22.22,
      "markChange": -4.6433,
      "markPercentChange": -17.284920318799255,
      "netChange": -4.6833,
      "netPercentChange": -17.433822352428777,
      "openPrice": 22.18,
      "quoteTime": 1644854681062,
      "securityStatus": "Normal",
      "totalVolume": 100,
      "tradeTime": 1644851921969,
      "volatility": 0
    },
    "regular": {
      "regularMarketLastPrice": 22.18,
      "regularMarketLastSize": 1,
      "regularMarketNetChange": -4.6833,
      "regularMarketPercentChange": -17.433822352428777,
      "regularMarketTradeTime": 1644851921969
    },
    "fundamental": {
      "avg10DaysVolume": 1606,
      "avg1YearVolume": 0,
      "divAmount": 0,
      "divFreq": 0,
      "divPayAmount": 0,
      "divYield": 0,
      "eps": 0,
      "fundLeverageFactor": 0,
      "fundStrategy": "A",
      "peRatio": 0
    }
  },
  "EBIZ": {
    "assetMainType": "EQUITY",
    "assetSubType": "ETF",
    "symbol": "EBIZ",
    "quoteType": "NBBO",
    "realtime": true,
    "ssid": 52313178,
    "reference": {
      "cusip": "37954Y467",
      "description": "GLOBAL X E-COMMERCE ETF",
      "exchange": "Q",
      "exchangeName": "NASDAQ"
    },
    "quote": {
      "52WeekHigh": 37.9754,
      "52WeekLow": 24.52,
      "askMICId": "XNAS",
      "askPrice": 24.85,
      "askSize": 200,
      "askTime": 1644854683318,
      "bidMICId": "XNAS",
      "bidPrice": 24.79,
      "bidSize": 200,
      "bidTime": 1644854683318,
      "closePrice": 27.45,
      "highPrice": 24.8303,
      "lastMICId": "XADF",
      "lastPrice": 24.8303,
      "lastSize": 100,
      "lowPrice": 24.52,
      "mark": 24.8303,
      "markChange": -2.619699999999998,
      "markPercentChange": -9.543533697632052,
      "netChange": -2.6197,
      "netPercentChange": -9.543533697632052,
      "openPrice": 24.55,
      "quoteTime": 1644854683318,
      "securityStatus": "Normal",
      "totalVolume": 1626,
      "tradeTime": 1644850278470,
      "volatility": 0
    },
    "regular": {
      "regularMarketLastPrice": 24.8303,
      "regularMarketLastSize": 1,
      "regularMarketNetChange": -2.6197,
      "regularMarketPercentChange": -9.543533697632052,
      "regularMarketTradeTime": 1644850278470
    },
    "fundamental": {
      "avg10DaysVolume": 0,
      "avg1YearVolume": 0,
      "declarationDate": "2020-12-29T06:00:00Z",
      "divAmount": 0,
      "divExDate": "2020-12-30T06:00:00Z",
      "divFreq": 1,
      "divPayAmount": 0.26641,
      "divPayDate": "2021-01-08T06:00:00Z",
      "divYield": 0.88276,
      "eps": 0,
      "fundLeverageFactor": 0,
      "fundStrategy": "P",
      "nextDivExDate": "2022-01-10T06:00:00Z",
      "nextDivPayDate": "2022-01-10T06:00:00Z",
      "peRatio": 0
    }
  },
  "$DJI": {
    "assetMainType": "INDEX",
    "symbol": "$DJI",
    "realtime": true,
    "ssid": 0,
    "reference": {
      "description": "Dow Jones Industrial Average",
      "exchange": "0",
      "exchangeName": "Index"
    },
    "quote": {
      "52WeekHigh": 34744.56,
      "52WeekLow": 34364.39,
      "closePrice": 34738.06,
      "highPrice": 34744.56,
      "lastPrice": 34436.13,
      "lowPrice": 34364.39,
      "netChange": -301.93,
      "netPercentChange": -0.8691619508976618,
      "openPrice": 34694.5,
      "securityStatus": "Unknown",
      "totalVolume": 106647543,
      "tradeTime": 1644854683055
    }
  },
  "AMZN  220617C03170000": {
    "assetMainType": "OPTION",
    "symbol": "AMZN  220617C03170000",
    "realtime": true,
    "ssid": 72507798,
    "reference": {
      "contractType": "C",
      "daysToExpiration": 123,
      "description": "Amazon.com Inc 06/17/2022 $3170 Call",
      "exchange": "o",
      "exchangeName": "OPR",
      "expirationDay": 17,
      "expirationMonth": 6,
      "expirationYear": 2022,
      "isPennyPilot": true,
      "lastTradingDay": 1655510400000,
      "multiplier": 100,
      "settlementType": "P",
      "strikePrice": 3170,
      "underlying": "AMZN",
      "uvExpirationType": "S"
    },
    "quote": {
      "askPrice": 223,
      "askSize": 2,
      "askTime": 0,
      "bidPrice": 217.65,
      "bidSize": 2,
      "bidTime": 0,
      "closePrice": 357.75,
      "delta": 0.5106,
      "gamma": 0.0007,
      "highPrice": 0,
      "impliedYield": 0.042,
      "indAskPrice": 0,
      "indBidPrice": 0,
      "indQuoteTime": 0,
      "lastPrice": 0,
      "lastSize": 0,
      "lowPrice": 0,
      "mark": 220.325,
      "markChange": -137.425,
      "markPercentChange": -38.41369671558351,
      "moneyIntrinsicValue": -40.795,
      "netChange": 0,
      "netPercentChange": 0,
      "openInterest": 0,
      "openPrice": 0,
      "quoteTime": 1644854683379,
      "rho": 4.5173,
      "securityStatus": "Normal",
      "theoreticalOptionValue": 221.4,
      "theta": -0.9619,
      "timeValue": 220.325,
      "totalVolume": 0,
      "tradeTime": 0,
      "underlyingPrice": 3129.205,
      "vega": 7.1633,
      "volatility": 32.8918
    }
  },
  "DJX   231215C00290000": {
    "assetMainType": "OPTION",
    "symbol": "DJX   231215C00290000",
    "realtime": true,
    "ssid": 69272575,
    "reference": {
      "contractType": "C",
      "daysToExpiration": 669,
      "description": "DOW JONES INDUS IND 12/15/2023 $290 Call",
      "exchange": "o",
      "exchangeName": "OPR",
      "expirationDay": 15,
      "expirationMonth": 12,
      "expirationYear": 2023,
      "isPennyPilot": true,
      "lastTradingDay": 1702602000000,
      "multiplier": 100,
      "settlementType": "A",
      "strikePrice": 290,
      "underlying": "$DJX",
      "uvExpirationType": "S"
    },
    "quote": {
      "askPrice": 76.95,
      "askSize": 11,
      "askTime": 0,
      "bidPrice": 70.9,
      "bidSize": 11,
      "bidTime": 0,
      "closePrice": 86.2,
      "delta": 0,
      "gamma": 0,
      "highPrice": 0,
      "impliedYield": 0,
      "indAskPrice": 79.55,
      "indBidPrice": 73.25,
      "indQuoteTime": 1644614546536,
      "lastPrice": 0,
      "lastSize": 0,
      "lowPrice": 0,
      "mark": 73.925,
      "markChange": -12.274999999999991,
      "markPercentChange": -14.24013921113688,
      "moneyIntrinsicValue": 0,
      "netChange": 0,
      "netPercentChange": 0,
      "openInterest": 0,
      "openPrice": 0,
      "quoteTime": 1644854648305,
      "rho": 0,
      "securityStatus": "Normal",
      "theoreticalOptionValue": 0,
      "theta": 0,
      "timeValue": 0,
      "totalVolume": 0,
      "tradeTime": 0,
      "underlyingPrice": 0,
      "vega": -999,
      "volatility": 0
    }
  },
  "TOITF": {
    "assetMainType": "EQUITY",
    "symbol": "TOITF",
    "quoteType": "NBBO",
    "realtime": true,
    "ssid": 68444487,
    "reference": {
      "cusip": "89072T102",
      "description": "TOPICUS COM INC",
      "exchange": "9",
      "exchangeName": "OTC Markets",
      "otcMarketTier": "PC"
    },
    "quote": {
      "52WeekHigh": 75.702,
      "52WeekLow": 45.3933,
      "askPrice": 75.978,
      "askSize": 10000,
      "askTime": 1644849000209,
      "bidPrice": 72.5951,
      "bidSize": 10000,
      "bidTime": 1644849000209,
      "closePrice": 92.7,
      "highPrice": 75.702,
      "lastPrice": 75.702,
      "lastSize": 100,
      "lowPrice": 72.5478,
      "mark": 75.702,
      "netChange": -16.998,
      "openPrice": 74.8977,
      "quoteTime": 1644854676927,
      "securityStatus": "Normal",
      "totalVolume": 4274,
      "tradeTime": 1644854585000,
      "volatility": 0
    },
    "regular": {
      "regularMarketLastPrice": 75.702,
      "regularMarketLastSize": 1,
      "regularMarketNetChange": -16.998,
      "regularMarketTradeTime": 1644854585000
    }
  },
  "EATOF": {
    "assetMainType": "EQUITY",
    "assetSubType": "ETF",
    "symbol": "EATOF",
    "quoteType": "NBBO",
    "realtime": true,
    "ssid": 43253301,
    "reference": {
      "cusip": "30052J102",
      "description": "EVOLVE AUTMBL INVTN INDX ETF",
      "exchange": "9",
      "exchangeName": "OTC Markets",
      "otcMarketTier": "EM"
    },
    "quote": {
      "52WeekHigh": 47.1993,
      "52WeekLow": 24.2835,
      "askPrice": 33.1512,
      "askSize": 400000,
      "askTime": 1644849000044,
      "bidPrice": 33.0487,
      "bidSize": 250000,
      "bidTime": 1644849000044,
      "closePrice": 40.198,
      "highPrice": 33.1196,
      "lastPrice": 33.1196,
      "lastSize": 200,
      "lowPrice": 32.82,
      "mark": 33.1196,
      "netChange": -7.0784,
      "openPrice": 32.82,
      "quoteTime": 1644854660496,
      "securityStatus": "Normal",
      "totalVolume": 1017,
      "tradeTime": 1644850274000,
      "volatility": 0
    },
    "regular": {
      "regularMarketLastPrice": 33.1196,
      "regularMarketLastSize": 2,
      "regularMarketNetChange": -7.0784,
      "regularMarketTradeTime": 1644850274000
    }
  },
  "CNSWF": {
    "assetMainType": "EQUITY",
    "symbol": "CNSWF",
    "quoteType": "NBBO",
    "realtime": true,
    "ssid": 807850646,
    "reference": {
      "cusip": "21037X100",
      "description": "Constellation Softwr",
      "exchange": "9",
      "exchangeName": "OTC Markets",
      "otcMarketTier": "PC"
    },
    "quote": {
      "52WeekHigh": 1709.738,
      "52WeekLow": 904.0901,
      "askPrice": 1693.4699,
      "askSize": 30000,
      "askTime": 1644849000567,
      "bidPrice": 1688.4547,
      "bidSize": 20000,
      "bidTime": 1644849000567,
      "closePrice": 1856.4626,
      "highPrice": 1709.738,
      "lastPrice": 1693.4541,
      "lastSize": 100,
      "lowPrice": 1680.1511,
      "mark": 1693.4541,
      "netChange": -163.0084,
      "openPrice": 1682.0121,
      "quoteTime": 1644854655233,
      "securityStatus": "Normal",
      "totalVolume": 13901,
      "tradeTime": 1644854560000,
      "volatility": 0
    },
    "regular": {
      "regularMarketLastPrice": 1693.4541,
      "regularMarketLastSize": 1,
      "regularMarketNetChange": -163.0084,
      "regularMarketTradeTime": 1644854560000
    }
  },
  "MVEN": {
    "assetMainType": "EQUITY",
    "symbol": "MVEN",
    "quoteType": "NBBO",
    "realtime": true,
    "ssid": 39225080,
    "reference": {
      "cusip": "88339B102",
      "description": "Themaven Inc",
      "exchange": "u",
      "exchangeName": "Nasdaq OTCBB",
      "otcMarketTier": "QX"
    },
    "quote": {
      "52WeekHigh": 3,
      "52WeekLow": 0.42,
      "askPrice": 0,
      "askSize": 0,
      "askTime": 0,
      "bidPrice": 0,
      "bidSize": 0,
      "bidTime": 0,
      "closePrice": 13.42,
      "highPrice": 0,
      "lastPrice": 0.42,
      "lastSize": 0,
      "lowPrice": 0,
      "mark": 0.42,
      "markChange": -13,
      "markPercentChange": -96.87034277198212,
      "netChange": -13,
      "netPercentChange": -96.87034277198212,
      "openPrice": 0,
      "quoteTime": 0,
      "securityStatus": "Normal",
      "totalVolume": 0,
      "tradeTime": 1644353952708,
      "volatility": 0
    },
    "regular": {
      "regularMarketLastPrice": 0.42,
      "regularMarketLastSize": 0,
      "regularMarketNetChange": -13,
      "regularMarketPercentChange": -96.87034277198212,
      "regularMarketTradeTime": 1644353952708
    },
    "fundamental": {
      "avg10DaysVolume": 299530,
      "avg1YearVolume": 430760,
      "divAmount": 0,
      "divFreq": 0,
      "divPayAmount": 0,
      "divYield": 0,
      "eps": 0,
      "fundLeverageFactor": 0,
      "peRatio": -0.68777
    }
  },
  "SOBS": {
    "assetMainType": "EQUITY",
    "symbol": "SOBS",
    "quoteType": "NBBO",
    "realtime": true,
    "ssid": 561081427,
    "reference": {
      "cusip": "83441Q105",
      "description": "Solvay Bank Corp Sol",
      "exchange": "9",
      "exchangeName": "OTC Markets",
      "otcMarketTier": "PC"
    },
    "quote": {
      "52WeekHigh": 43,
      "52WeekLow": 30.28,
      "askPrice": 45,
      "askSize": 200,
      "askTime": 0,
      "bidPrice": 39,
      "bidSize": 100,
      "bidTime": 0,
      "closePrice": 38.219,
      "highPrice": 0,
      "lastPrice": 38.219,
      "lastSize": 0,
      "lowPrice": 0,
      "mark": 38.219,
      "markChange": 0,
      "markPercentChange": 0,
      "netChange": 0,
      "netPercentChange": 0,
      "openPrice": 0,
      "quoteTime": 1644613200189,
      "securityStatus": "Normal",
      "totalVolume": 0,
      "tradeTime": 0,
      "volatility": 0
    },
    "regular": {
      "regularMarketLastPrice": 38.219,
      "regularMarketLastSize": 0,
      "regularMarketNetChange": 0,
      "regularMarketPercentChange": 0,
      "regularMarketTradeTime": 0
    },
    "fundamental": {
      "avg10DaysVolume": 1296,
      "avg1YearVolume": 0,
      "declarationDate": "2021-09-21T05:00:00Z",
      "divAmount": 1.48,
      "divExDate": "2021-09-30T05:00:00Z",
      "divFreq": 4,
      "divPayAmount": 1.47,
      "divPayDate": "2021-10-29T05:00:00Z",
      "divYield": 3.869,
      "eps": 0,
      "fundLeverageFactor": 0,
      "nextDivExDate": "2022-01-31T06:00:00Z",
      "nextDivPayDate": "2022-01-31T06:00:00Z",
      "peRatio": 0
    }
  },
  "/ESZ21": {
    "assetMainType": "FUTURE",
    "symbol": "/ESZ21",
    "realtime": true,
    "ssid": 0,
    "reference": {
      "description": "E-mini S&P 500 Index Futures,Dec-2021,ETH",
      "exchange": "@",
      "exchangeName": "XCME",
      "futureActiveSymbol": "/ESZ21",
      "futureExpirationDate": 1639717200000,
      "futureIsActive": true,
      "futureIsTradable": true,
      "futureMultiplier": 50,
      "futurePriceFormat": "D,D",
      "futureSettlementPrice": 4696,
      "futureTradingHours": "GLBX(de=1640;0=-17001600;1=r-17001600d-15551640;7=d-16401555)",
      "product": "/ES"
    },
    "quote": {
      "askPrice": 4694.5,
      "askSize": 113,
      "askTime": 0,
      "bidPrice": 4694.25,
      "bidSize": 57,
      "bidTime": 0,
      "netChange": -1.5,
      "closePrice": 4696,
      "futurePercentChange": -0.0003,
      "highPrice": 4701,
      "lastPrice": 4694.5,
      "lastSize": 3,
      "lowPrice": 4679.25,
      "mark": 0,
      "openInterest": 2328678,
      "openPrice": 4696.5,
      "quoteTime": 1637168671400,
      "securityStatus": "Unknown",
      "settleTime": 0,
      "tick": 0.25,
      "tickAmount": 12.5,
      "totalVolume": 550778,
      "tradeTime": 1637168671399
    }
  },
  "EUR/USD": {
    "assetMainType": "FOREX",
    "symbol": "EUR/USD",
    "ssid": 1,
    "realtime": true,
    "reference": {
      "description": "Euro/USDollar Spot",
      "exchange": "T",
      "exchangeName": "GFT",
      "isTradable": false,
      "marketMaker": "",
      "product": "",
      "tradingHours": ""
    },
    "quote": {
      "52WeekHigh": 1.135,
      "52WeekLow": 1.1331,
      "askPrice": 1.13456,
      "askSize": 1000000,
      "bidPrice": 1.13434,
      "bidSize": 1000000,
      "netChange": 0.00254,
      "closePrice": 1.13191,
      "highPrice": 1.135,
      "lastPrice": 1.13445,
      "lastSize": 0,
      "lowPrice": 1.1331,
      "mark": 1.13445,
      "openPrice": 1.13324,
      "netPercentChange": 0,
      "quoteTime": 1637236739892,
      "securityStatus": "Unknown",
      "tick": 0,
      "tickAmount": 0,
      "totalVolume": 0,
      "tradeTime": 1637236739892
    }
  }
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

Used to identify an individual request throughout the lifetime of the request and across systems.

	string
Example: 0a7f446a-7d74-49c8-a1e5-ca8ed59a3386
	No links
400	

Error response for generic client error 400

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "6808262e-52bb-4421-9d31-6c0e762e7dd5",
      "status": "400",
      "title": "Bad Request",
      "detail": "Missing header",
      "source": {
        "header": "Authorization"
      }
    },
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": "400",
      "title": "Bad Request",
      "detail": "Search combination should have min of 1.",
      "source": {
        "pointer": [
          "/data/attributes/symbols",
          "/data/attributes/cusips",
          "/data/attributes/ssids"
        ]
      }
    },
    {
      "id": "28485414-290f-42e2-992b-58ea3e3203b1",
      "status": "400",
      "title": "Bad Request",
      "detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value",
      "source": {
        "parameter": "fields"
      }
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
401	

Error response for 401 Unauthorized

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 401,
      "title": "Unauthorized",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
500	

Error response for 500 Internal Server Error

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": 500,
      "title": "Internal Server Error"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
GET
/{symbol_id}/quotes
Get Quote by single symbol.
Parameters
Try it out
Name	Description

symbol_id *
string
(path)
	

Symbol of instrument

Example : TSLA



fields
string
(query)
	

Request for subset of data by passing coma separated list of root nodes, possible root nodes are quote, fundamental, extended, reference, regular. Sending quote, fundamental in request will return quote and fundamental data in response. Dont send this attribute for full response.

Default value : all

Responses
Code	Description	Links
200	

Quote Response

Media type
application/json
Controls Accept header.
Examples
Search by symbol AAPL
Example Value
Schema
{
  "symbol": "AAPL",
  "empty": false,
  "previousClose": 174.56,
  "previousCloseDate": 1639029600000,
  "candles": [
    {
      "open": 175.01,
      "high": 175.15,
      "low": 175.01,
      "close": 175.04,
      "volume": 10719,
      "datetime": 1639137600000
    },
    {
      "open": 175.08,
      "high": 175.09,
      "low": 175.05,
      "close": 175.05,
      "volume": 500,
      "datetime": 1639137660000
    },
    {
      "open": 176.22,
      "high": 176.27,
      "low": 176.22,
      "close": 176.25,
      "volume": 3395,
      "datetime": 1640307300000
    },
    {
      "open": 176.26,
      "high": 176.27,
      "low": 176.26,
      "close": 176.26,
      "volume": 2174,
      "datetime": 1640307360000
    },
    {
      "open": 176.26,
      "high": 176.31,
      "low": 176.26,
      "close": 176.3,
      "volume": 15401,
      "datetime": 1640307420000
    },
    {
      "open": 176.3,
      "high": 176.3,
      "low": 176.3,
      "close": 176.3,
      "volume": 1700,
      "datetime": 1640307480000
    },
    {
      "open": 176.3,
      "high": 176.5,
      "low": 176.3,
      "close": 176.32,
      "volume": 5941,
      "datetime": 1640307540000
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

Used to identify an individual request throughout the lifetime of the request and across systems.

	string
Example: 0a7f446a-7d74-49c8-a1e5-ca8ed59a3386
	No links
400	

Error response for generic client error 400

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "6808262e-52bb-4421-9d31-6c0e762e7dd5",
      "status": "400",
      "title": "Bad Request",
      "detail": "Missing header",
      "source": {
        "header": "Authorization"
      }
    },
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": "400",
      "title": "Bad Request",
      "detail": "Search combination should have min of 1.",
      "source": {
        "pointer": [
          "/data/attributes/symbols",
          "/data/attributes/cusips",
          "/data/attributes/ssids"
        ]
      }
    },
    {
      "id": "28485414-290f-42e2-992b-58ea3e3203b1",
      "status": "400",
      "title": "Bad Request",
      "detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value",
      "source": {
        "parameter": "fields"
      }
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
401	

Error response for 401 Unauthorized

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 401,
      "title": "Unauthorized",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
404	

Error response for 404 Not Found

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 404,
      "title": "Not Found",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
500	

Error response for 500 Internal Server Error

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": 500,
      "title": "Internal Server Error"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
Option Chains

Get Option Chains Web Service.

GET
/chains
Get option chain for an optionable Symbol

Get Option Chain including information on options contracts associated with each expiration.

Parameters
Try it out
Name	Description

symbol *
string
(query)
	

Enter one symbol

Example : AAPL



contractType
string
(query)
	

Contract Type

Available values : CALL, PUT, ALL

--
CALL
PUT
ALL


strikeCount
integer
(query)
	

The Number of strikes to return above or below the at-the-money price



includeUnderlyingQuote
boolean
(query)
	

Underlying quotes to be included

--
true
false


strategy
string
(query)
	

OptionChain strategy. Default is SINGLE. ANALYTICAL allows the use of volatility, underlyingPrice, interestRate, and daysToExpiration params to calculate theoretical values.

Available values : SINGLE, ANALYTICAL, COVERED, VERTICAL, CALENDAR, STRANGLE, STRADDLE, BUTTERFLY, CONDOR, DIAGONAL, COLLAR, ROLL

--
SINGLE
ANALYTICAL
COVERED
VERTICAL
CALENDAR
STRANGLE
STRADDLE
BUTTERFLY
CONDOR
DIAGONAL
COLLAR
ROLL


interval
number($double)
(query)
	

Strike interval for spread strategy chains (see strategy param)



strike
number($double)
(query)
	

Strike Price



range
string
(query)
	

Range(ITM/NTM/OTM etc.)



fromDate
string($date)
(query)
	

From date(pattern: yyyy-MM-dd)



toDate
string($date)
(query)
	

To date (pattern: yyyy-MM-dd)



volatility
number($double)
(query)
	

Volatility to use in calculations. Applies only to ANALYTICAL strategy chains (see strategy param)



underlyingPrice
number($double)
(query)
	

Underlying price to use in calculations. Applies only to ANALYTICAL strategy chains (see strategy param)



interestRate
number($double)
(query)
	

Interest rate to use in calculations. Applies only to ANALYTICAL strategy chains (see strategy param)



daysToExpiration
integer($int32)
(query)
	

Days to expiration to use in calculations. Applies only to ANALYTICAL strategy chains (see strategy param)



expMonth
string
(query)
	

Expiration month

Available values : JAN, FEB, MAR, APR, MAY, JUN, JUL, AUG, SEP, OCT, NOV, DEC, ALL

--
JAN
FEB
MAR
APR
MAY
JUN
JUL
AUG
SEP
OCT
NOV
DEC
ALL


optionType
string
(query)
	

Option Type



entitlement
string
(query)
	

Applicable only if its retail token, entitlement of client PP-PayingPro, NP-NonPro and PN-NonPayingPro

Available values : PN, NP, PP

--
PN
NP
PP
Responses
Code	Description	Links
200	

The Chain for the symbol was returned successfully.

Media type
application/json
Controls Accept header.
Example Value
Schema
{
  "symbol": "string",
  "status": "string",
  "underlying": {
    "ask": 0,
    "askSize": 0,
    "bid": 0,
    "bidSize": 0,
    "change": 0,
    "close": 0,
    "delayed": true,
    "description": "string",
    "exchangeName": "IND",
    "fiftyTwoWeekHigh": 0,
    "fiftyTwoWeekLow": 0,
    "highPrice": 0,
    "last": 0,
    "lowPrice": 0,
    "mark": 0,
    "markChange": 0,
    "markPercentChange": 0,
    "openPrice": 0,
    "percentChange": 0,
    "quoteTime": 0,
    "symbol": "string",
    "totalVolume": 0,
    "tradeTime": 0
  },
  "strategy": "SINGLE",
  "interval": 0,
  "isDelayed": true,
  "isIndex": true,
  "daysToExpiration": 0,
  "interestRate": 0,
  "underlyingPrice": 0,
  "volatility": 0,
  "callExpDateMap": {
    "additionalProp1": {
      "additionalProp1": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp2": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp3": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      }
    },
    "additionalProp2": {
      "additionalProp1": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp2": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp3": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      }
    },
    "additionalProp3": {
      "additionalProp1": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp2": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp3": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      }
    }
  },
  "putExpDateMap": {
    "additionalProp1": {
      "additionalProp1": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp2": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp3": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      }
    },
    "additionalProp2": {
      "additionalProp1": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp2": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp3": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      }
    },
    "additionalProp3": {
      "additionalProp1": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp2": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      },
      "additionalProp3": {
        "putCall": "PUT",
        "symbol": "string",
        "description": "string",
        "exchangeName": "string",
        "bidPrice": 0,
        "askPrice": 0,
        "lastPrice": 0,
        "markPrice": 0,
        "bidSize": 0,
        "askSize": 0,
        "lastSize": 0,
        "highPrice": 0,
        "lowPrice": 0,
        "openPrice": 0,
        "closePrice": 0,
        "totalVolume": 0,
        "tradeDate": 0,
        "quoteTimeInLong": 0,
        "tradeTimeInLong": 0,
        "netChange": 0,
        "volatility": 0,
        "delta": 0,
        "gamma": 0,
        "theta": 0,
        "vega": 0,
        "rho": 0,
        "timeValue": 0,
        "openInterest": 0,
        "isInTheMoney": true,
        "theoreticalOptionValue": 0,
        "theoreticalVolatility": 0,
        "isMini": true,
        "isNonStandard": true,
        "optionDeliverablesList": [
          {
            "symbol": "string",
            "assetType": "string",
            "deliverableUnits": "string",
            "currencyType": "string"
          }
        ],
        "strikePrice": 0,
        "expirationDate": "string",
        "daysToExpiration": 0,
        "expirationType": "M",
        "lastTradingDay": 0,
        "multiplier": 0,
        "settlementType": "A",
        "deliverableNote": "string",
        "isIndexOption": true,
        "percentChange": 0,
        "markChange": 0,
        "markPercentChange": 0,
        "isPennyPilot": true,
        "intrinsicValue": 0,
        "optionRoot": "string"
      }
    }
  }
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

Used to identify an individual request throughout the lifetime of the request and across systems.

	string
Example: 0a7f446a-7d74-49c8-a1e5-ca8ed59a3386
	No links
400	

Error response for generic client error 400

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "6808262e-52bb-4421-9d31-6c0e762e7dd5",
      "status": "400",
      "title": "Bad Request",
      "detail": "Missing header",
      "source": {
        "header": "Authorization"
      }
    },
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": "400",
      "title": "Bad Request",
      "detail": "Search combination should have min of 1.",
      "source": {
        "pointer": [
          "/data/attributes/symbols",
          "/data/attributes/cusips",
          "/data/attributes/ssids"
        ]
      }
    },
    {
      "id": "28485414-290f-42e2-992b-58ea3e3203b1",
      "status": "400",
      "title": "Bad Request",
      "detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value",
      "source": {
        "parameter": "fields"
      }
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
401	

Error response for 401 Unauthorized

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 401,
      "title": "Unauthorized",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
404	

Error response for 404 Not Found

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 404,
      "title": "Not Found",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
500	

Error response for 500 Internal Server Error

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": 500,
      "title": "Internal Server Error"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
Option Expiration Chain

Get Option Expiration Chain Web Service.

GET
/expirationchain
Get option expiration chain for an optionable symbol

Get Option Expiration (Series) information for an optionable symbol. Does not include individual options contracts for the underlying.

Parameters
Try it out
Name	Description

symbol *
string
(query)
	

Enter one symbol

Example : AAPL

Responses
Code	Description	Links
200	

The Expiration Chain for the symbol was returned successfully.

Media type
application/json
Controls Accept header.
Examples
Get ExpirationChain for AAPL
Example Value
Schema
{
  "expirationList": [
    {
      "expirationDate": "2022-01-07",
      "daysToExpiration": 2,
      "expirationType": "W",
      "standard": true
    },
    {
      "expirationDate": "2022-01-14",
      "daysToExpiration": 9,
      "expirationType": "W",
      "standard": true
    },
    {
      "expirationDate": "2022-01-21",
      "daysToExpiration": 16,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2022-01-28",
      "daysToExpiration": 23,
      "expirationType": "W",
      "standard": true
    },
    {
      "expirationDate": "2022-02-04",
      "daysToExpiration": 30,
      "expirationType": "W",
      "standard": true
    },
    {
      "expirationDate": "2022-02-11",
      "daysToExpiration": 37,
      "expirationType": "W",
      "standard": true
    },
    {
      "expirationDate": "2022-02-18",
      "daysToExpiration": 44,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2022-03-18",
      "daysToExpiration": 72,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2022-04-14",
      "daysToExpiration": 99,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2022-05-20",
      "daysToExpiration": 135,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2022-06-17",
      "daysToExpiration": 163,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2022-07-15",
      "daysToExpiration": 191,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2022-09-16",
      "daysToExpiration": 254,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2023-01-20",
      "daysToExpiration": 380,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2023-03-17",
      "daysToExpiration": 436,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2023-06-16",
      "daysToExpiration": 527,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2023-09-15",
      "daysToExpiration": 618,
      "expirationType": "S",
      "standard": true
    },
    {
      "expirationDate": "2024-01-19",
      "daysToExpiration": 744,
      "expirationType": "S",
      "standard": true
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

Used to identify an individual request throughout the lifetime of the request and across systems.

	string
Example: 0a7f446a-7d74-49c8-a1e5-ca8ed59a3386
	No links
400	

Error response for generic client error 400

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "6808262e-52bb-4421-9d31-6c0e762e7dd5",
      "status": "400",
      "title": "Bad Request",
      "detail": "Missing header",
      "source": {
        "header": "Authorization"
      }
    },
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": "400",
      "title": "Bad Request",
      "detail": "Search combination should have min of 1.",
      "source": {
        "pointer": [
          "/data/attributes/symbols",
          "/data/attributes/cusips",
          "/data/attributes/ssids"
        ]
      }
    },
    {
      "id": "28485414-290f-42e2-992b-58ea3e3203b1",
      "status": "400",
      "title": "Bad Request",
      "detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value",
      "source": {
        "parameter": "fields"
      }
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
401	

Error response for 401 Unauthorized

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 401,
      "title": "Unauthorized",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
404	

Error response for 404 Not Found

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 404,
      "title": "Not Found",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
500	

Error response for 500 Internal Server Error

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": 500,
      "title": "Internal Server Error"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
PriceHistory

Get Price History Web Service.

GET
/pricehistory
Get PriceHistory for a single symbol and date ranges.

Get historical Open, High, Low, Close, and Volume for a given frequency (i.e. aggregation). Frequency available is dependent on periodType selected. The datetime format is in EPOCH milliseconds.

Parameters
Try it out
Name	Description

symbol *
string
(query)
	

The Equity symbol used to look up price history

Example : AAPL



periodType
string
(query)
	

The chart period being requested.

Available values : day, month, year, ytd

--
day
month
year
ytd


period
integer($int32)
(query)
	

The number of chart period types.

If the periodType is
• day - valid values are 1, 2, 3, 4, 5, 10
• month - valid values are 1, 2, 3, 6
• year - valid values are 1, 2, 3, 5, 10, 15, 20
• ytd - valid values are 1

If the period is not specified and the periodType is
• day - default period is 10.
• month - default period is 1.
• year - default period is 1.
• ytd - default period is 1.




frequencyType
string
(query)
	

The time frequencyType

If the periodType is
• day - valid value is minute
• month - valid values are daily, weekly
• year - valid values are daily, weekly, monthly
• ytd - valid values are daily, weekly

If frequencyType is not specified, default value depends on the periodType
• day - defaulted to minute.
• month - defaulted to weekly.
• year - defaulted to monthly.
• ytd - defaulted to weekly.


Available values : minute, daily, weekly, monthly

--
minute
daily
weekly
monthly


frequency
integer($int32)
(query)
	

The time frequency duration

If the frequencyType is
• minute - valid values are 1, 5, 10, 15, 30
• daily - valid value is 1
• weekly - valid value is 1
• monthly - valid value is 1

If frequency is not specified, default value is 1




startDate
integer($int64)
(query)
	

The start date, Time in milliseconds since the UNIX epoch eg 1451624400000
If not specified startDate will be (endDate - period) excluding weekends and holidays.



endDate
integer($int64)
(query)
	

The end date, Time in milliseconds since the UNIX epoch eg 1451624400000
If not specified, the endDate will default to the market close of previous business day.



needExtendedHoursData
boolean
(query)
	

Need extended hours data

--
true
false


needPreviousClose
boolean
(query)
	

Need previous close price/date

--
true
false
Responses
Code	Description	Links
200	

Get all candles for given date range

Media type
application/json
Controls Accept header.
Examples
Search by symbol AAPL
Example Value
Schema
{
  "symbol": "AAPL",
  "empty": false,
  "previousClose": 174.56,
  "previousCloseDate": 1639029600000,
  "candles": [
    {
      "open": 175.01,
      "high": 175.15,
      "low": 175.01,
      "close": 175.04,
      "volume": 10719,
      "datetime": 1639137600000
    },
    {
      "open": 175.08,
      "high": 175.09,
      "low": 175.05,
      "close": 175.05,
      "volume": 500,
      "datetime": 1639137660000
    },
    {
      "open": 176.22,
      "high": 176.27,
      "low": 176.22,
      "close": 176.25,
      "volume": 3395,
      "datetime": 1640307300000
    },
    {
      "open": 176.26,
      "high": 176.27,
      "low": 176.26,
      "close": 176.26,
      "volume": 2174,
      "datetime": 1640307360000
    },
    {
      "open": 176.26,
      "high": 176.31,
      "low": 176.26,
      "close": 176.3,
      "volume": 15401,
      "datetime": 1640307420000
    },
    {
      "open": 176.3,
      "high": 176.3,
      "low": 176.3,
      "close": 176.3,
      "volume": 1700,
      "datetime": 1640307480000
    },
    {
      "open": 176.3,
      "high": 176.5,
      "low": 176.3,
      "close": 176.32,
      "volume": 5941,
      "datetime": 1640307540000
    }
  ]
}
	No links
400	

Error response for generic client error 400

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "6808262e-52bb-4421-9d31-6c0e762e7dd5",
      "status": "400",
      "title": "Bad Request",
      "detail": "Missing header",
      "source": {
        "header": "Authorization"
      }
    },
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": "400",
      "title": "Bad Request",
      "detail": "Search combination should have min of 1.",
      "source": {
        "pointer": [
          "/data/attributes/symbols",
          "/data/attributes/cusips",
          "/data/attributes/ssids"
        ]
      }
    },
    {
      "id": "28485414-290f-42e2-992b-58ea3e3203b1",
      "status": "400",
      "title": "Bad Request",
      "detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value",
      "source": {
        "parameter": "fields"
      }
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
401	

Error response for 401 Unauthorized

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 401,
      "title": "Unauthorized",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
404	

Error response for 404 Not Found

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 404,
      "title": "Not Found",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
500	

Error response for 500 Internal Server Error

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": 500,
      "title": "Internal Server Error"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
Movers

Get Movers Web Service.

GET
/movers/{symbol_id}
Get Movers for a specific index.

Get a list of top 10 securities movement for a specific index.

Parameters
Try it out
Name	Description

symbol_id *
string
(path)
	

Index Symbol

Available values : $DJI, $COMPX, $SPX, NYSE, NASDAQ, OTCBB, INDEX_ALL, EQUITY_ALL, OPTION_ALL, OPTION_PUT, OPTION_CALL

Example : $DJI

$DJI
$COMPX
$SPX
NYSE
NASDAQ
OTCBB
INDEX_ALL
EQUITY_ALL
OPTION_ALL
OPTION_PUT
OPTION_CALL


sort
string
(query)
	

Sort by a particular attribute

Available values : VOLUME, TRADES, PERCENT_CHANGE_UP, PERCENT_CHANGE_DOWN

Example : VOLUME

--
VOLUME
TRADES
PERCENT_CHANGE_UP
PERCENT_CHANGE_DOWN


frequency
integer($int32)
(query)
	

To return movers with the specified directions of up or down

Available values : 0, 1, 5, 10, 30, 60

Default value : 0

--
0
1
5
10
30
60
Responses
Code	Description	Links
200	

Analytics for the symbol was returned successfully.

Media type
application/json
Controls Accept header.
Examples
Search by "$DJI"
Example Value
Schema
{
  "screeners": [
    {
      "change": 10,
      "description": "Dow jones",
      "direction": "up",
      "last": 100,
      "symbol": "$DJI",
      "totalVolume": 100
    },
    {
      "change": 10,
      "description": "Dow jones",
      "direction": "up",
      "last": 100,
      "symbol": "$DJI",
      "totalVolume": 100
    },
    {
      "change": 10,
      "description": "Dow jones",
      "direction": "up",
      "last": 100,
      "symbol": "$DJI",
      "totalVolume": 100
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

Used to identify an individual request throughout the lifetime of the request and across systems.

	string
Example: 0a7f446a-7d74-49c8-a1e5-ca8ed59a3386
	No links
400	

Error response for generic client error 400

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "6808262e-52bb-4421-9d31-6c0e762e7dd5",
      "status": "400",
      "title": "Bad Request",
      "detail": "Missing header",
      "source": {
        "header": "Authorization"
      }
    },
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": "400",
      "title": "Bad Request",
      "detail": "Search combination should have min of 1.",
      "source": {
        "pointer": [
          "/data/attributes/symbols",
          "/data/attributes/cusips",
          "/data/attributes/ssids"
        ]
      }
    },
    {
      "id": "28485414-290f-42e2-992b-58ea3e3203b1",
      "status": "400",
      "title": "Bad Request",
      "detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value",
      "source": {
        "parameter": "fields"
      }
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
401	

Error response for 401 Unauthorized

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 401,
      "title": "Unauthorized",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
404	

Error response for 404 Not Found

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 404,
      "title": "Not Found",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
500	

Error response for 500 Internal Server Error

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": 500,
      "title": "Internal Server Error"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
MarketHours

Get MarketHours Web Service.

GET
/markets
Get Market Hours for different markets.

Get Market Hours for dates in the future across different markets.

Parameters
Try it out
Name	Description

markets *
array[string]
(query)
	

List of markets

Available values : equity, option, bond, future, forex

equity
option
bond
future
forex


date
string($date)
(query)
	

Valid date range is from currentdate to 1 year from today. It will default to current day if not entered. Date format:YYYY-MM-DD

Responses
Code	Description	Links
200	

OK

Media type
application/json
Controls Accept header.
Examples
Get getMarketHours for EQUITY and OPTION
Example Value
Schema
{
  "equity": {
    "EQ": {
      "date": "2022-04-14",
      "marketType": "EQUITY",
      "product": "EQ",
      "productName": "equity",
      "isOpen": true,
      "sessionHours": {
        "preMarket": [
          {
            "start": "2022-04-14T07:00:00-04:00",
            "end": "2022-04-14T09:30:00-04:00"
          }
        ],
        "regularMarket": [
          {
            "start": "2022-04-14T09:30:00-04:00",
            "end": "2022-04-14T16:00:00-04:00"
          }
        ],
        "postMarket": [
          {
            "start": "2022-04-14T16:00:00-04:00",
            "end": "2022-04-14T20:00:00-04:00"
          }
        ]
      }
    }
  },
  "option": {
    "EQO": {
      "date": "2022-04-14",
      "marketType": "OPTION",
      "product": "EQO",
      "productName": "equity option",
      "isOpen": true,
      "sessionHours": {
        "regularMarket": [
          {
            "start": "2022-04-14T09:30:00-04:00",
            "end": "2022-04-14T16:00:00-04:00"
          }
        ]
      }
    },
    "IND": {
      "date": "2022-04-14",
      "marketType": "OPTION",
      "product": "IND",
      "productName": "index option",
      "isOpen": true,
      "sessionHours": {
        "regularMarket": [
          {
            "start": "2022-04-14T09:30:00-04:00",
            "end": "2022-04-14T16:15:00-04:00"
          }
        ]
      }
    }
  }
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The generated GUID can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
	No links
400	

Error response for generic client error 400

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "6808262e-52bb-4421-9d31-6c0e762e7dd5",
      "status": "400",
      "title": "Bad Request",
      "detail": "Missing header",
      "source": {
        "header": "Authorization"
      }
    },
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": "400",
      "title": "Bad Request",
      "detail": "Search combination should have min of 1.",
      "source": {
        "pointer": [
          "/data/attributes/symbols",
          "/data/attributes/cusips",
          "/data/attributes/ssids"
        ]
      }
    },
    {
      "id": "28485414-290f-42e2-992b-58ea3e3203b1",
      "status": "400",
      "title": "Bad Request",
      "detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value",
      "source": {
        "parameter": "fields"
      }
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
401	

Error response for 401 Unauthorized

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 401,
      "title": "Unauthorized",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
500	

Error response for 500 Internal Server Error

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": 500,
      "title": "Internal Server Error"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
GET
/markets/{market_id}
Get Market Hours for a single market.

Get Market Hours for dates in the future for a single market.

Parameters
Try it out
Name	Description

market_id *
string
(path)
	

market id

Available values : equity, option, bond, future, forex

equity
option
bond
future
forex


date
string($date)
(query)
	

Valid date range is from currentdate to 1 year from today. It will default to current day if not entered. Date format:YYYY-MM-DD

Responses
Code	Description	Links
200	

OK

Media type
application/json
Controls Accept header.
Examples
Get market hours for equity market
Example Value
Schema
{
  "equity": {
    "EQ": {
      "date": "2022-04-14",
      "marketType": "EQUITY",
      "exchange": "NULL",
      "category": "NULL",
      "product": "EQ",
      "productName": "equity",
      "isOpen": true,
      "sessionHours": {
        "preMarket": [
          {
            "start": "2022-04-14T07:00:00-04:00",
            "end": "2022-04-14T09:30:00-04:00"
          }
        ],
        "regularMarket": [
          {
            "start": "2022-04-14T09:30:00-04:00",
            "end": "2022-04-14T16:00:00-04:00"
          }
        ],
        "postMarket": [
          {
            "start": "2022-04-14T16:00:00-04:00",
            "end": "2022-04-14T20:00:00-04:00"
          }
        ]
      }
    }
  }
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The generated GUID can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
	No links
400	

Error response for generic client error 400

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "6808262e-52bb-4421-9d31-6c0e762e7dd5",
      "status": "400",
      "title": "Bad Request",
      "detail": "Missing header",
      "source": {
        "header": "Authorization"
      }
    },
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": "400",
      "title": "Bad Request",
      "detail": "Search combination should have min of 1.",
      "source": {
        "pointer": [
          "/data/attributes/symbols",
          "/data/attributes/cusips",
          "/data/attributes/ssids"
        ]
      }
    },
    {
      "id": "28485414-290f-42e2-992b-58ea3e3203b1",
      "status": "400",
      "title": "Bad Request",
      "detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value",
      "source": {
        "parameter": "fields"
      }
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
401	

Error response for 401 Unauthorized

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 401,
      "title": "Unauthorized",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
404	

Error response for 404 Not Found

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 404,
      "title": "Not Found",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
500	

Error response for 500 Internal Server Error

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": 500,
      "title": "Internal Server Error"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
Instruments

Get Instruments Web Service.

GET
/instruments
Get Instruments by symbols and projections.

Get Instruments details by using different projections. Get more specific fundamental instrument data by using fundamental as the projection.

Parameters
Try it out
Name	Description

symbol *
string
(query)
	

symbol of a security



projection *
string
(query)
	

search by

Available values : symbol-search, symbol-regex, desc-search, desc-regex, search, fundamental

symbol-search
symbol-regex
desc-search
desc-regex
search
fundamental
Responses
Code	Description	Links
200	

OK

Media type
application/json
Controls Accept header.
Examples
symbol=AAPL,BAC&projection=symbol-search
Example Value
Schema
{
  "instruments": [
    {
      "cusip": "037833100",
      "symbol": "AAPL",
      "description": "Apple Inc",
      "exchange": "NASDAQ",
      "assetType": "EQUITY"
    },
    {
      "cusip": "060505104",
      "symbol": "BAC",
      "description": "Bank Of America Corp",
      "exchange": "NYSE",
      "assetType": "EQUITY"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Resource-Version	

Used to identify desired and returned version of an API resource

	integer
Example: 3
Schwab-Client-CorrelId	

Used to identify an individual request throughout the lifetime of the request and across systems.

	string
Example: 0a7f446a-7d74-49c8-a1e5-ca8ed59a3386
	No links
400	

Error response for generic client error 400

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "6808262e-52bb-4421-9d31-6c0e762e7dd5",
      "status": "400",
      "title": "Bad Request",
      "detail": "Missing header",
      "source": {
        "header": "Authorization"
      }
    },
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": "400",
      "title": "Bad Request",
      "detail": "Search combination should have min of 1.",
      "source": {
        "pointer": [
          "/data/attributes/symbols",
          "/data/attributes/cusips",
          "/data/attributes/ssids"
        ]
      }
    },
    {
      "id": "28485414-290f-42e2-992b-58ea3e3203b1",
      "status": "400",
      "title": "Bad Request",
      "detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value",
      "source": {
        "parameter": "fields"
      }
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
401	

Error response for 401 Unauthorized

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 401,
      "title": "Unauthorized",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
500	

Error response for 500 Internal Server Error

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": 500,
      "title": "Internal Server Error"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
GET
/instruments/{cusip_id}
Get Instrument by specific cusip

Get basic instrument details by cusip

Parameters
Try it out
Name	Description

cusip_id *
string
(path)
	

cusip of a security

Responses
Code	Description	Links
200	

OK

Media type
application/json
Controls Accept header.
Examples
Get getinstruments for cusip
Example Value
Schema
{
  "cusip": "037833100",
  "symbol": "AAPL",
  "description": "Apple Inc",
  "exchange": "NASDAQ",
  "assetType": "EQUITY"
}
Headers:
Name	Description	Type
Schwab-Resource-Version	

Used to identify desired and returned version of an API resource

	integer
Example: 3
Schwab-Client-CorrelId	

Used to identify an individual request throughout the lifetime of the request and across systems.

	string
Example: 0a7f446a-7d74-49c8-a1e5-ca8ed59a3386
	No links
400	

Error response for generic client error 400

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "6808262e-52bb-4421-9d31-6c0e762e7dd5",
      "status": "400",
      "title": "Bad Request",
      "detail": "Missing header",
      "source": {
        "header": "Authorization"
      }
    },
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": "400",
      "title": "Bad Request",
      "detail": "Search combination should have min of 1.",
      "source": {
        "pointer": [
          "/data/attributes/symbols",
          "/data/attributes/cusips",
          "/data/attributes/ssids"
        ]
      }
    },
    {
      "id": "28485414-290f-42e2-992b-58ea3e3203b1",
      "status": "400",
      "title": "Bad Request",
      "detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value",
      "source": {
        "parameter": "fields"
      }
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
401	

Error response for 401 Unauthorized

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 401,
      "title": "Unauthorized",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
404	

Error response for 404 Not Found

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "status": 404,
      "title": "Not Found",
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
500	

Error response for 500 Internal Server Error

Media type
application/json
Example Value
Schema
{
  "errors": [
    {
      "id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",
      "status": 500,
      "title": "Internal Server Error"
    }
  ]
}
Headers:
Name	Description	Type
Schwab-Client-CorrelId	

This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.

	string
Example: 977dbd7f-992e-44d2-a5f4-e213d29c8691
Schwab-Resource-Version	

This is the requested API version.

	string
Example: 1
	No links
Schemas
Terms Of Use
|
Privacy Notice

© 2026 Charles Schwab & Co., Inc. All rights reserved. Member SIPC. Unauthorized access is prohibited. Usage is monitored.

```

---

# 三、Documentation / Schwab Streamer API 分頁原始擷取文字

```text
Developer Portal
Home
API Products
User Guides
Dashboard
Profile
Sign Out
Home
API Products
User Guides
Dashboard
Profile
Sign Out
Documentation
Specifications
toggle

Specifications

Documentation
toggle

Documentation

API Products
Trader API - Individual
Market Data Production
Market Data Production
Schwab Streamer API

The Streamer API enables clients to connect into different services to stream market data and account activity with JSON-formatting via WebSockets. Authentication and entitlements are provided via the Access token generated from the POST Token endpoint. Streamer information to establish the connection can be found on the GET User Preference endpoint. Client as referenced throughout this document is in reference to the application.

Contents
1. API Contract

1. Services available:

Service Name	Description	Delivery Type
LEVELONE_EQUITIES	Level 1 Equities	Change
LEVELONE_OPTIONS	Level 1 Options	Change
LEVELONE_FUTURES	Level 1 Futures	Change
LEVELONE_FUTURES_OPTIONS	Level 1 Futures Options	Change
LEVELONE_FOREX	Level 1 Forex	Change
NYSE_BOOK	Level Two book for Equities	Whole
NASDAQ_BOOK	Level Two book for Equities	Whole
OPTIONS_BOOK	Level Two book for Options	Whole
CHART_EQUITY	Chart candle for Equities	All Sequence
CHART_FUTURES	Chart candle for Futures	All Sequence
SCREENER_EQUITY	Advances and Decliners for Equities	Whole
SCREENER_OPTION	Advances and Decliners for Options	Whole
ACCT_ACTIVITY	Get account activity information such as order fills, etc	All Sequence


 

2. Request Format
A client request will consist of an array of one or more commands. Each command will include:

Request	Name	Parameter
service	Service Name (required)	ADMIN, LEVELONE_EQUITY etc. Please see Service Names table above.
command	Command (required)	LOGIN, SUBS, ADD, UNSUBS, VIEW, LOGOUT
requestid	Request ID (required)	Unique number that will identify this request.
SchwabClientCustomerId	Client's customer ID	`schwabClientCustomerId` as found in GET User Preference endpoint
SchwabClientCorrelId	Client's session ID	`schwabClientCorrelId` as found in GET User Preference endpoint. Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	Any parameter (optional)	fields, version, credential, symbol, frequency, period, etc
Command	Name
LOGIN	Initial request when opening a new connection. This must be successful before sending other commands.
SUBS	

Subscribes to a set of symbols or keys for a particular service. This overwrites all previously subscribed symbols for that service. This is a convenient way to wipe out old subscription list and start fresh, but it's not the most efficient. If you only want to add one symbol to 300 already subscribed, use an ADD instead.
For example:
 

SUBS A,B,C (fresh sub for LEVELONE_EQUITIES)
SUBS A (fresh sub for LEVELONE_EQUITIES, previous SUBS of B,C are unsub'ed, only A is sub'ed)

ADD	

Adds a new symbol for a particular service. This does NOT wipe out previous symbols that were already subscribed. It is OK to use ADD for first subscription command instead of SUBS.
For example:
 

ADD A,B (fresh sub for LEVELONE_EQUITIES)
ADD C (additional symbol C added to A, B. All 3 symbols will stream)

UNSUBS	This unsubscribes a symbol to a list of subscribed symbol for a particular service.
VIEW	This changes the field subscription for a particular service. It will apply to all symbols for that particular service.
LOGOUT	Logs out of the streamer connection. Streamer will close the connection.

Example:
One Request
{
  "requestid": "0",
  "service": "LEVELONE_EQUITIES",
  "command": "SUBS",
  "SchwabClientCustomerId": "Someone",
  "SchwabClientCorrelId": "3be0b7e7-5b8b-4fd3-9bed-7f49106cfe1",
  "parameters": {
   "keys": "AAPL",
   "fields": "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54"
  }
}
 

Multiple Requests
{
  "requests": [
   {
    "requestid": "1",
    "service": "ADMIN",
    "command": "LOGIN",
    "SchwabClientCustomerId": "Someone",
    "SchwabClientCorrelId": "2be0b7e7-5b8b-4fd3-9bed-7f49106cfe1",
    "parameters": {
     "Authorization": "PN",
     "SchwabClientChannel": "IO",
     "SchwabClientFunctionId": "Tradeticket"
    }
   },
   {
    "requestid":"3",
    "service":"LEVELONE_EQUITIES",
    "command":"SUBS",
    "SchwabClientCustomerId":"Someone",
    "SchwabClientCorrelId":"2be0b7e7-5b8b-4fd3-9bed-7f49106cfe1",
    "parameters":{
     "keys":"AAPL",
     "fields":"0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19"
    }
   }
  ]
}

3. Response Format
There are currently three types of responses:
 

Response â€“ Response to a request
Notify â€“ Notification of heartbeats
Data â€“ Streaming market data


A client response will consist of an array of one or more responses. Each response will include:
 

Response Type	Request	Name	Parameter
response notify data	service	Service Name	ADMIN, LEVELONE_EQUITY, etc. Please see Service Names table in section 5.
requestid	Request ID	Unique number that will identify the original request
command	Command from the request	LOGIN, SUBS, ADD, UNSUBS, VIEW, LOGOUT
content	Data content


Examples:
{"notify":[{"heartbeat":"1668715930582"}]}

{
 "response": [
  {
   "service": "LEVELONE_EQUITIES",
   "command": "SUBS",
   "requestid": "0",
   "SchwabClientCorrelId": "3be0b7e7-5b8b-4fd3-9bed-7f49106cfe1",
   "timestamp": 1668715930582,
   "content": {
    "code": 0,
    "msg": "SUBS command succeeded"
   }
  }
 ]
}
{
 "data": [
  {
   "service": "LEVELONE_EQUITIES",
   "timestamp": 1668715930585,
   "command": "SUBS",
   "content": [
    {
     "1": 149.81,
     "2": 149.82,
     "3": 149.811,
     "4": 4,
     "5": 2,
     "6": "Q",
     "7": "P",
     "8": 56049058,
     "9": 300,
     "10": 151.48,
     "11": 146.15,
     "12": " ",
     "13": 142.41,
     "14": "Q",
     "15": false,
     "16": "APPLE INC",
     "17": "D",
     "18": 146.43,
     "19": 7.401,
     "20": 182.94,
     "21": 129.04,
     "22": 0.04062,
     "23": 0,
     "24": 0,
     "25": 0,
     "26": "NASDAQ",
     "27": "",
     "28": true,
     "29": true,
     "30": 149.811,
     "31": 300,
     "32": 7.401,
     "33": "Normal",
     "34": 149.811,
     "35": 1668715930570,
     "36": 1668715930345,
     "37": 1668715930345,
     "38": 1668715930570,
     "39": 1668715930522,
     "40": "XNAS",
     "41": "ARCX",
     "42": "XADF",
     "43": 5.19696651,
     "44": 5.19696651,
     "45": 7.401,
     "46": 5.19696651,
     "key": "AAPL",
     "delayed": false
    }
   ]
  }
 ]
}
 


 

4. Response Codes
 

Code	Name	Description	Connection Severed	Error Notes
0	SUCCESS	The request was successful	No	n/a - success
3	LOGIN_DENIED	The user login has been denied	Yes	Client should reconnect and re-login with new token. Client to determine if failed logins are expected.
9	UNKNOWN_FAILURE	Error of last-resort when no specific error was caught	TBD	Should be investigated by Trader API team. Please contact TraderAPI@Schwab.com if you see this with the `schwabClientCorrelId` of subscription.
11	SERVICE_NOT_AVAILABLE	The service is not available	No	Should be investigated by Trader API team. Please contact TraderAPI@Schwab.com if you see this with the `schwabClientCorrelId` of subscription. Either client is requesting an unsupported service or the service is not running from the source.
12	CLOSE_CONNECTION	You've reached the maximum number of connections allowed.	Yes	Client to determine if max connections are expected and proper response to customer. A limit of 1 Streamer connection at any given time from a given user is available.
19	REACHED_SYMBOL_LIMIT	Subscribe or Add command has reached a total subscription symbol limit	No	Client to determine if symbol limit is expected and proper response to customer.
20	STREAM_CONN_NOT_FOUND	No connection found for user or new session but no login request	TBD	

Server cannot find the connection based on the provided SchwabClientCustomerId & SchwabClientCorrelId in the request.Should be investigated by Trader API team. Please contact TraderAPI@Schwab.com if you see this with the `schwabClientCorrelId` of subscription.
Common causes:
 

Client does not wait for a successful LOGIN response and issues a command immediately after the LOGIN command. There could be a race condition where the SUB is processed before the LOGIN.
Client modifies SchwabClientCustomerId or SchwabClientCorrelId after logging in.
Streamer has disconnected the client while processing the command.

21	BAD_COMMAND_FORMAT	Command fails to match specification	No	Client should investigate why a command is not formatted properly
22	FAILED_COMMAND_SUBS	Subscribe command could not be completed successfully	No	

Should be investigated by Trader API team. Please contact TraderAPI@Schwab.com if you see this with the `schwabClientCorrelId` of subscription.
Common causes:
 

Two or more commands are processed in parallel causing one to fail.

23	FAILED_COMMAND_UNSUBS	Unsubscribe command could not be completed successfully
24	FAILED_COMMAND_ADD	Add command could not be completed successfully
25	FAILED_COMMAND_VIEW	View command could not be completed successfully
26	SUCCEEDED_COMMAND_SUBS	Subscribe command completed successfully	No	n/a - success
27	SUCCEEDED_COMMAND_UNSUBS	Unsubscribe command completed successfully
28	SUCCEEDED_COMMAND_ADD	Add command completed successfully
29	SUCCEEDED_COMMAND_VIEW	View command completed successfully
30	STOP_STREAMING	Signal that streaming has been terminated due to administrator action, inactivity, or slowness	Yes	

See message provided for details.
Common Causes:
 

Typically due to no subscriptions.


 

5. Delivery Types
 

Delivery Types	Description
All Sequence	All data is streamed to the client and includes a sequence number. Data is not conflated by the streamer although the underlying source of the data may conflate.
Change	Only fields that clients are interested in, and have changed, are streamed to the client. Data is conflated by the streamer.
Whole	Data is streamed as a whole unit to the client, in throttled mode.
All Sequence	All data is streamed to the client and includes a sequence number. Data is not conflated by the streamer although the underlying source of the data may conflate.


 

2. Admin Services

1. Login Request
 

Delivery Types	Description	Type	Length	Description
service	 	String	Variable	ADMIN
command	 	String	Variable	LOGIN
requestid	 	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	 	String	Variable	`schwabClientCustomerId` as found in GET User Preference endpoint
SchwabClientCorrelId	 	String	Variable	Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	Authorization	String	Variable	Access token as found from POST Token endpoint.
SchwabClientChannel	String	2	Identifies the channel as found through the GET User Preferences endpoint.
SchwabClientFunctionId	String	5	Identifies the page or source in the channel where quote is being called from (5 alphanumeric). 
Found through the GET User Preferences endpoint.

Streamer LOGIN Request Example:
{
 "requests": [
  {
   "requestid": "1",
   "service": "ADMIN",
   "command": "LOGIN",
   "SchwabClientCustomerId": "Someone",
   "SchwabClientCorrelId": "5be0b7e7-5b8b-4fd3-9bed-7f49106cfe96",
   "parameters": {
    "Authorization": "Access Token",
    "SchwabClientChannel": "N9",
    "SchwabClientFunctionId": "APIAPP"
   }
  }
 ]
}
 


 

2. Login Response
 

Type	Request	Name	Type	Description
response	service	ADMIN	 	 

requestid	Unique request ID number	 	 

command	LOGIN	 	 

SchwabClientCorrelId	Correlation ID string passed by client	 	 

timestamp	Milliseconds since epoch	 	 

content	code	Integer	0 = Success, 3 = Login denied

msg	String	server=hostname-instance (for troubleshooting purposes)
status=PN (Non-Paying Pro)
NP (Non-Pro)
PP (Paying-Pro)
if no entitlements, client will get nfl/delayed quotes
error message if there's a login issue
 

Streamer LOGIN Response Examples:
Login Successful
{
 "response": [
  {
   "service": "ADMIN",
   "command": "LOGIN",
   "requestid": "1",
   "SchwabClientCorrelId": "5be0b7e7-5b8b-4fd3-9bed-7f49106cfe96",
   "timestamp": 1669828276886,
   "content": {
    "code": 0,
    "msg": "server=s0166bdv-1;status=PN"
   }
  }
 ]
}
 

Login Denied
{
 "response": [
  {
   "service": "ADMIN",
   "command": "LOGIN",
   "requestid": "1",
   "SchwabClientCorrelId": "5be0b7e7-5b8b-4fd3-9bed-7f49106cfe96",
   "timestamp": 1669828982588,
   "content": {
    "code": 3,
    "msg": "Login Denied.: token is invalid or has expired."
   }
  }
 ]
}
 


 

3. Logout request
 

Streamer Contract name	Type	Length	Description
service	String	Variable	ADMIN
command	String	Variable	LOGOUT
requestid	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	String	Variable	Identifies the page or source in the channel where quote is being called from (5 alphanumeric).
Found through the GET User Preferences endpoint.
SchwabClientCorrelId	String	Variable	Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	String	Variable	Can leave empty


 

4. Logout response
 

Type	Request	Name	Type	Description
response	service	ADMIN	 	 

requestid	Unique request ID number	 	 

command	LOGIN	 	 

SchwabClientCorrelId	Correlation ID string passed by client	 	 

timestamp	Milliseconds since epoch	 	 

content	code	Integer	0 = Success, 3 = Login denied

msg	String	SUCCESS, FAILURE

Streamer Logout Response Examples:
{
 "response": [
  {
   "service": "ADMIN",
   "command": "LOGOUT",
   "requestid": "0",
   "SchwabClientCorrelId": "5be0b7e7-5b8b-4fd3-9bed-7f49106cfe95",
   "timestamp": 1669830137089,
   "content": {
    "code": 0,
    "msg": "SUCCESS"
   }
  }
 ]
}
 


 

3. LEVELONE Services

1. LEVELONE_EQUITIES
 

Level One Equities Request
 

Streamer Contract name	Type	Length	Description
service	 	String	Variable	LEVELONE_EQUITIES
command	 	String	Variable	SUBS, UNSUBS, ADD, VIEW
requestid	 	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	 	String	Variable	`schwabClientCustomerId` as found in GET User Preference endpoint
SchwabClientCorrelId	 	String	Variable	Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	keys	String	Variable	Schwab-standard symbols in uppercase and separated by commas
e.g. AAPL,TSLA,IBM
fields	String	Variable	Please see the LEVELONE_EQUITIES Field Definition table below

LEVELONE_EQUITIES Request Example:
{
 "requests": [
  {
   "service": "LEVELONE_EQUITIES",
   "requestid": 1,
   "command": "SUBS",
   "SchwabClientCustomerId": "Someone",
   "SchwabClientCorrelId": "29bdf6d-b9d0-46dd-8786-424e1577bd",
   "parameters": {
    "keys": "SCHW,AAPL,SPY",
    "fields": "0,1,2,3,4,5,8,10 "
   }
  }
 ]
}
 

Response Field Definitions
Outside of fields that can be subscribed to, Streamer also returns initial data that indicates whether the data is real time or NFL (delayed).

Field Name	Type	Field Description	Notes, Examples Source
key	String	Usually this is the symbol	AAPL
delayed	boolean	Whether data is from the SIP or NFL	- false : data is from a SIP 
SIP stands for Securities Information Processor. Often considered the example for market data around the world, a SIP will collect trade and quote data from multiple exchanges and consolidate these sources into a single source of information.
- true : data is from an NFL source 
NFL stands for Non-Fee Liable. This either means the result is returning delayed data (typically options, futures and futures options) or the result is returning real-time data from a subset of exchanges and therefore does not contain all markets in the National Plan (typically equity data). Delayed quotes do not represent the most recent last or bid/ask; real-time quotes from the subset of exchanges may not contain the most recent last or bid/ask.
assetMainType	String	Asset Type	BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL_FUND, OPTION, UNKNOWN
assetSubType	String	Asset sub type	ADR, CEF, COE, ETF, ETN, GDR, OEF, PRF, RGT, UIT, WAR
cusip	String	9 digits CUSIP	CUSIP number for the instrument, such as 594918104

LEVELONE_EQUITIES Response Example:
{
 "data": [
  {
   "service": "LEVELONE_EQUITIES",
   "timestamp": 1714949592301,
   "command": "SUBS",
   "content": [
    {
     "key": "SCHW",
     "delayed": false,
     "assetMainType": "EQUITY",
     "assetSubType": "COE",
     "cusip": "808513105",
     "1": 76.08,
     "2": 76.49,
     "3": 76.44,
     "4": 3,
     "5": 1,
     "8": 5414735,
     "10": 76.47
    },
    {
     "key": "AAPL",
     "delayed": false,
     "assetMainType": "EQUITY",
     "assetSubType": "COE",
     "cusip": "037833100",
     "1": 183.75,
     "2": 183.8,
     "3": 183.8,
     "4": 1,
     "5": 2,
     "8": 163224109,
     "10": 187
    },
    {
     "key": "SPY",
     "delayed": false,
     "assetMainType": "EQUITY",
     "assetSubType": "ETF",
     "cusip": "78462F103",
     "1": 512.3,
     "2": 512.32,
     "3": 511.29,
     "4": 8,
     "5": 1,
     "8": 72756709,
     "10": 512.55
    }
   ]
  }
 ]
}
 

Fields	Field Name	Type	Field Description	Notes, Examples Source
0	Symbol	String	Ticker symbol in upper case.	 
1	Bid Price	double	Current Bid Price	 
2	Ask Price	double	Current Ask Price	 
3	Last Price	double	Price at which the last trade was matched	 
4	Bid Size	int	Number of shares for bid	Units are "lots" (typically 100 shares per lot)Note for NFL data this field can be 0 with a non-zero bid price which representing a bid size of less than 100 shares.
5	Ask Size	int	Number of shares for ask	See bid size notes.
6	Ask ID	char	Exchange with the ask	 
7	Bid ID	char	Exchange with the bid	 
8	Total Volume	long	Aggregated shares traded throughout the day, including pre/post market hours.	Volume is set to zero at 7:28am ET.
9	Last Size	long	Number of shares traded with last trade	Units are shares
10	High Price	double	Day's high trade price	According to industry standard, only regular session trades set the High and Low
If a stock does not trade in the regular session, high and low will be zero.
High/low reset to ZERO at 3:30am ET
11	Low Price	double	Day's low trade price	See High Price notes
12	Close Price	double	Previous day's closing price	Closing prices are updated from the DB at 3:30 AM ET.
13	Exchange ID	char	Primary "listing" Exchange	

As long as the symbol is valid, this data is always present
This field is updated every time the closing prices are loaded from DB
 

Exchange	Code	Realtime/NFL
AMEX	A	Both
Indicator	:	Realtime Only
Indices	0	Realtime Only
Mutual Fund	3	Realtime Only
NASDAQ	Q	Both
NYSE	N	Both
Pacific	P	Both
Pinks	9	Realtime Only
OTCBB	U	Realtime Only

14	Marginable	boolean	Stock approved by the Federal Reserve and an investor's broker as being eligible for providing collateral for margin debt.	 
15	Description	String	A company, index or fund name	Once per day descriptions are loaded from the database at 7:29:50 AM ET.
16	Last ID	char	Exchange where last trade was executed	 
17	Open Price	double	Day's Open Price According to industry standard, only regular session trades set the open.
If a stock does not trade during the regular session, then the open price is 0.
In the pre-market session, open is blank because pre-market session trades do not set the open. 
Open is set to ZERO at 3:30am ET.
18	Net Change	double	 	LastPrice - ClosePrice
If close is zero, change will be zero
19	52 Week High	double	Higest price traded in the past 12 months, or 52 weeks	Calculated by merging intraday high (from fh) and 52-week high (from db)
20	52 Week Low	double	Lowest price traded in the past 12 months, or 52 weeks	Calculated by merging intraday low (from fh) and 52-week low (from db)
21	PE Ratio	double	Price-to-earnings ratio. 
The P/E equals the price of a share of stock, divided by the company's earnings-per-share.	Note that the "price of a share of stock" in the definition does update during the day so this field has the potential to stream. However, the current implementation uses the closing price and therefore does not stream throughout the day.
22	Annual Dividend Amount	double	Annual Dividend Amount	 
23	Dividend Yield	double	Dividend Yield	 
24	NAV	double	Mutual Fund Net Asset Value	Load various times after market close
25	Exchange Name	String	Display name of exchange	 
26	Dividend Date	String	 	 
27	Regular Market Quote	boolean	 	Is last quote a regular quote
28	Regular Market Trade	boolean	 	Is last trade a regular trade
29	Regular Market Last Price	double	 	Only records regular trade
30	Regular Market Last Size	integer	 	Currently realize/100, only records regular trade
31	Regular Market Net Change	double	 	RegularMarketLastPrice - ClosePrice
32	Security Status	String	 	Indicates a symbols current trading status, Normal, Halted, Closed
33	Mark Price	double	Mark Price	 
34	Quote Time in Long	Long	Last time a bid or ask updated in milliseconds since Epoch	The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC.
35	Trade Time in Long	Long	Last trade time in milliseconds since Epoch	The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC.
36	Regular Market Trade Time in Long	Long	Regular market trade time in milliseconds since Epoch	The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC.
37	Bid Time	long	Last bid time in milliseconds since Epoch	The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC.
38	Ask Time	long	Last ask time in milliseconds since Epoch	The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC.
39	Ask MIC ID	String	4-chars Market Identifier Code	 
40	Bid MIC ID	String	4-chars Market Identifier Code	 
41	Last MIC ID	String	4-chars Market Identifier Code	 
42	Net Percent Change	double	Net Percentage Change	NetChange / ClosePrice * 100
43	Regular Market Percent Change	double	Regular market hours percentage change	RegularMarketNetChange / ClosePrice * 100
44	Mark Price Net Change	double	Mark price net change	7.97
45	Mark Price Percent Change	double	Mark price percentage change	4.2358
46	Hard to Borrow Quantity	integer	 	-1 = NULL
>= 0 is valid quantity
47	Hard To Borrow Rate	double	 	null = NULL
valid range = -99,999.999 to +99,999.999
48	Hard to Borrow	integer	 	-1 = NULL
1 = true
0 = false
49	shortable	integer	 	-1 = NULL
1 = true
0 = false
50	Post-Market Net Change	double	Change in price since the end of the regular session (typically 4:00pm)	PostMarketLastPrice - RegularMarketLastPrice
51	Post-Market Percent Change	double	Percent Change in price since the end of the regular session (typically 4:00pm)	PostMarketNetChange / RegularMarketLastPrice * 100


 

2. LEVELONE_OPTIONS
 

Please refer to LEVELONE_EQUITIES for REQUESTS and RESPONSE examples. Replace LEVELONE_EQUITIES with LEVELONE_OPTIONS.

Level One Options Request
 

Streamer Contract name	 	Type	Length	Description
service	 	String	Variable	LEVELONE_OPTIONS
command	 	String	Variable	SUBS, UNSUBS, ADD, VIEW
requestid	 	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	 	String	Variable	`schwabClientCustomerId` as found in GET User Preference endpoint
SchwabClientCorrelId	 	String	Variable	Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	keys	String	Variable	

Options symbols in uppercase and separated by commas
Schwab-standard option symbol format:
RRRRRRYYMMDDsWWWWWddd
Where:
 

R is the space-filled root
symbol YY is the expiration year
MM is the expiration month
DD is the expiration day
s is the side: C/P (call/put)
WWWWW is the whole portion of the strike price
nnn is the decimal portion of the strike price


e.g.: AAPL  251219C00200000


fields	String	Variable	Please see the LEVELONE_OPTIONS Field Definition table below

Response Field Definitions
 

Streamer Contract name	 	Type	Length	Description
0	Symbol	String	Ticker symbol in upper case.	N/A	N/A	 
1	Description	String	A company, index or fund name	Yes	Yes	Descriptions are loaded from the database daily at 3:30 am ET.
2	Bid Price	double	Current Bid Price	Yes	No	 
3	Ask Price	double	Current Ask Price	Yes	No	 
4	Last Price	double	Price at which the last trade was matched	Yes	No	 
5	High Price	double	Day's high trade price	Yes	No	According to industry standard, only regular session trades set the High and Low.
If a stock does not trade in the regular session, high and low will be zero.
High/low reset to zero at 3:30am ET
 
6	Low Price	double	Day's low trade price	Yes	No	See High Price notes
7	Close Price	double	Previous day's closing price	No	No	Closing prices are updated from the DB at 7:29AM ET.
8	Total Volume	long	Aggregated contracts traded throughout the day, including pre/post market hours.	Yes	No	Volume is set to zero at 3:30am ET.
9	Open Interest	int	 	Yes	No	 
10	Volatility	double	Option Risk/Volatility Measurement/Implied	Yes	No	Volatility is reset to 0 at 3:30am ET
11	Money Intrinsic Value	double	The value an option would have if it were exercised today. Basically, the intrinsic value is the amount by which the strike price of an option is profitable or in-the-money as compared to the underlying stock's price in the market.	Yes	No	In-the-money is positive, out-of-the money is negative.
12	Expiration Year	int	 	 	 	 
13	Multiplier	double	 	 	 	 
14	Digits	int	Number of decimal places	 	 	 
15	Open Price	double	Day's Open Price Yes No According to industry standard, only regular session trades set the open
If a stock does not trade during the regular session, then the open price is 0.
In the pre-market session, open is blank because pre-market session trades do not set the open.
Open is set to ZERO at 7:28 ET.	 	 	 
16	Bid Size	int	Number of contracts for bid	Yes	No	From FH
17	Ask Size	int	Number of contracts for ask	Yes	No	From FH
18	Last Size	int	Number of contracts traded with last trade	Yes	No	Size in 100's
19	Net Change	double	Current Last-Prev Close	Yes	No	If(close>0)
change = last â€“ close
Else change=0
20	Strike Price	double	Contract strike price	Yes	No	 
21	Contract Type	char	 	 	 	 
22	Underlying	String	 	 	 	 
23	Expiration Month	int<	 	 	 	 
24	Deliverables	String	 	 	 	 
25	Time Value	double	 	 	 	 
26	Expiration Day	int	 	 	 	 
27	Days to Expiration	int	 	 	 	 
28	Delta	double	 	 	 	 
29	Gamma	double	 	 	 	 
30	Theta	double	 	 	 	 
31	Vega	double	 	 	 	 
32	Rho	double	 	 	 	 
33	Security Status	String	 	Yes	Yes	Indicates a symbol's current trading status: Normal, Halted, Closed
34	Theoretical Option Value	double	 	 	 	 
35	Underlying Price	double	 	 	 	 
36	UV Expiration Type	char	 	 	 	 
37	Mark Price	double	Mark Price	Yes	Yes	 
38	Quote Time in Long	long	Last quote time in milliseconds since Epoch	Yes	Yes The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC.
39	Trade Time in Long	long	Last trade time in milliseconds since Epoch	Yes	Yes	The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC.
40	Exchange	char	Exchange character	Yes	Yes	o
41	Exchange Name	String	Display name of exchange	Yes	Yes	 
42	Last Trading Day	long	Last Trading Day	Yes	Yes	 
43	Settlement Type	char	Settlement type character	Yes	Yes	 
44	Net Percent Change	double	Net Percentage Change	Yes	Yes	4.2358
45	Mark Price Net Change	double	Mark price net change	Yes	Yes	7.97
46	Mark Price Percent Change	double	Mark price percentage change	Yes	Yes	4.2358
47	Implied Yield	double	 	 	 	 
48	isPennyPilot	boolean	 	 	 	 
49	Option Root	String	 	 	 	 
50	52 Week High	double	 	 	 	 
51	52 Week Low	double	 	 	 	 
52	Indicative Ask Price	double	 	 	 	Only valid for index options (0 for all other options)
53	Indicative Bid Price	double	 	 	 	Only valid for index options (0 for all other options)
54	Indicative Quote Time	long	The latest time the indicative bid/ask prices updated in milliseconds since Epoch	 	Only valid for index options (0 for all other options)
The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC.	 
55	Exercise Type	char	 	 	 	 


 

3. LEVELONE_FUTURES
 

Please refer to LEVELONE_EQUITIES for REQUESTS and RESPONSE examples. Replace LEVELONE_EQUITIES with LEVELONE_FUTURES.

Level One Futures Fields for Streamer
 

Streamer Contract name	 	Type	Length	Description
service	 	String	Variable	LEVELONE_FUTURES
command	 	String	Variable	SUBS, UNSUBS, ADD, VIEW
requestid	 	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	 	String	Variable	`schwabClientCustomerId` as found in GET User Preference endpoint
SchwabClientCorrelId	 	String	Variable	Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	keys	String	Variable	

Futures symbols in upper case and separated by commas.
Schwab-standard format:
'/' + 'root symbol' + 'month code' + 'year code'
where month code is:
 

F: January
G: February
H: March
J: April
K: May
M: June
N: July
Q: August
U: September
V: October
X: November
Z: December


and year code is the last two digits of the year
Common roots:
 

ES: E-Mini S&P 500
NQ: E-Mini Nasdaq 100
CL: Light Sweet Crude Oil
GC: Gold
HO: Heating Oil
BZ: Brent Crude Oil
YM: Mini Dow Jones Industrial Average

fields	String	Variable	Please see the LEVELONE_FUTURES Field Definition table below

Response Field Definitions
 

Field	Field Name	Type	Field Description	Update Regular Hours	Update AM/PM Hours	Notes, Examples Source
0	Symbol	 	String	Ticker symbol in upper case.	N/A	N/A
1	Bid Price	double	Current Best Bid Price	Yes	Yes	 
2	Ask Price	double	Current Best Ask Price	Yes	Yes	 
3	Last Price	double	Price at which the last trade was matched	Yes	Yes	 
4	Bid Size	long	Number of contracts for bid	Yes	Yes	 
5	Ask Size	long	Number of contracts for ask	Yes	Yes	 
6	Bid ID	char	Exchange with the best bid	Yes	Yes Currently "?" for unknown as all quotes are CME
7	Ask ID	char	Exchange with the best ask	Yes	Yes	Currently "?" for unknown as all quotes are CME
8	Total Volume	long	Aggregated contracts traded throughout the day, including pre/post market hours.	Yes	 	Yes
9	Last Size	long	Number of contracts traded with last trade	Yes	Yes	 
10	Quote Time	long	Time of the last quote in milliseconds since epoch	Yes	Yes	 
11	Trade Time	long	Time of the last trade in milliseconds since epoch	Yes	Yes	 
12	High Price	double	Day's high trade price	Yes	Yes	 
13	Low Price	double	Day's low trade price	Yes	Yes	 
14	Close Price	double	Previous day's closing price	N/A	N/A	 
15	Exchange ID	char	Primary "listing" Exchange	N/A	N/A	Currently "?" for unknown as all quotes are CME
16	Description	String	Description of the product	N/A	N/A	 
17	Last ID	char	Exchange where last trade was executed	Yes	Yes	 
18	Open Price	double	Day's Open Price	Yes	Yes	 
19	Net Change	double	Current Last-Prev Close	Yes	Yes	If(close>0)
change = last â€“ close
else change=0
20	Future Percent Change	double	Current percent change	Yes	Yes	If(close>0)
pctChange = (last â€“ close)/close
else pctChange=0
21	Exchange Name	String	Name of exchange	 	 
22	Security Status	String	Trading status of the symbol	Yes	Yes	Indicates a symbols current trading status, Normal, Halted, Closed
23	Open Interest	int	The total number of futures contracts that are not closed or delivered on a particular day	Yes	Yes	 
24	Mark	double	Mark-to-Market value is calculated daily using current prices to determine profit/loss	Yes	Yes	If lastprice is within spread, 
value = lastprice
else
value=(bid+ask)/2
25	Tick	double	Minimum price movement	N/A	N/A	Minimum price increment of contract
26	Tick Amount	double	Minimum amount that the price of the market can change	N/A	N/A	Tick * multiplier field
27	Product	String	Futures product	N/A	N/A	From Database
28	Future Price Format	String	Display in fraction or decimal format. N/A N/A Set from FSP Config
format is \< numerator decimals to display\>, \< implied denominator>
where D=decimal format, no fractional display
Equity futures will be "D,D" to indicate pure decimal.
Fixed income futures are fractional, typically "3,32".
Below is an example for "3,32": 
price=101.8203125
=101 + 0.8203125 (split into whole and fractional)
=101 + 26.25/32 (Multiply fractional by implied denomiator)
=101 + 26.2/32 (round to numerator decimals to display)
=101'262 (display in fractional format)	 	 	 
29	Future Trading Hours	String	Trading hours	N/A	N/A	days: 0 = monday-friday, 1 = sunday, 
7 = Saturday
0 = [-2000,1700] ==> open, close
1= [-1530,-1630,-1700,1515] ==> open, close, open, close
0 = [-1800,1700,d,-1700,1900] ==> open, close, DST-flag, open, close
30	Future Is Tradable	boolean	Flag to indicate if this future contract is tradable	N/A	N/A	 
31	Future Multiplier	double	Point value	N/A	N/A	 
32	Future Is Active	boolean	Indicates if this contract is active	Yes	Yes	 
33	Future Settlement Price	double	Closing price	Yes	Yes	 
34	Future Active Symbol	String	Symbol of the active contract	N/A	N/A	 
35	Future Expiration Date	long	Expiration date of this contract	N/A	N/A	Milliseconds since epoch
36	Expiration Style	String	 	 	 	 
37	Ask Time	long	Time of the last ask-side quote in milliseconds since epoch	Yes	Yes	 
38	Bid Time	long	Time of the last bid-side quote in milliseconds since epoch	Yes	Yes	 
39	Quoted In Session	boolean	Indicates if this contract has quoted during the active session	 	 	 
40	Settlement Date	long	Expiration date of this contract	N/A	N/A	Milliseconds since epoch


For more examples on Futures Price format, see: https://www.cmegroup.com/confluence/display/EPICSANDBOX/Fractional+Pricing+-+Display+Examples

If the DST-flag is present for Futures Trading Hours (field 29), please see the following hours for DST days: https://www.cmegroup.com/confluence/display/EPICSANDBOX/Fractional+Pricing+-+Display+Examples


 

4. LEVELONE_FUTURES_OPTIONS
 

Please refer to LEVELONE_EQUITIES for REQUESTS and RESPONSE examples. Replace LEVELONE_EQUITIES with LEVELONE_FUTURES_OPTIONS.

Level One Futures Options Fields for Streamer
 

Streamer Contract name	 	Type	Length	Description
service	 	String	Variable	LEVELONE_FUTURES_OPTIONS
command	 	String	Variable	SUBS, UNSUBS, ADD, VIEW
requestid	 	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	 	String	Variable	`schwabClientCustomerId` as found in GET User Preference endpoint
SchwabClientCorrelId	 	String	Variable	Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	keys	String	Variable	

Symbols in upper case and separated by commas.
Schwab-standard format:
'.' + '/' + 'root symbol' + 'month code' + 'year code' + 'Call/Put code' + 'Strike Price'
where month code is:
 

F: January
G: February
H: March
J: April
K: May
M: June
N: July
Q: August
U: September
V: October
X: November
Z: December


and year code is the last two digits of the year
e.g.: ./OZCZ23C565
 


fields	String	Variable	Please see the LEVELONE_FUTURES_OPTIONS Field Definition table below

Response Field Definitions
 

Fields	Field Name	Type	Field Description	Update Regular Hours	Update AM/PM Hours	Notes, Examples Source
0	Symbol	String	Ticker symbol in upper case.	N/A	N/A	 
1	Bid Price	double	Current Bid Price	Yes	Yes	 
2	Ask Price	double	Current Ask Price	Yes	Yes	 
3	Last Price	double	Price at which the last trade was matched	Yes	Yes	 
4	Bid Size	long	Number of contracts for bid	Yes	Yes	 
5	Ask Size	long	Number of contracts for ask	Yes	Yes	 
6	Bid ID	char	Exchange with the bid	Yes	Yes	Currently "?" for unknown as all quotes are CME
7	Ask ID	char	Exchange with the ask	Yes	Yes	Currently "?" for unknown as all quotes are CME
8	Total Volume	long	Aggregated contracts traded throughout the day, including pre/post market hours.	Yes	Yes	 
9	Last Size	long	Number of contracts traded with last trade	Yes	Yes	 
10	Quote Time	long	Trade time of the last quote in milliseconds since epoch	Yes	Yes	 
11	Trade Time	long	Trade time of the last trade in milliseconds since epoch	Yes	Yes	 
12	High Price	double	Day's high trade price	Yes	Yes-	 
13	Low Price	double	Day's low trade price	Yes	Yes	 
14	Close Price	double	Previous day's closing price	N/A	N/A	 
15	Last ID	char	Exchange where last trade was executed	Yes	Yes	Currently "?" for unknown as all quotes are CME
16	Description	String	Description of the product	N/A	N/A	 
17	Open Price	double	Day's Open Price	Yes	Yes	 
18	Open Interest	double	 	 	 	 
19	Mark	double	Mark-to-Market value is calculated daily using current prices to determine profit/loss	Yes	Yes	If lastprice is within spread, 
value = lastprice
else
value=(bid+ask)/2
20	Tick	double	Minimum price movement	N/A	N/A	Minimum price increment of contract
21	Tick Amount	double	Minimum amount that the price of the market can change	N/A	N/A	Tick * multiplier field
22	Future Multiplier	double	Point value	N/A	N/A	 
23	Future Settlement Price	double	Closing price	Yes	Yes	 
24	Underlying Symbol	String	Underlying symbol	N/A	N/A	 
25	Strike Price	double	Strike Price	 	 	 
26	Future Expiration Date	long	Expiration date of this contract	N/A	N/A	Milliseconds since epoch
27	Expiration Style	String	 	 	 	 
28	Contract Type	Char	 	 	 	 
29	Security Status	String	 	Yes	Yes	Indicates a symbol's current trading status: Normal, Halted, Closed
30	Exchange	char	Exchange character	Yes	Yes	 
31	Exchange Name	String	Display name of exchange	Yes	Yes	 


 

5. LEVELONE_FOREX
 

Please refer to LEVELONE_EQUITIES for REQUESTS and RESPONSE examples. Replace LEVELONE_EQUITIES with LEVELONE_FOREX.

Level One Forex Request for Streamer
 

Streamer Contract name	 	Type	Length	Description
service	 	String	Variable	LEVELONE_FOREX
command	 	String	Variable	SUBS, UNSUBS, ADD, VIEW
requestid	 	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	 	String	Variable	`schwabClientCustomerId` as found in GET User Preference endpoint
SchwabClientCorrelId	 	String	Variable	Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	keys	String	Variable	Symbols in upper case and separated by commas.
e.g.: EUR/USD,USD/JPY,AUD/CAD
fields	String	Variable	Please see the LEVELONE_FOREX Field Definition table below

Response Field Definitions
 

Fields	Field Name	Type	Field Description	Update Regular Hours	Update AM/PM Hours	Notes, Examples Source
0	Symbol	String	Ticker symbol in upper case.	N/A	N/A	 
1	Bid Price	double	Current Bid Price	Yes	Yes	 
2	Ask Price	double	Current Ask Price	Yes	Yes	 
3	Last Price d	ouble	Price at which the last trade was matched	Yes	Yes	 
4	Bid Size	long	Number of currency pairs for bid	Yes	Yes	 
5	Ask Size	long	Number of currency pairs for ask	Yes	Yes	 
6	Total Volume	long	Aggregated currency pairs traded throughout the day, including pre/post market hours.	Yes	Yes	 
7	Last Size	long	Number of currency pairs traded with last trade	Yes	Yes	 
8	Quote Time	long	Trade time of the last quote in milliseconds since epoch	Yes	Yes	 
9	Trade Time	long	Trade time of the last trade in milliseconds since epoch	Yes	Yes	 
10	High Price	double	Day's high trade price	Yes	Yes	 
11	Low Price d	ouble	Day's low trade price	Yes	Yes	 
12	Close Price	double	Previous day's closing price	N/A	N/A	 
13	Exchange	char	 	 	 
14	Description	String	Description of the product	N/A	N/A	 
15	Open Price	double	Day's Open Price	Yes	Yes	 
16	Net Change	double	Current Last-Prev Close	Yes	Yes	If(close>0)
change = last â€“ close
else change=0
17	Percent Change	double	Current percent change	Yes	Yes	If(close>0)
pctChange = (last â€“ close)/close
else pctChange=0
18	Exchange Name	String	Name of exchange	N/A	N/A	 
19	Digits	Int	Valid decimal points	N/A	N/A	 
20	Security Status	String	Trading status of the symbol	Yes	Yes	Indicates a symbols current trading status, Normal, Halted, Closed
21	Tick	double	Minimum price movement	N/A	N/A	Minimum price increment for pair
22	Tick Amount	double	Minimum amount that the price of the market can change	N/A	N/A	Tick * multiplier field from database
23	Product	String	Product name	N/A	N/A	 
24	Trading Hours	String	Trading hours	N/A	N/A	 
25	Is Tradable	boolean	Flag to indicate if this forex is tradable	N/A	N/A	 
26	Market Maker	String	 	 	 	 
27	52 Week High	double	Higest price traded in the past 12 months, or 52 weeks	Yes	Yes	 
28	52 Week Low	double	Lowest price traded in the past 12 months, or 52 weeks	Yes	Yes	 
29	Mark	double	Mark-to-Market value is calculated daily using current prices to determine profit/loss	Yes	Yes	 


 

4. BOOK Services

1. Book Common
 

Streamer Contract name	 	Type	Length	Description
service	 	String	Variable	NYSE_BOOK, NASDAQ_BOOK, OPTIONS_BOOK
command	 	String	Variable	SUBS, UNSUBS, ADD, VIEW
requestid	 	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	 	String	Variable	`schwabClientCustomerId` as found in GET User Preference endpoint
SchwabClientCorrelId	 	String	Variable	Unique identifiervalue that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	keys	String	Variable	Symbols in upper case and separated by commas.
e.g.: AAPL,TSLA,IBM
fields	String	Variable	Please see the BOOK Field Definition table below

Response field definitions

Book Fields for Streamer
 

Fields	Field Name	Value	Type	Description
0	Symbol	Ticker symbol in upper case.	String	 
1	Market Snapshot Time	Milliseconds since Epoch	long	Timestamp for the data
2	Bid Side Levels	Price Levels	Array	Bid side price levels
3	Ask Side Levels	Price Levels	Array	Ask side price levels

Book Price Levels Sub-Field for Streamer
 

Price Levels 
Field #	Field Name	Type	Description
0	Price	double	Price for this level
1	Aggregate Size	int	Aggregate size for this price level
2	Market Maker Count	int	Number of Market Makers in this price level
3	Array of Market Makers	Array	Array of market maker sizes for this price level

Book Market Makers Sub-Field for Streamer
 

Market Makers
Field #	Field Name	Type	Description
0	Market Maker ID	String	Market Maker ID
1	Size	long	Size of the Market Maker for this price level
2	Quote Time	long	Quote time in milliseconds for this Market Maker's quote


 

5. CHART Services

1. CHART_EQUITY
 

Chart Equity Request for Streamer
 

Streamer Contract name	 	Type	Length	Description
service	 	String	Variable	CHART_EQUITY
command	 	String	Variable	SUBS, UNSUBS, ADD, VIEW
requestid	 	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	 	String	Variable	'schwabClientCustomerId' as found in GET User Preference endpoint
SchwabClientCorrelId	 	String	Variable	Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	keys	String	Variable	Equities symbols in upper case and separated by commas.
e.g.: AAPL,TSLA,IBM
 
fields	String	Variable	Please see the CHART_EQUITY Field Definition table below

Response field definitions
 

Fields	Field Name	Type	Field Description	Update Regular Hours	Update AM/PM Hours	Notes, Examples Source
0	key	String	Ticker symbol in upper case.	N/A	N/A	 
1	Open Price	double	Opening price for the minute	Yes	Yes	 
2	High Price	double	Highest price for the minute	Yes	Yes	 
3	Low Price	double	Chart's lowest price for the minute	Yes	Yes	 
4	Close Price	double	Closing price for the minute	Yes	Yes	 
5	Volume	double	Total volume for the minute	Yes	Yes	 
6	Sequence	long	Identifies the candle minute	Yes	Yes	 
7	Chart Time	long	Milliseconds since Epoch	Yes	Yes	 
8	Chart Day	int	 	 	 	 


 

2. CHART_FUTURES
 

Chart Futures Request for Streamer
 

Streamer Contract name	 	Type	Length	Description
service	 	String	Variable	CHART_FUTURES
command	 	String	Variable	SUBS, UNSUBS, ADD, VIEW
requestid	 	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	 	String	Variable	'schwabClientCustomerId' as found in GET User Preference endpoint
SchwabClientCorrelId	 	String	Variable	Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	keys	String	Variable	

Futures symbols in upper case and separated by commas
Schwab-standard format:
'/' + 'root symbol' + 'month code' + 'year code'
where month code is:
 

F: January
G: February
H: March
J: April
K: May
M: June
N: July
Q: August
U: September
V: October
X: November
Z: December


and year code is the last two digits of the year
Common roots:
 

ES: E-Mini S&P 500
NQ: E-Mini Nasdaq 100
CL: Light Sweet Crude Oil
GC: Gold
HO: Heating Oil
BZ: Brent Crude Oil
YM: Mini Dow Jones Industrial Average


 


fields	String	Variable	Please see the CHART_FUTURES Field Definition table below

Field response definitions
 

Fields	Field Name	Type	Field Description	Update Regular Hours	Update AM/PM Hours	Notes, Examples Source
0	key	String	Ticker symbol in upper case.	N/A	N/A	 
1	Chart Time	long	Milliseconds since Epoch	Yes	Yes	 
2	Open Price	double	Opening price for the minute	Yes	Yes	 
3	High Price	double	Highest price for the minute	Yes	Yes	 
4	Low Price	double	Chart's lowest price for the minute	Yes	Yes	 
5	Close Price	double	Closing price for the minute	Yes	Yes	 
6	Volume	double	Total volume for the minute	Yes	Yes	 


 

6. SCREENER services

1. Screener Common
 

Screener Request for Streamer
 

Streamer Contract name	 	Type	Length	Description
service	 	String	Variable	SCREENER_EQUITY, SCREENER_OPTION
command	 	String	Variable	SUBS, UNSUBS, ADD, VIEW
requestid	 	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	 	String	Variable	'schwabClientCustomerId' as found in GET User Preference endpoint
SchwabClientCorrelId	 	String	Variable	Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	keys	String	Variable	

Symbols in upper case and separated by commas.
(PREFIX)_(SORTFIELD)_(FREQUENCY) where PREFIX is:
 

Indices: $COMPX $DJI, $SPX, INDEX_ALL
Exchanges: NYSE, NASDAQ, OTCBB, EQUITY_ALL
Option: OPTION_PUT, OPTION_CALL, OPTION_ALL


and sortField is:

VOLUME, TRADES, PERCENT_CHANGE_UP, PERCENT_CHANGE_DOWN, AVERAGE_PERCENT_VOLUME


and frequency is:

0, 1, 5, 10, 30 60 minutes (0 is for all day)


 


fields	String	Variable	Please see the SCREENER Field Definition table below

Response field definitions

Index	Field	Type	Description	Values
0	symbol	String	The symbol used to look up either actives, gainers or losers	Subscribed or requested symbol

1	timestamp	long	Market snapshot timestamp in milliseconds since Epoch	12345613123

2	sortField	String	Field to sort on	VOLUME, TRADES, PERCENT_CHANGE_UP, PERCENT_CHANGE_DOWN, AVERAGE_PERCENT_VOLUME

3	frequency	Integer	Frequency of data to sort	0, 1, 5, 10, 30 60 minutes (0 is for all day)

4	Items	Array	 	Refer to the field table below



 

Field	Type	Description
description	String	Description of instrument

lastPrice	double	Last trade price (up to 2 decimal places)

marketShare	double	Market share percentage of instrument (up to 2 decimal places)

netChange	double	Net change value (up to 2 decimal places)

netPercentChange	double	Net percent change value (up to 4 decimal places)

symbol	String	Stock or Option symbol

totalVolume	long	Total volume for the day

trades	long	Number of trades for the frequency requested

volume	long	Volume for the frequency requested



 

7. ACCOUNT services

1. ACCT_ACTIVITY
 

Account Activity Request for Streamer
 

Streamer Contract name	 	Type	Length	Description
service	 	String	Variable	ACCOUNT_ACTIVITY
command	 	String	Variable	SUBS, UNSUBS
requestid	 	Integer	Variable	Unique number that will identify this request.
SchwabClientCustomerId	 	String	Variable	'schwabClientCustomerId' as found in GET User Preference endpoint
SchwabClientCorrelId	 	String	Variable	Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain.
parameters	keys	String	Variable	A client-provided string that streamer will populate updates with. Only first key is used if multiple are provided.
fields	String	Variable	"0" expected

Example:
{
 "requests": [
  {
   "service": "ACCT_ACTIVITY",
   "requestid": "2",
   "command": "SUBS",
   "SchwabClientCustomerId": "Someone",
   "SchwabClientCorrelId": "f308b89-19a7-2d18-4a0a-1c5e7120336",
   "parameters": {
    "keys": "Account Activity",
    "fields": "0,1,2,3"
   }
  }
 ]
}
 

Response
 

Fields	Field Name	Type	Value
"seq"	Sequence	Integer	This field identifies the message number. If client reconnects and receives the same seq number again, it can choose to ignore the duplicate.
"key"	Key	String	Passed back to the client from the request to identify a subscription this response belongs to.
1	Account	String	Account Number that the activity occurred on.
2	Message Type	String	Message Type that dictates the format of the Message Data field.
3	Message Data	String	The core data for the message. Either JSON-formatted data describing the update, NULL in some cases, or plain text in case of ERROR.


 

Terms Of Use
|
Privacy Notice

© 2026 Charles Schwab & Co., Inc. All rights reserved. Member SIPC. Unauthorized access is prohibited. Usage is monitored.

```
