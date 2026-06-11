export type WindowMode = 'calendar' | 'trading-days';

const DAY_MS = 86_400_000;
export const DEFAULT_LOOKBACK_DAYS = 365;

export interface CandleInput {
  datetime?: string | number | Date | undefined;
  date?: string | undefined;
  open?: number | undefined;
  high?: number | undefined;
  low: number;
  close: number;
  volume?: number | undefined;
}

export interface NormalizedCandle {
  timestampMs: number;
  open: number | null;
  high: number | null;
  low: number;
  close: number;
  volume: number | null;
}

export interface PutContractInput {
  symbol?: string | undefined;
  expirationDate?: string | undefined;
  daysToExpiration?: number | undefined;
  strikePrice?: number | undefined;
  bidPrice?: number | undefined;
  askPrice?: number | undefined;
  markPrice?: number | undefined;
  lastPrice?: number | undefined;
  totalVolume?: number | undefined;
  volume?: number | undefined;
  openInterest?: number | undefined;
  delta?: number | undefined;
  volatility?: number | undefined;
  theoreticalOptionValue?: number | undefined;
  isInTheMoney?: boolean | undefined;
}

export interface NormalizedPutContract {
  contractSymbol: string | null;
  expirationDate: string;
  daysToExpiration: number;
  strikePrice: number;
  bidPrice: number;
  askPrice: number | null;
  markPrice: number | null;
  lastPrice: number | null;
  totalVolume: number | null;
  openInterest: number | null;
  delta: number | null;
  volatility: number | null;
  theoreticalOptionValue: number | null;
  isInTheMoney: boolean | null;
}

export interface EvaluateInput {
  symbol: string;
  s0: number;
  asOf?: string | undefined;
  candles: CandleInput[];
  options: PutContractInput[];
}

export interface EvaluateConfig {
  windowMode: WindowMode;
  lookbackDays?: number | undefined;
  minDte?: number | undefined;
  maxDte?: number | undefined;
  otmOnly?: boolean | undefined;
  minBid?: number | undefined;
}

export interface RollingWindow {
  startTimestampMs: number;
  endTimestampMs: number;
  startClose: number;
  endClose: number;
  windowLow: number;
  endRatio: number;
  lowRatio: number;
  calendarDays: number;
  tradingSessions: number;
}

export interface WeeklyCandle {
  weekStartTimestampMs: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface WeeklyPriceContext {
  lookbackDays: number;
  weeklySampleSize: number;
  oneYearWeeklyHigh: number | null;
  oneYearWeeklyLow: number | null;
  weeklyMaxDrawdown: number | null;
  weeklyMaxDrawdownPeak: number | null;
  weeklyMaxDrawdownTrough: number | null;
  supportLevel: number | null;
  supportDistance: number | null;
}

export type Verdict = 'better_compensation' | 'insufficient_compensation' | 'no_bid_credit' | 'insufficient_history';

export interface OptionRiskRow {
  symbol: string;
  contractSymbol: string | null;
  expiration: string;
  dte: number;
  s0: number;
  strike: number;
  bid: number;
  ask: number | null;
  mark: number | null;
  last: number | null;
  theo: number | null;
  delta: number | null;
  volatility: number | null;
  openInterest: number | null;
  volume: number | null;
  breakeven: number;
  strikeDistance: number;
  breakevenDistance: number;
  expiryBelowStrikeProb: number | null;
  touchedStrikeProb: number | null;
  expiryBelowBreakevenProb: number | null;
  avgExpiryLoss: number | null;
  premiumEdge: number | null;
  premiumEdgePctOfStrike: number | null;
  annualizedYield: number | null;
  annualizedPremiumEdge: number | null;
  oneYearWeeklyHigh: number | null;
  oneYearWeeklyLow: number | null;
  weeklyMaxDrawdown: number | null;
  supportLevel: number | null;
  supportDistance: number | null;
  strikeDistanceToSupport: number | null;
  sampleSize: number;
  windowMode: WindowMode;
  verdict: Verdict;
}

export interface EvaluationResult {
  model: 'sell-put-historical-risk-premium-two-layer-v1';
  scope: string;
  symbol: string;
  s0: number;
  asOf: string | null;
  generatedAt: string;
  priceContext: WeeklyPriceContext | null;
  rows: OptionRiskRow[];
}

export const MODEL_SCOPE = 'PremiumEdge uses one-year historical expiry loss and bid-premium compensation. One-year weekly max drawdown/support is informational price context; excludes earnings, news, liquidity, sizing, and trade recommendation.';

export const finiteNumber = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
};

const requireFinite = (value: unknown, label: string): number => {
  const parsed = finiteNumber(value);
  if (parsed == null) throw new Error(`${label} must be a finite number`);
  return parsed;
};

export const toTimestampMs = (value: unknown): number => {
  if (value instanceof Date) {
    const time = value.getTime();
    if (Number.isFinite(time)) return time;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    // Schwab candles use milliseconds. Accept seconds as a convenience.
    return value < 10_000_000_000 ? value * 1000 : value;
  }
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  throw new Error(`Invalid candle datetime: ${String(value)}`);
};

export const normalizeCandles = (candles: CandleInput[]): NormalizedCandle[] => {
  const normalized = candles.map((candle, index) => {
    const timestampMs = toTimestampMs(candle.datetime ?? candle.date);
    const close = requireFinite(candle.close, `candles[${index}].close`);
    const low = requireFinite(candle.low, `candles[${index}].low`);
    if (close <= 0) throw new Error(`candles[${index}].close must be > 0`);
    if (low <= 0) throw new Error(`candles[${index}].low must be > 0`);
    return {
      timestampMs,
      open: finiteNumber(candle.open),
      high: finiteNumber(candle.high),
      low,
      close,
      volume: finiteNumber(candle.volume),
    };
  });

  normalized.sort((left, right) => left.timestampMs - right.timestampMs);
  return normalized;
};

export const normalizePutContract = (contract: PutContractInput, index: number): NormalizedPutContract => {
  const strikePrice = requireFinite(contract.strikePrice, `options[${index}].strikePrice`);
  const bidPrice = requireFinite(contract.bidPrice, `options[${index}].bidPrice`);
  const daysToExpiration = requireFinite(contract.daysToExpiration, `options[${index}].daysToExpiration`);
  if (!Number.isInteger(daysToExpiration) || daysToExpiration < 1) {
    throw new Error(`options[${index}].daysToExpiration must be a positive integer`);
  }
  if (strikePrice <= 0) throw new Error(`options[${index}].strikePrice must be > 0`);
  if (!contract.expirationDate) throw new Error(`options[${index}].expirationDate is required`);

  return {
    contractSymbol: contract.symbol ?? null,
    expirationDate: contract.expirationDate,
    daysToExpiration,
    strikePrice,
    bidPrice,
    askPrice: finiteNumber(contract.askPrice),
    markPrice: finiteNumber(contract.markPrice),
    lastPrice: finiteNumber(contract.lastPrice),
    totalVolume: finiteNumber(contract.totalVolume ?? contract.volume),
    openInterest: finiteNumber(contract.openInterest),
    delta: finiteNumber(contract.delta),
    volatility: finiteNumber(contract.volatility),
    theoreticalOptionValue: finiteNumber(contract.theoreticalOptionValue),
    isInTheMoney: typeof contract.isInTheMoney === 'boolean' ? contract.isInTheMoney : null,
  };
};

const lowerBoundTimestamp = (candles: NormalizedCandle[], targetMs: number, startIndex: number): number => {
  let low = startIndex;
  let high = candles.length;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    const candle = candles[mid];
    if (!candle) break;
    if (candle.timestampMs < targetMs) low = mid + 1;
    else high = mid;
  }
  return low;
};

const minLow = (candles: NormalizedCandle[], fromIndex: number, toIndexInclusive: number): number => {
  let low = Number.POSITIVE_INFINITY;
  for (let index = fromIndex; index <= toIndexInclusive; index += 1) {
    const candle = candles[index];
    if (!candle) continue;
    low = Math.min(low, candle.low);
  }
  if (!Number.isFinite(low)) throw new Error('Unable to compute window low');
  return low;
};

export const buildRollingWindows = (
  candles: NormalizedCandle[],
  dte: number,
  windowMode: WindowMode = 'calendar',
): RollingWindow[] => {
  if (!Number.isInteger(dte) || dte < 1) throw new Error('dte must be a positive integer');
  if (candles.length < 2) return [];

  const windows: RollingWindow[] = [];
  for (let startIndex = 0; startIndex < candles.length - 1; startIndex += 1) {
    const start = candles[startIndex];
    if (!start) continue;

    const endIndex = windowMode === 'trading-days'
      ? startIndex + dte
      : lowerBoundTimestamp(candles, start.timestampMs + dte * DAY_MS, startIndex + 1);

    const end = candles[endIndex];
    if (!end) break;
    if (endIndex <= startIndex) continue;

    // The model's start price is the start-day close, so the start day's intraday low is excluded.
    const windowLow = minLow(candles, startIndex + 1, endIndex);
    const calendarDays = Math.round((end.timestampMs - start.timestampMs) / DAY_MS);
    windows.push({
      startTimestampMs: start.timestampMs,
      endTimestampMs: end.timestampMs,
      startClose: start.close,
      endClose: end.close,
      windowLow,
      endRatio: end.close / start.close,
      lowRatio: windowLow / start.close,
      calendarDays,
      tradingSessions: endIndex - startIndex,
    });
  }
  return windows;
};

export const filterCandlesByLookback = (candles: NormalizedCandle[], lookbackDays: number): NormalizedCandle[] => {
  if (!Number.isFinite(lookbackDays) || lookbackDays < 1) throw new Error('lookbackDays must be >= 1');
  if (candles.length === 0) return [];
  const latest = candles[candles.length - 1];
  if (!latest) return [];
  const cutoff = latest.timestampMs - lookbackDays * DAY_MS;
  return candles.filter((candle) => candle.timestampMs >= cutoff);
};

const utcWeekStart = (timestampMs: number): number => {
  const date = new Date(timestampMs);
  const midnight = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  const day = new Date(midnight).getUTCDay();
  const daysSinceMonday = (day + 6) % 7;
  return midnight - daysSinceMonday * DAY_MS;
};

export const buildWeeklyCandles = (candles: NormalizedCandle[]): WeeklyCandle[] => {
  const weekly: WeeklyCandle[] = [];
  for (const candle of candles) {
    const weekStartTimestampMs = utcWeekStart(candle.timestampMs);
    const high = candle.high ?? Math.max(candle.open ?? candle.close, candle.close, candle.low);
    const open = candle.open ?? candle.close;
    const current = weekly[weekly.length - 1];
    if (!current || current.weekStartTimestampMs !== weekStartTimestampMs) {
      weekly.push({
        weekStartTimestampMs,
        open,
        high,
        low: candle.low,
        close: candle.close,
      });
    } else {
      current.high = Math.max(current.high, high);
      current.low = Math.min(current.low, candle.low);
      current.close = candle.close;
    }
  }
  return weekly;
};

export const calculateWeeklyPriceContext = (
  candles: NormalizedCandle[],
  s0: number,
  lookbackDays: number,
): WeeklyPriceContext | null => {
  const weekly = buildWeeklyCandles(candles);
  if (weekly.length === 0) return null;

  let oneYearWeeklyHigh = Number.NEGATIVE_INFINITY;
  let oneYearWeeklyLow = Number.POSITIVE_INFINITY;
  let peak = weekly[0]?.high ?? Number.NaN;
  let maxDrawdown = 0;
  let maxDrawdownPeak = peak;
  let maxDrawdownTrough = weekly[0]?.low ?? Number.NaN;

  for (const week of weekly) {
    oneYearWeeklyHigh = Math.max(oneYearWeeklyHigh, week.high);
    oneYearWeeklyLow = Math.min(oneYearWeeklyLow, week.low);

    if (Number.isFinite(peak) && peak > 0) {
      const drawdown = week.low / peak - 1;
      if (drawdown < maxDrawdown) {
        maxDrawdown = drawdown;
        maxDrawdownPeak = peak;
        maxDrawdownTrough = week.low;
      }
    }
    if (!Number.isFinite(peak) || week.high > peak) peak = week.high;
  }

  const supportLevel = Number.isFinite(oneYearWeeklyLow) ? oneYearWeeklyLow : null;
  return {
    lookbackDays,
    weeklySampleSize: weekly.length,
    oneYearWeeklyHigh: Number.isFinite(oneYearWeeklyHigh) ? oneYearWeeklyHigh : null,
    oneYearWeeklyLow: Number.isFinite(oneYearWeeklyLow) ? oneYearWeeklyLow : null,
    weeklyMaxDrawdown: maxDrawdown,
    weeklyMaxDrawdownPeak: Number.isFinite(maxDrawdownPeak) ? maxDrawdownPeak : null,
    weeklyMaxDrawdownTrough: Number.isFinite(maxDrawdownTrough) ? maxDrawdownTrough : null,
    supportLevel,
    supportDistance: supportLevel != null ? supportLevel / s0 - 1 : null,
  };
};

export const evaluateContract = (
  symbol: string,
  s0: number,
  contract: NormalizedPutContract,
  windows: RollingWindow[],
  windowMode: WindowMode,
  priceContext: WeeklyPriceContext | null,
): OptionRiskRow => {
  if (s0 <= 0) throw new Error('s0 must be > 0');
  const strike = contract.strikePrice;
  const bid = contract.bidPrice;
  const breakeven = strike - bid;
  const strikeRatio = strike / s0;
  const breakevenRatio = breakeven / s0;
  const annualizationFactor = 365 / contract.daysToExpiration;
  const annualizedYield = (bid / strike) * annualizationFactor;
  const sampleSize = windows.length;

  const base = {
    symbol,
    contractSymbol: contract.contractSymbol,
    expiration: contract.expirationDate,
    dte: contract.daysToExpiration,
    s0,
    strike,
    bid,
    ask: contract.askPrice,
    mark: contract.markPrice,
    last: contract.lastPrice,
    theo: contract.theoreticalOptionValue,
    delta: contract.delta,
    volatility: contract.volatility,
    openInterest: contract.openInterest,
    volume: contract.totalVolume,
    breakeven,
    strikeDistance: strikeRatio - 1,
    breakevenDistance: breakevenRatio - 1,
    oneYearWeeklyHigh: priceContext?.oneYearWeeklyHigh ?? null,
    oneYearWeeklyLow: priceContext?.oneYearWeeklyLow ?? null,
    weeklyMaxDrawdown: priceContext?.weeklyMaxDrawdown ?? null,
    supportLevel: priceContext?.supportLevel ?? null,
    supportDistance: priceContext?.supportDistance ?? null,
    strikeDistanceToSupport: priceContext?.supportLevel ? strike / priceContext.supportLevel - 1 : null,
    sampleSize,
    windowMode,
  };

  if (sampleSize === 0) {
    return {
      ...base,
      expiryBelowStrikeProb: null,
      touchedStrikeProb: null,
      expiryBelowBreakevenProb: null,
      avgExpiryLoss: null,
      premiumEdge: null,
      premiumEdgePctOfStrike: null,
      annualizedYield,
      annualizedPremiumEdge: null,
      verdict: 'insufficient_history',
    };
  }

  let itmCount = 0;
  let touchCount = 0;
  let lossCount = 0;
  let expiryLossSum = 0;

  for (const window of windows) {
    if (window.endRatio < strikeRatio) itmCount += 1;
    if (window.lowRatio < strikeRatio) touchCount += 1;
    if (window.endRatio < breakevenRatio) lossCount += 1;
    const simulatedTerminalPrice = s0 * window.endRatio;
    expiryLossSum += Math.max(strike - simulatedTerminalPrice, 0);
  }

  const avgExpiryLoss = expiryLossSum / sampleSize;
  const premiumEdge = bid - avgExpiryLoss;
  const verdict: Verdict = bid <= 0
    ? 'no_bid_credit'
    : premiumEdge > 0
      ? 'better_compensation'
      : 'insufficient_compensation';

  return {
    ...base,
    expiryBelowStrikeProb: itmCount / sampleSize,
    touchedStrikeProb: touchCount / sampleSize,
    expiryBelowBreakevenProb: lossCount / sampleSize,
    avgExpiryLoss,
    premiumEdge,
    premiumEdgePctOfStrike: premiumEdge / strike,
    annualizedYield,
    annualizedPremiumEdge: (premiumEdge / strike) * annualizationFactor,
    verdict,
  };
};

export const evaluateInput = (input: EvaluateInput, config: EvaluateConfig): EvaluationResult => {
  const symbol = input.symbol.trim().toUpperCase();
  if (!symbol) throw new Error('symbol is required');
  const s0 = requireFinite(input.s0, 's0');
  if (s0 <= 0) throw new Error('s0 must be > 0');

  const lookbackDays = config.lookbackDays ?? DEFAULT_LOOKBACK_DAYS;
  const candles = filterCandlesByLookback(normalizeCandles(input.candles), lookbackDays);
  const priceContext = calculateWeeklyPriceContext(candles, s0, lookbackDays);
  const contracts = input.options
    .map((contract, index) => normalizePutContract(contract, index))
    .filter((contract) => {
      if (config.minDte != null && contract.daysToExpiration < config.minDte) return false;
      if (config.maxDte != null && contract.daysToExpiration > config.maxDte) return false;
      if (config.minBid != null && contract.bidPrice < config.minBid) return false;
      if (config.otmOnly && contract.strikePrice >= s0) return false;
      return true;
    });

  const windowsByDte = new Map<number, RollingWindow[]>();
  const rows = contracts.map((contract) => {
    let windows = windowsByDte.get(contract.daysToExpiration);
    if (!windows) {
      windows = buildRollingWindows(candles, contract.daysToExpiration, config.windowMode);
      windowsByDte.set(contract.daysToExpiration, windows);
    }
    return evaluateContract(symbol, s0, contract, windows, config.windowMode, priceContext);
  });

  return {
    model: 'sell-put-historical-risk-premium-two-layer-v1',
    scope: MODEL_SCOPE,
    symbol,
    s0,
    asOf: input.asOf ?? null,
    generatedAt: new Date().toISOString(),
    priceContext,
    rows,
  };
};
