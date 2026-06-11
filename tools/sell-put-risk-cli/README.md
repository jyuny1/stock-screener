# Sell Put Risk CLI

TypeScript CLI for the two-layer model in `https://github.com/jyuny1/stock-screener/wiki/Sell-Put-Historical-Risk-Premium-Two-Layer-Model`.

It answers only:

1. Historical risk baseline: how often the same-DTE percentage move ended ITM, touched the strike, or ended below breakeven.
2. Premium compensation: whether current `bidPrice` exceeds average expiry loss.

Default analysis uses 14–28 DTE contracts and the most recent one-year historical window. It also reports one-year weekly max drawdown and a deterministic support proxy (lowest weekly low) as price context.

It does **not** model earnings, news, liquidity, sizing, or trade recommendations. Weekly support context is informational only and does not change PremiumEdge.

## Build

```bash
cd tools/sell-put-risk-cli
npm install
npm run build
```

## Local JSON evaluation

```bash
npm run build
node dist/cli.js evaluate --input sample.json --format table --otm-only
```

Minimal normalized input:

```json
{
  "symbol": "XYZ",
  "s0": 100,
  "asOf": "2026-06-10",
  "candles": [
    { "datetime": "2025-01-02", "open": 99, "high": 101, "low": 98, "close": 100, "volume": 1000000 }
  ],
  "options": [
    {
      "expirationDate": "2026-06-24",
      "daysToExpiration": 14,
      "strikePrice": 90,
      "bidPrice": 1,
      "askPrice": 1.2,
      "openInterest": 500,
      "totalVolume": 100
    }
  ]
}
```

The loader also accepts Schwab-like JSON containing `putExpDateMap` and `priceHistory.candles`.

## Live Schwab evaluation

```bash
export SCHWAB_ACCESS_TOKEN=...
node dist/cli.js schwab AAPL --strike-count 40
```

Defaults:

- DTE: `14–28`
- Calculation lookback: `365` days
- Schwab pricehistory fetch: `--period-years 1`

The live command calls:

- `/quotes` for current underlying price
- `/chains` for PUT contracts
- `/pricehistory` for daily historical OHLC

Override examples:

```bash
node dist/cli.js schwab AAPL --min-dte 21 --max-dte 35 --lookback-days 365 --period-years 1
```

## Output fields

The table/CSV/JSON output includes the minimum fields from the model document:

- symbol, expiration, DTE, S0, strike, bid, ask
- breakeven, strike distance, breakeven distance
- `BelowK` / `expiryBelowStrikeProb`: expiry below strike probability
- `TouchK` / `touchedStrikeProb`: intraperiod strike touch probability
- `BelowBE` / `expiryBelowBreakevenProb`: expiry below breakeven probability
- `AvgLoss` / `avgExpiryLoss`: average expiry loss before premium
- `PremiumEdge` / `premiumEdge`: `Bid - AvgLoss`
- `Edge/K` / `premiumEdgePctOfStrike`: `PremiumEdge / Strike`
- `AnnYield` / `annualizedYield`: `Bid / Strike * 365 / DTE`
- `AnnEdge` / `annualizedPremiumEdge`: `PremiumEdge / Strike * 365 / DTE`
- sample size
- one-year weekly high/low, weekly max drawdown, support level, strike-to-support distance

`PremiumEdge = bidPrice - AvgLoss`.

Annualized fields are decimal rates in JSON/CSV and percentages in table output.

Support definition in this CLI:

```text
supportLevel = lowest weekly low within the configured lookback window
weeklyMaxDrawdown = max decline from a prior weekly high to a later weekly low
```
