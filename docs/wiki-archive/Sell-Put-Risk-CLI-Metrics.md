# Sell Put Risk CLI 指標中英文解釋

> 適用工具：`sell-put-risk-cli` / `sell-put-risk`
>
> 預設範圍：14–28 DTE、最近一年歷史日線、Schwab option chain。這些指標是「歷史價格路徑 + 權利金補償」檢查，不是下單建議，也不是 assignment 機率模型。

---

## 核心指標

| CLI 表格欄位 | JSON key | 中文名稱 | English | 公式 / 計算方式 | 直白解釋 |
|---|---|---|---|---|---|
| `BelowK` | `expiryBelowStrikeProb` | 到期跌破履約價 | Expiry Below Strike Probability | `count(end_ratio < Strike / S0) / total_windows` | 如果一路持有到期，歷史上有多少比例最後收盤低於 strike。這不是提前 assignment 機率。 |
| `TouchK` | `touchedStrikeProb` | 期間碰到履約價 | Touched Strike Probability | `count(low_ratio < Strike / S0) / total_windows` | 持有期間中途曾跌到 strike 以下的比例；代表過程壓力，但不代表一定被 assignment。 |
| `BelowBE` | `expiryBelowBreakevenProb` | 到期跌破損益兩平 | Expiry Below Breakeven Probability | `count(end_ratio < Breakeven / S0) / total_windows` | 如果一路持有到期，歷史上有多少比例最後低於損益兩平點。這是到期損益風險，不是 assignment 機率。 |
| `AvgLoss` | `avgExpiryLoss` | 平均到期虧損 | Average Expiry Loss | `average(max(Strike - S0 * end_ratio_i, 0))` | 每次賣同樣相對距離的 Put 並持有到期，歷史平均需要補多少；包含結果為 0 的窗口。尚未扣掉收到的權利金。 |
| `PremiumEdge` | `premiumEdge` | 權利金優勢 | Premium Edge | `Bid - AvgLoss` | 今天可收的 bid 權利金，比歷史平均到期虧損多多少。正值代表從此窄模型看，權利金高於歷史平均到期成本。 |
| `Edge/K` | `premiumEdgePctOfStrike` | 權利金優勢 / 履約價 | Premium Edge as % of Strike | `PremiumEdge / Strike` | 把權利金優勢標準化成 strike 百分比，方便不同 strike 比較。 |

---

## 價格距離與輔助欄位

| CLI 表格欄位 | JSON key | 中文名稱 | English | 公式 / 計算方式 | 直白解釋 |
|---|---|---|---|---|---|
| `StrikeDist` | `strikeDistance` | 履約價距現價 | Strike Distance | `Strike / S0 - 1` | Strike 比現在價格低多少；負值代表 OTM Put。 |
| `BEDist` | `breakevenDistance` | 損益兩平距現價 | Breakeven Distance | `(Strike - Bid) / S0 - 1` | 真正跌破多少才會在到期損益上低於 breakeven。 |
| `Breakeven` | `breakeven` | 損益兩平價 | Breakeven Price | `Strike - Bid` | 不含手續費與稅務時，Put 賣方到期損益兩平價格。 |
| `Samples` | `sampleSize` | 歷史樣本數 | Historical Window Count | rolling windows count | 最近一年內能形成同樣 DTE 歷史窗口的數量。 |
| `1Y WkMDD` | `weeklyMaxDrawdown` | 一年周 K 最大回撤 | One-Year Weekly Max Drawdown | max decline from prior weekly high to later weekly low | 最近一年周 K 中，從先前高點到後續低點的最大跌幅。 |
| `Support` | `supportLevel` | 一年周 K 低點支撐 | One-Year Weekly-Low Support Proxy | lowest weekly low in lookback window | CLI 的 deterministic 支撐代理：最近一年周 K 最低 low。它不是型態辨識支撐。 |
| `K/Support` | `strikeDistanceToSupport` | 履約價距支撐 | Strike vs Support Distance | `Strike / Support - 1` | Strike 相對於一年周 K 低點支撐高多少。 |

---

## Rolling window 比例化邏輯

CLI 不用歷史絕對價格比較 strike，因為標的價格水準會變，例如槓桿 ETF 或飆漲股票可能一年內漲很多。

它使用比例化比較：

```text
strike_ratio = Strike / S0
breakeven_ratio = Breakeven / S0
end_ratio = historical_window_end_close / historical_window_start_close
low_ratio = historical_window_low / historical_window_start_close
```

所以問題不是：

```text
歷史上有沒有跌破今天的 Strike 絕對價格？
```

而是：

```text
歷史上同樣 DTE 期間，有沒有跌超過今天這張 Put 對應的相對距離？
```

---

## AvgLoss 為什麼包含 0？

`AvgLoss` 回答的是：

```text
每次賣這張 Put 並持有到期，平均需要補多少？
```

因此沒有跌破 strike 的窗口，Put 到期內在價值成本是 `0`，也必須納入平均。

如果只平均虧損窗口，回答的是另一個問題：

```text
如果真的跌破 strike，平均要補多少？
```

這不是 `PremiumEdge = Bid - AvgLoss` 要用的平均成本，因為 `Bid` 是每次賣都會收到，所以成本也要用「每次賣」的平均成本。

---

## Assignment 相關限制

這些指標不是 assignment 模型：

| 指標 | 能說什麼 | 不能說什麼 |
|---|---|---|
| `BelowK` | 到期收盤低於 strike 的歷史比例 | 不能當成提前 assignment 機率 |
| `TouchK` | 中途碰到 strike 的歷史比例 | 不能當成一定會被 assignment |
| `BelowBE` | 到期跌破 breakeven 的歷史比例 | 不能當成被 assignment 後虧損機率 |

美式 Put 可提前 exercise，但是否 exercise 取決於買方；short option 是否被分配則是 OCC / broker 層級事件。Schwab Market Data API 不提供市場整體歷史 assignment 次數，因此 CLI 只能用價格路徑做到期風險與中途壓力 proxy。
