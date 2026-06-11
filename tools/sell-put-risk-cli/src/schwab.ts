import type { EvaluateInput } from './model.js';
import { finiteNumber } from './model.js';
import { flattenSchwabPutMap } from './input.js';

const SCHWAB_MARKETDATA_BASE_URL = 'https://api.schwabapi.com/marketdata/v1';
const DAY_MS = 86_400_000;

export interface SchwabFetchOptions {
  accessToken: string;
  baseUrl?: string | undefined;
  minDte: number;
  maxDte: number;
  strikeCount?: number | undefined;
  periodYears: number;
}

const ymd = (date: Date): string => date.toISOString().slice(0, 10);

const buildUrl = (baseUrl: string, path: string, params: Record<string, string | number | boolean | undefined>): URL => {
  const url = new URL(`${baseUrl.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') url.searchParams.set(key, String(value));
  }
  return url;
};

const schwabGet = async <T>(
  path: string,
  params: Record<string, string | number | boolean | undefined>,
  options: Pick<SchwabFetchOptions, 'accessToken' | 'baseUrl'>,
): Promise<T> => {
  const url = buildUrl(options.baseUrl ?? SCHWAB_MARKETDATA_BASE_URL, path, params);
  const response = await fetch(url, {
    headers: {
      accept: 'application/json',
      authorization: `Bearer ${options.accessToken}`,
    },
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    const suffix = detail ? `: ${detail.slice(0, 500)}` : '';
    throw new Error(`Schwab ${path} HTTP ${response.status}${suffix}`);
  }
  return await response.json() as T;
};

const firstRecord = (value: unknown): Record<string, unknown> | null => {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  const first = Object.values(record)[0];
  return first && typeof first === 'object' && !Array.isArray(first) ? first as Record<string, unknown> : null;
};

const readNestedNumber = (record: Record<string, unknown> | null, paths: string[][]): number | null => {
  if (!record) return null;
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

const extractQuoteS0 = (quotePayload: unknown): number | null => {
  const quote = firstRecord(quotePayload);
  return readNestedNumber(quote, [
    ['quote', 'mark'],
    ['quote', 'lastPrice'],
    ['quote', 'last'],
    ['quote', 'closePrice'],
    ['regular', 'regularMarketLastPrice'],
    ['regular', 'regularMarketLast'],
  ]);
};

const extractChainS0 = (chainPayload: unknown): number | null => {
  if (!chainPayload || typeof chainPayload !== 'object') return null;
  const chain = chainPayload as Record<string, unknown>;
  return readNestedNumber(chain, [
    ['underlyingPrice'],
    ['underlying', 'mark'],
    ['underlying', 'last'],
    ['underlying', 'lastPrice'],
    ['underlying', 'quote', 'mark'],
    ['underlying', 'quote', 'lastPrice'],
  ]);
};

const extractCandles = (priceHistoryPayload: unknown): EvaluateInput['candles'] => {
  if (!priceHistoryPayload || typeof priceHistoryPayload !== 'object') throw new Error('Schwab pricehistory response is not an object');
  const candles = (priceHistoryPayload as Record<string, unknown>).candles;
  if (!Array.isArray(candles)) throw new Error('Schwab pricehistory response did not include candles');
  return candles.map((raw, index) => {
    if (!raw || typeof raw !== 'object') throw new Error(`pricehistory.candles[${index}] is not an object`);
    const record = raw as Record<string, unknown>;
    const low = finiteNumber(record.low);
    const close = finiteNumber(record.close);
    if (low == null || close == null) throw new Error(`pricehistory.candles[${index}] requires low and close`);
    return {
      datetime: record.datetime as string | number,
      open: finiteNumber(record.open) ?? undefined,
      high: finiteNumber(record.high) ?? undefined,
      low,
      close,
      volume: finiteNumber(record.volume) ?? undefined,
    };
  });
};

export const fetchSchwabEvaluateInput = async (
  symbol: string,
  options: SchwabFetchOptions,
): Promise<EvaluateInput> => {
  const normalizedSymbol = symbol.trim().toUpperCase();
  if (!normalizedSymbol) throw new Error('symbol is required');
  if (!options.accessToken) throw new Error('Schwab access token is required');
  if (options.minDte < 1 || options.maxDte < options.minDte) throw new Error('invalid DTE range');
  if (options.periodYears < 1) throw new Error('periodYears must be >= 1');

  const now = new Date();
  const fromDate = ymd(new Date(now.getTime() + options.minDte * DAY_MS));
  const toDate = ymd(new Date(now.getTime() + options.maxDte * DAY_MS));

  const [quotePayload, chainPayload, priceHistoryPayload] = await Promise.all([
    schwabGet<unknown>('/quotes', { symbols: normalizedSymbol, fields: 'quote,reference,regular' }, options),
    schwabGet<unknown>('/chains', {
      symbol: normalizedSymbol,
      contractType: 'PUT',
      strategy: 'SINGLE',
      includeUnderlyingQuote: true,
      fromDate,
      toDate,
      strikeCount: options.strikeCount,
    }, options),
    schwabGet<unknown>('/pricehistory', {
      symbol: normalizedSymbol,
      periodType: 'year',
      period: options.periodYears,
      frequencyType: 'daily',
      frequency: 1,
      needExtendedHoursData: false,
      needPreviousClose: false,
    }, options),
  ]);

  const s0 = extractQuoteS0(quotePayload) ?? extractChainS0(chainPayload);
  if (s0 == null) throw new Error('Unable to derive underlying price from Schwab quote/chain responses');

  if (!chainPayload || typeof chainPayload !== 'object') throw new Error('Schwab chain response is not an object');
  const optionsList = flattenSchwabPutMap((chainPayload as Record<string, unknown>).putExpDateMap);
  if (optionsList.length === 0) throw new Error('Schwab chain response did not include PUT contracts');

  return {
    symbol: normalizedSymbol,
    s0,
    asOf: ymd(now),
    candles: extractCandles(priceHistoryPayload),
    options: optionsList,
  };
};
