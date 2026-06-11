import { describe, expect, it } from 'vitest';
import { buildRollingWindows, evaluateInput, normalizeCandles } from '../src/model.js';

const candles = [
  { datetime: '2026-01-01T00:00:00Z', low: 99, close: 100 },
  { datetime: '2026-01-02T00:00:00Z', low: 94, close: 95 },
  { datetime: '2026-01-03T00:00:00Z', low: 88, close: 89 },
  { datetime: '2026-01-04T00:00:00Z', low: 104, close: 105 },
  { datetime: '2026-01-05T00:00:00Z', low: 79, close: 80 },
];

describe('historical two-layer model', () => {
  it('calculates BelowK, TouchK, BelowBE, AvgLoss, and PremiumEdge from rolling windows', () => {
    const result = evaluateInput({
      symbol: 'TEST',
      s0: 100,
      candles,
      options: [{ expirationDate: '2026-01-20', daysToExpiration: 2, strikePrice: 90, bidPrice: 1, askPrice: 1.2 }],
    }, { windowMode: 'calendar' });

    expect(result.rows).toHaveLength(1);
    const row = result.rows[0];
    expect(row?.sampleSize).toBe(3);
    expect(row?.expiryBelowStrikeProb).toBeCloseTo(2 / 3, 8);
    expect(row?.touchedStrikeProb).toBeCloseTo(2 / 3, 8);
    expect(row?.expiryBelowBreakevenProb).toBe(0);
    const expectedAvgLoss = (1 + (90 - (100 * 80 / 89))) / 3;
    const expectedPremiumEdge = 1 - expectedAvgLoss;
    expect(row?.avgExpiryLoss).toBeCloseTo(expectedAvgLoss, 8);
    expect(row?.premiumEdge).toBeCloseTo(expectedPremiumEdge, 8);
    expect(row?.annualizedYield).toBeCloseTo((1 / 90) * (365 / 2), 8);
    expect(row?.annualizedPremiumEdge).toBeCloseTo((expectedPremiumEdge / 90) * (365 / 2), 8);
    expect(row?.verdict).toBe('better_compensation');
  });

  it('excludes the start-day low because the start price is the start-day close', () => {
    const oneDay = normalizeCandles([
      { datetime: '2026-01-01T00:00:00Z', low: 50, close: 100 },
      { datetime: '2026-01-02T00:00:00Z', low: 99, close: 101 },
    ]);

    const windows = buildRollingWindows(oneDay, 1, 'calendar');
    expect(windows).toHaveLength(1);
    expect(windows[0]?.lowRatio).toBeCloseTo(0.99, 8);
  });

  it('returns insufficient_history when candles cannot form the requested DTE window', () => {
    const result = evaluateInput({
      symbol: 'TEST',
      s0: 100,
      candles: candles.slice(0, 2),
      options: [{ expirationDate: '2026-02-20', daysToExpiration: 45, strikePrice: 90, bidPrice: 1 }],
    }, { windowMode: 'calendar' });

    expect(result.rows[0]?.sampleSize).toBe(0);
    expect(result.rows[0]?.avgExpiryLoss).toBeNull();
    expect(result.rows[0]?.premiumEdge).toBeNull();
    expect(result.rows[0]?.annualizedYield).toBeCloseTo((1 / 90) * (365 / 45), 8);
    expect(result.rows[0]?.annualizedPremiumEdge).toBeNull();
    expect(result.rows[0]?.verdict).toBe('insufficient_history');
  });

  it('limits calculations to the configured lookback and adds one-year weekly support context', () => {
    const result = evaluateInput({
      symbol: 'TEST',
      s0: 100,
      candles: [
        { datetime: '2024-01-01T00:00:00Z', high: 200, low: 1, close: 100 },
        { datetime: '2025-12-29T00:00:00Z', high: 100, low: 90, close: 100 },
        { datetime: '2026-01-05T00:00:00Z', high: 110, low: 95, close: 105 },
        { datetime: '2026-01-12T00:00:00Z', high: 108, low: 88, close: 90 },
      ],
      options: [{ expirationDate: '2026-01-20', daysToExpiration: 1, strikePrice: 95, bidPrice: 2 }],
    }, { windowMode: 'trading-days', lookbackDays: 365 });

    expect(result.priceContext?.weeklySampleSize).toBe(3);
    expect(result.priceContext?.oneYearWeeklyHigh).toBe(110);
    expect(result.priceContext?.supportLevel).toBe(88);
    expect(result.priceContext?.weeklyMaxDrawdown).toBeCloseTo(88 / 110 - 1, 8);
    expect(result.rows[0]?.sampleSize).toBe(2);
    expect(result.rows[0]?.supportLevel).toBe(88);
    expect(result.rows[0]?.strikeDistanceToSupport).toBeCloseTo(95 / 88 - 1, 8);
  });
});
