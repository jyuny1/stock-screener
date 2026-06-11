import { readFile } from 'node:fs/promises';
import type { CandleInput, EvaluateInput, PutContractInput } from './model.js';
import { finiteNumber } from './model.js';

export interface InputOverrides {
  symbol?: string | undefined;
  s0?: number | undefined;
  asOf?: string | undefined;
}

const asRecord = (value: unknown, label: string): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
};

const asArray = (value: unknown, label: string): unknown[] => {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
};

const stringValue = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value : null);

const numberValue = (record: Record<string, unknown>, keys: string[]): number | null => {
  for (const key of keys) {
    const parsed = finiteNumber(record[key]);
    if (parsed != null) return parsed;
  }
  return null;
};

const readNestedNumber = (record: Record<string, unknown>, paths: string[][]): number | null => {
  for (const path of paths) {
    let current: unknown = record;
    for (const segment of path) {
      if (!current || typeof current !== 'object') {
        current = undefined;
        break;
      }
      current = (current as Record<string, unknown>)[segment];
    }
    const parsed = finiteNumber(current);
    if (parsed != null) return parsed;
  }
  return null;
};

export const flattenSchwabPutMap = (putExpDateMap: unknown): PutContractInput[] => {
  if (!putExpDateMap || typeof putExpDateMap !== 'object') return [];
  const contracts: PutContractInput[] = [];
  for (const [expiryKey, strikesValue] of Object.entries(putExpDateMap as Record<string, unknown>)) {
    if (!strikesValue || typeof strikesValue !== 'object') continue;
    const [expirationDate, dteText] = expiryKey.split(':');
    const keyDte = finiteNumber(dteText);
    for (const contractsValue of Object.values(strikesValue as Record<string, unknown>)) {
      if (!Array.isArray(contractsValue)) continue;
      for (const raw of contractsValue) {
        if (!raw || typeof raw !== 'object') continue;
        const record = raw as Record<string, unknown>;
        const option: PutContractInput = {};
        const symbol = stringValue(record.symbol);
        if (symbol) option.symbol = symbol;
        option.expirationDate = stringValue(record.expirationDate) ?? expirationDate ?? '';
        option.daysToExpiration = numberValue(record, ['daysToExpiration']) ?? keyDte ?? undefined;
        option.strikePrice = numberValue(record, ['strikePrice']) ?? undefined;
        option.bidPrice = numberValue(record, ['bidPrice', 'bid']) ?? undefined;
        option.askPrice = numberValue(record, ['askPrice', 'ask']) ?? undefined;
        option.markPrice = numberValue(record, ['markPrice', 'mark']) ?? undefined;
        option.lastPrice = numberValue(record, ['lastPrice', 'last']) ?? undefined;
        option.totalVolume = numberValue(record, ['totalVolume', 'volume']) ?? undefined;
        option.openInterest = numberValue(record, ['openInterest']) ?? undefined;
        option.delta = numberValue(record, ['delta']) ?? undefined;
        option.volatility = numberValue(record, ['volatility']) ?? undefined;
        option.theoreticalOptionValue = numberValue(record, ['theoreticalOptionValue']) ?? undefined;
        if (typeof record.isInTheMoney === 'boolean') option.isInTheMoney = record.isInTheMoney;
        contracts.push(option);
      }
    }
  }
  return contracts;
};

const normalizeCandleArray = (value: unknown): CandleInput[] => {
  const rawCandles = asArray(value, 'candles');
  return rawCandles.map((raw, index) => {
    const record = asRecord(raw, `candles[${index}]`);
    const datetime = record.datetime ?? record.date;
    const low = numberValue(record, ['low']);
    const close = numberValue(record, ['close']);
    if (low == null || close == null) throw new Error(`candles[${index}] requires low and close`);
    const candle: CandleInput = { low, close };
    if (datetime != null) candle.datetime = datetime as string | number;
    const open = numberValue(record, ['open']);
    const high = numberValue(record, ['high']);
    const volume = numberValue(record, ['volume']);
    if (open != null) candle.open = open;
    if (high != null) candle.high = high;
    if (volume != null) candle.volume = volume;
    return candle;
  });
};

const normalizeOptionArray = (value: unknown): PutContractInput[] => {
  const rawOptions = asArray(value, 'options');
  return rawOptions.map((raw, index) => {
    const record = asRecord(raw, `options[${index}]`);
    const option: PutContractInput = {};
    const symbol = stringValue(record.symbol ?? record.contractSymbol);
    const expirationDate = stringValue(record.expirationDate ?? record.expiration);
    if (symbol) option.symbol = symbol;
    if (expirationDate) option.expirationDate = expirationDate;
    option.daysToExpiration = numberValue(record, ['daysToExpiration', 'dte']) ?? undefined;
    option.strikePrice = numberValue(record, ['strikePrice', 'strike']) ?? undefined;
    option.bidPrice = numberValue(record, ['bidPrice', 'bid']) ?? undefined;
    option.askPrice = numberValue(record, ['askPrice', 'ask']) ?? undefined;
    option.markPrice = numberValue(record, ['markPrice', 'mark']) ?? undefined;
    option.lastPrice = numberValue(record, ['lastPrice', 'last']) ?? undefined;
    option.totalVolume = numberValue(record, ['totalVolume', 'volume']) ?? undefined;
    option.openInterest = numberValue(record, ['openInterest']) ?? undefined;
    option.delta = numberValue(record, ['delta']) ?? undefined;
    option.volatility = numberValue(record, ['volatility']) ?? undefined;
    option.theoreticalOptionValue = numberValue(record, ['theoreticalOptionValue', 'theo']) ?? undefined;
    if (typeof record.isInTheMoney === 'boolean') option.isInTheMoney = record.isInTheMoney;
    return option;
  });
};

export const normalizeInputPayload = (payload: unknown, overrides: InputOverrides = {}): EvaluateInput => {
  const record = asRecord(payload, 'input');
  const chain = record.chain && typeof record.chain === 'object' ? record.chain as Record<string, unknown> : record;
  const priceHistory = record.priceHistory && typeof record.priceHistory === 'object'
    ? record.priceHistory as Record<string, unknown>
    : record;

  const symbol = overrides.symbol
    ?? stringValue(record.symbol)
    ?? stringValue(chain.symbol)
    ?? stringValue((chain.underlying as Record<string, unknown> | undefined)?.symbol)
    ?? '';

  const s0 = overrides.s0
    ?? finiteNumber(record.s0)
    ?? finiteNumber(record.underlyingPrice)
    ?? readNestedNumber(chain, [
      ['underlying', 'mark'],
      ['underlying', 'last'],
      ['underlying', 'lastPrice'],
      ['underlying', 'quote', 'mark'],
      ['underlying', 'quote', 'lastPrice'],
    ]);

  const candlesValue = record.candles ?? priceHistory.candles;
  const optionsValue = record.options ?? chain.options;
  const options = optionsValue != null
    ? normalizeOptionArray(optionsValue)
    : flattenSchwabPutMap(chain.putExpDateMap);

  if (!symbol) throw new Error('input symbol is required or pass --symbol');
  if (s0 == null) throw new Error('input s0 is required or pass --s0');
  if (candlesValue == null) throw new Error('input candles are required');
  if (options.length === 0) throw new Error('input options or Schwab putExpDateMap are required');

  const input: EvaluateInput = {
    symbol,
    s0,
    candles: normalizeCandleArray(candlesValue),
    options,
  };
  const asOf = overrides.asOf ?? stringValue(record.asOf);
  if (asOf) input.asOf = asOf;
  return input;
};

export const loadEvaluateInput = async (
  path: string,
  overrides: InputOverrides = {},
): Promise<EvaluateInput> => {
  const content = await readFile(path, 'utf8');
  return normalizeInputPayload(JSON.parse(content), overrides);
};
