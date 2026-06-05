# Scan Table 指標中英對照

> 目的：作為後續將 Scan table 指標名稱從英文改成中文的 UI label 對照基準。
>
> 注意：部分英文 label 是短版欄位名，中文欄位以「清楚理解」優先，不一定逐字直譯。

| Field ID | English Label | 中文建議 | 說明 |
|---|---|---|---|
| `chart` | Chart | 圖表 | 開啟個股圖表 |
| `symbol` | Sym | 代號 | 股票/ETF 代號 |
| `rs_trend` | RS Trend | 相對強度趨勢 | 近期相對 SPY 的 RS line 趨勢 |
| `price_change_1d` | Price | 日漲跌 | 單日價格變化 |
| `gics_sector` | Sector | 板塊 | GICS / provider sector |
| `ibd_industry_group` | IBD Industry | IBD 產業 | IBD/產業組名稱或 surrogate group |
| `market_themes` | Themes | 主題 | 投資主題/市場敘事 | X
| `ibd_group_rank` | Grp | 產業排名 | 產業組排名，數字越小越強 |
| `composite_score` | Comp | 綜合分 | 多指標綜合分數 | X
| `minervini_score` | Min | Minervini 分 | Minervini template / 趨勢條件分數 | X
| `canslim_score` | CAN | CANSLIM 分 | CANSLIM 風格綜合分 | X
| `ipo_score` | IPO | IPO 分 | IPO / 新股相關分數 | X
| `custom_score` | Cust | 自訂分 | 自訂策略分數 | X
| `volume_breakthrough_score` | VolB | 放量突破 |成交量突破分數 |
| `se_setup_score` | SE | SE 型態分 | Setup Engine 型態分數  | X
| `se_pattern_primary` | Pat | 型態 | 主要型態名稱 | X
| `se_distance_to_pivot_pct` | Pvt% | 距樞紐% | 現價距離 pivot 的百分比 |
| `se_bb_width_pctile_252` | Sqz | 壓縮度 | 252 日布林帶寬度百分位，越低越壓縮 | X
| `se_volume_vs_50d` | V50 | 量比50日 | 當日量 / 50 日均量 |
| `se_rs_line_new_high` | RSH | RS 新高 | RS line 是否創近期新高 |
| `se_pivot_price` | Pvt$ | 樞紐價 | Setup Engine pivot price | X
| `rs_rating` | RS | 相對強度 | 12 個月相對強度排名 |
| `rs_rating_1m` | 1M | 1月強度 | 1 個月相對強度排名 |
| `rs_rating_3m` | 3M | 3月強度 | 3 個月相對強度排名 |
| `rs_rating_12m` | 12M | 12月強度 | 12 個月相對強度排名 |
| `beta` | β | Beta | 相對市場波動度 |
| `beta_adj_rs` | βRS | Beta調整RS | 以 Beta 調整後的相對強度 | X
| `eps_rating` | EPS Rtg | EPS評級 | EPS growth 派生評級 | X
| `stage` | Stg | 階段 | 技術趨勢階段 | X
| `current_price` | Price | 現價 | 最新收盤/價格 |
| `volume` | Vol | 成交量 | 最新交易日成交股數 |
| `market_cap` | MCap | 市值/AUM | 股票為市值；ETF fallback 為 AUM/淨資產 | 
| `adv_usd` | ADV ($) | 日均成交額 | 美元日成交額/近似 ADV |
| `ipo_date` | IPO | 上市日期 | IPO 或 first trade date |X
| `eps_growth_qq` | EPS | EPS成長 | 季對季/近期 EPS growth |
| `sales_growth_qq` | Sales | 營收成長 | 季對季/近期 revenue growth | X
| `adr_percent` | ADR | 平均日振幅 | Average Daily Range percent |
| `ma_alignment` | MA | 均線排列 | 價格與均線是否多頭排列 |
| `vcp_detected` | VCP | VCP型態 | 是否偵測到 VCP | X
| `vcp_score` | VScr | VCP分 | VCP score | X
| `vcp_pivot` | Pvt | VCP樞紐 | VCP pivot price | X
| `vcp_ready_for_breakout` | Rdy | 突破準備 | 是否接近可突破狀態 | X
| `passes_template` | Pass | 通過模板 | 是否通過 Minervini/template 條件 | X
| `rating` | Rate | 評級 | 綜合 rating label | X

## 建議短版中文 Label

若表格欄寬不足，可優先使用以下短版：

| English Label | 中文短版 |
|---|---|
| Sym | 代號 |
| RS Trend | RS趨勢 |
| Price | 漲跌 |
| Sector | 板塊 |
| IBD Industry | 產業 |
| Themes | 主題 |
| Grp | 產排 |
| Comp | 綜合 |
| Min | Min |
| CAN | CAN |
| IPO | IPO |
| Cust | 自訂 |
| VolB | 放量 |
| SE | SE |
| Pat | 型態 |
| Pvt% | 樞% |
| Sqz | 壓縮 |
| V50 | 量50 |
| RSH | RS高 |
| Pvt$ | 樞價 |
| RS | RS |
| 1M | 1月 |
| 3M | 3月 |
| 12M | 12月 |
| β | β |
| βRS | βRS |
| EPS Rtg | EPS評 |
| Stg | 階段 |
| Vol | 成交量 |
| MCap | 市值/AUM |
| ADV ($) | 均額 |
| EPS | EPS增 |
| Sales | 營收增 |
| ADR | 振幅 |
| MA | 均線 |
| VCP | VCP |
| VScr | VCP分 |
| Rdy | 準備 |
| Pass | 通過 |
| Rate | 評級 |
