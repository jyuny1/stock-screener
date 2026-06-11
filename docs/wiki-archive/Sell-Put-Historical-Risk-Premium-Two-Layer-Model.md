# Sell Put 歷史風險與權利金補償兩層模型

> 指標中英文名稱、公式與白話解釋請見：[Sell Put Risk CLI 指標中英文解釋](Sell-Put-Risk-CLI-Metrics.md)。

> 本文件只定義前兩層模型：
> 1. 歷史風險基準
> 2. 權利金補償
>
> 不處理財報、新聞、技術支撐、流動性、部位大小或是否下單。

---

## 目的

Schwab API 不提供 thinkorswim 類似的 `Prob ITM` / `Prob OTM` 欄位。

所以本模型不依賴 Schwab 直接給機率，而是：

```text
用 Schwab 提供的 option chain 與歷史價格，自己計算：
過去同樣天數內，這個 strike 對應的跌幅發生過幾次；
再看今天收到的權利金是否足夠補償歷史平均賠付。
```

簡單說：

> 把每個 Put 當成一張保險。先看歷史上同樣天數內災害發生多常、平均賠多少，再看今天市場給的保費夠不夠。

---

## 已確認 Schwab API 可用資料

依據 `docs/system_design/schwab_api.md`，Schwab Market Data API 可提供：

### 1. 現價資料

來源：`/quotes`

可用於取得：

```text
underlying current price / mark / last
quote time
trade time
```

### 2. Option chain

來源：`/chains`

可用欄位包含：

```text
strikePrice
expirationDate
daysToExpiration
bidPrice
askPrice
markPrice
lastPrice
totalVolume
openInterest
delta
volatility
theoreticalOptionValue
isInTheMoney
```

### 3. 歷史價格

來源：`/pricehistory`

可用 candle 欄位：

```text
open
high
low
close
volume
datetime
```

### 4. 不可用資料

目前文件沒有 Schwab 直接提供的：

```text
Prob ITM
Prob OTM
```

因此本模型不使用 Schwab Prob ITM / OTM。

---

## 模型只回答兩個問題

### 問題 1：歷史上有多危險？

```text
過去同樣 DTE 的時間窗口內，這個 Put 對應的跌幅發生過幾次？
```

輸出：

```text
Hist ITM Prob
Hist Touch Prob
Hist Loss Prob
Hist Avg Payout
```

### 問題 2：權利金夠不夠？

```text
今天收到的權利金，是否高於歷史平均賠付？
```

輸出：

```text
Seller EV
```

---

## 基本例子

假設：

```text
現在股價 S0 = 100
Put Strike K = 90
Bid Credit = 1
DTE = 14 天
```

代表：

```text
跌到 90 以下：可能被指派
跌到 89 以下：到期真正開始虧錢
```

因為：

```text
Breakeven = Strike - Credit = 90 - 1 = 89
```

所以這張 Put 對應兩個跌幅：

```text
Strike 距離 = 90 / 100 - 1 = -10%
Breakeven 距離 = 89 / 100 - 1 = -11%
```

---

## 第一層：歷史風險基準

### 為什麼用百分比，不用絕對價格？

不能問：

```text
歷史上有沒有跌破 90？
```

因為以前股價可能是 30、50、200，絕對價格不可比。

正確問題是：

```text
歷史上同樣 14 天內，有沒有跌超過 10%？
歷史上同樣 14 天內，有沒有跌超過 11%？
```

---

## 歷史窗口計算方式

對每一個歷史 rolling window：

```text
start_price = window 起始日 close
end_price = window 結束日 close
window_low = window 期間最低 low
```

計算：

```text
end_ratio = end_price / start_price
low_ratio = window_low / start_price
```

對目前 Put：

```text
strike_ratio = K / S0
breakeven_ratio = (K - Credit) / S0
```

---

## 三個歷史機率

### 1. Hist ITM Prob

問題：

```text
到期時，有沒有跌破 strike？
```

公式：

```text
Hist ITM Prob =
count(end_ratio < strike_ratio) / total_windows
```

白話：

```text
以前同樣天數內，有幾次最後會被指派？
```

---

### 2. Hist Touch Prob

問題：

```text
期間內，有沒有曾經碰到 strike？
```

公式：

```text
Hist Touch Prob =
count(low_ratio < strike_ratio) / total_windows
```

白話：

```text
以前同樣天數內，有幾次中途會被打到？
```

---

### 3. Hist Loss Prob

問題：

```text
到期時，有沒有跌破 breakeven？
```

公式：

```text
Hist Loss Prob =
count(end_ratio < breakeven_ratio) / total_windows
```

白話：

```text
以前同樣天數內，有幾次最後真的虧錢？
```

---

## Hist Avg Payout

除了算發生幾次，還要算平均賠多少。

每個歷史窗口先轉成今天的價格尺度：

```text
simulated_ST = S0 * end_ratio
```

然後計算如果到期在這個價格，Put 賣方要賠多少：

```text
payout = max(K - simulated_ST, 0)
```

最後取平均：

```text
Hist Avg Payout = average(payout)
```

白話：

```text
如果以前每一段同樣天數都賣這種 Put，平均每次會賠多少。
```

---

## 第二層：權利金補償

第一版使用：

```text
Credit = bidPrice
```

原因：

```text
bidPrice 是較保守、較接近可立即成交的賣方權利金。
```

核心公式：

```text
Seller EV = bidPrice - Hist Avg Payout
```

解讀：

```text
Seller EV > 0：
從歷史平均賠付角度看，今天權利金高於歷史平均風險。

Seller EV < 0：
從歷史平均賠付角度看，今天權利金不足以補償歷史平均風險。
```

---

## 最小輸出欄位

每個 Put contract 輸出：

| 欄位 | 意義 | 來源 |
|---|---|---|
| Symbol | 股票代碼 | input |
| Expiration | 到期日 | Schwab `/chains` |
| DTE | 到期天數 | Schwab `/chains` |
| S0 | 現價 | Schwab `/quotes` 或 `/chains` underlying quote |
| Strike | 履約價 | Schwab `/chains` |
| Bid | 可收權利金 | Schwab `/chains` |
| Ask | 買回參考價 | Schwab `/chains` |
| Breakeven | `Strike - Bid` | 自算 |
| Strike Distance | `Strike / S0 - 1` | 自算 |
| Breakeven Distance | `Breakeven / S0 - 1` | 自算 |
| Hist ITM Prob | 歷史到期跌破 strike 頻率 | 自算 |
| Hist Touch Prob | 歷史期間碰到 strike 頻率 | 自算 |
| Hist Loss Prob | 歷史到期跌破 breakeven 頻率 | 自算 |
| Hist Avg Payout | 歷史平均賠付 | 自算 |
| Seller EV | `Bid - Hist Avg Payout` | 自算 |
| Sample Size | rolling window 數量 | 自算 |

---

## 最小演算法

```text
對每個 symbol：

1. 用 Schwab /quotes 取得現價 S0

2. 用 Schwab /chains 取得 Put option chain

3. 用 Schwab /pricehistory 取得歷史 daily OHLC

4. 對每個 Put：
   K = strikePrice
   Credit = bidPrice
   Breakeven = K - Credit

   strike_ratio = K / S0
   breakeven_ratio = Breakeven / S0

5. 依照該 Put 的 DTE 建立歷史 rolling windows

6. 對每個 window：
   end_ratio = end_close / start_close
   low_ratio = window_low / start_close

7. 統計：
   Hist ITM Prob = end_ratio < strike_ratio 的比例
   Hist Touch Prob = low_ratio < strike_ratio 的比例
   Hist Loss Prob = end_ratio < breakeven_ratio 的比例

8. 計算：
   simulated_ST = S0 * end_ratio
   payout = max(K - simulated_ST, 0)
   Hist Avg Payout = average(payout)
   Seller EV = bidPrice - Hist Avg Payout
```

---

## 這版不做什麼

本文件只定義第一與第二層，因此不處理：

```text
財報風險
新聞風險
支撐 / 壓力
技術型態
IV 是否合理
Delta 是否高低
流動性篩選
部位大小
是否應該下單
```

因此模型輸出不能寫：

```text
建議賣出這張 Put
```

只能寫：

```text
從歷史風險與權利金補償角度，這張 Put 的補償較好 / 較差。
```

---

## 最後濃縮

> 第一層問：以前同樣天數內，這個跌幅發生多常、平均賠多少？
>
> 第二層問：今天 Schwab option chain 顯示的 bid 權利金，是否高於歷史平均賠付？

如果：

```text
Seller EV = bidPrice - Hist Avg Payout > 0
```

代表：

```text
至少從歷史平均風險角度看，今天的權利金有補償。
```

但這仍不等於可以交易，因為事件、新聞、流動性與部位風險尚未納入。
