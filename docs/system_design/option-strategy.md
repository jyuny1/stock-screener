# option-strategy 規劃

建立日期：2026-06-03

相關文件：

- [[option-analysis]]
- [[option-market-scanner]]
- [[選擇權標的選擇系統]]

---

# 一、系統定位

`option-strategy` 是 Sell Put / Wheel / 期權部位管理的策略層。

它不直接負責抓 option chain，也不負責全市場找 ticker，而是負責把候選標的與合約分析結果轉成可執行的策略規則。

```text
option-market-scanner → 找出候選 ticker
option-analysis       → 分析指定 ticker 的合約與風險
option-strategy       → 決定如何篩選、排序、配置與執行
```

---

# 二、與 option-analysis 的分工

## option-analysis 負責客觀分析

```text
- quote
- option chain
- DTE / Delta / Greeks
- bid / ask / spread
- OI / volume
- premium / breakeven / annualized return
- earnings / news / SEC 風險提示
- 多 ticker 合約比較
```

## option-strategy 負責主觀決策

```text
- 保守 / 平衡 / 積極策略模板
- Delta / DTE / OTM 偏好
- position sizing
- account size / buying power 使用比例
- sector / ticker exposure limit
- earnings avoidance policy
- assignment / rolling / exit rule
- Wheel strategy 規則
```

原則：

```text
option-analysis 不內建策略風格；它只提供資料與指標。
option-strategy 決定要用哪些條件呼叫 option-analysis。
```

---

# 三、策略模組第一版目標

MVP 先聚焦 Sell Put，不做自動下單。

```text
1. 根據使用者風格產生 option-analysis 查詢參數。
2. 對 option-analysis 回傳結果做策略層排序。
3. 根據帳戶大小與風險限制計算最大可接受 cash required。
4. 給出候選合約清單與原因，但保留人工確認。
```

不是第一版目標：

```text
1. 自動下單。
2. 高頻交易。
3. 全市場 ticker discovery。
4. 取代 option-analysis 的合約資料計算。
```

---

# 四、策略模板草案

## 1. Conservative Sell Put

適合：偏保守、重視接股品質與安全邊際。

```text
DTE: 21～45
Delta: -0.08 ～ -0.20
OTM%: >= 8%
Spread target: <= 10%
OI target: >= 300
Volume target: >= 20
includeEarnings: false
max cash per trade: 5%～10% account size
```

## 2. Balanced Sell Put

適合：一般 Sell Put 候選比較。

```text
DTE: 25～50
Delta: -0.15 ～ -0.30
OTM%: >= 5%
Spread target: <= 15%
OI target: >= 100
Volume target: >= 10
includeEarnings: false
max cash per trade: 10% account size
```

## 3. Aggressive Premium Sell Put

適合：願意承擔較高波動與指派風險，追求較高權利金。

```text
DTE: 14～45
Delta: -0.25 ～ -0.40
OTM%: >= 3%～5%
Spread target: <= 20%
OI target: >= 50
Volume target: >= 5
includeEarnings: normally false; explicit override required
max cash per trade: 5%～8% account size
```

---

# 五、策略輸入 / 輸出設計

## 輸入

```ts
{
  symbols: string[];
  profile: "conservative" | "balanced" | "aggressive";
  accountSize?: number;
  maxCashPctPerTrade?: number;
  maxSectorExposurePct?: number;
  includeEarnings?: boolean;
  overrides?: {
    minDte?: number;
    maxDte?: number;
    minDelta?: number;
    maxDelta?: number;
    minOpenInterest?: number;
    minVolume?: number;
    maxSpreadPct?: number;
  };
}
```

## 輸出

```text
- 產生給 option-analysis 的查詢參數
- 策略模板說明
- position sizing 限制
- 合約候選排序原因
- 風險警告與人工確認清單
```

---

# 六、與 option-market-scanner 的關係

`option-market-scanner` 負責產生候選 ticker。

`option-strategy` 可以讀取 scanner 的候選清單，但不應重做全市場掃描。

```text
scanner result:
  symbols = ["AAPL", "AMD", "NVDA"]

strategy:
  profile = balanced
  accountSize = 100000

analysis:
  option_analysis_sell_put_scan({ symbols, minDte, maxDte, minDelta, maxDelta, ... })
```

---

# 七、風險規則草案

策略層應額外處理：

```text
1. 單筆 cash required 不超過帳戶指定比例。
2. 同一 ticker 不重複過度曝險。
3. 同一 sector 不超過總曝險上限。
4. 財報前持倉必須明確 opt-in。
5. 高波動 / 低價 / turnaround ticker 要降低部位。
6. 若使用 margin，需額外估算壓力情境下 buying power 需求。
```

---

# 八、實作順序建議

```text
Phase 1: strategy profile → option-analysis 參數轉換
Phase 2: position sizing / cash required 過濾
Phase 3: sector / ticker exposure 控管
Phase 4: rolling / assignment / Wheel 規則
Phase 5: 與 option-market-scanner 串接候選 ticker
```
