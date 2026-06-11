#!/usr/bin/env node
import { realpathSync } from 'node:fs';
import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { formatModelScopeNote, formatResult, type OutputFormat } from './format.js';
import { loadEvaluateInput } from './input.js';
import { DEFAULT_LOOKBACK_DAYS, evaluateInput, type EvaluateConfig, type EvaluationResult, type OptionRiskRow, type WindowMode } from './model.js';
import { fetchSchwabEvaluateInput } from './schwab.js';

type FlagValue = string | boolean;
type SortField = 'premium_edge' | 'premium_edge_pct' | 'annualized_yield' | 'annualized_premium_edge' | 'below_be' | 'below_k' | 'touch_k' | 'dte' | 'strike' | 'expiration';
type Order = 'asc' | 'desc';

const DEFAULT_MIN_DTE = 14;
const DEFAULT_MAX_DTE = 28;
const DEFAULT_PERIOD_YEARS = 1;

interface ParsedArgs {
  command: string;
  positionals: string[];
  flags: Map<string, FlagValue>;
}

const HELP = `sell-put-risk - Sell Put historical risk + bid-premium compensation CLI

Usage:
  sell-put-risk evaluate --input snapshot.json [options]
  sell-put-risk schwab SYMBOL [options]

Commands:
  evaluate              Evaluate a local JSON snapshot. Accepts normalized input or Schwab-like chain/pricehistory payload.
  schwab                Fetch /quotes, /chains, and /pricehistory from Schwab, then evaluate PUT contracts.

Evaluation options:
  --format table|json|csv          Output format (default: table)
  --window-mode calendar|trading-days
                                  Rolling window mode (default: calendar)
  --min-dte N                     Minimum DTE filter (default: 14)
  --max-dte N                     Maximum DTE filter (default: 28)
  --lookback-days N               Historical calculation lookback in days (default: 365)
  --min-bid N                     Minimum bid credit filter
  --otm-only                      Keep only strikes below S0
  --sort FIELD                    premium_edge, premium_edge_pct, annualized_yield,
                                  annualized_premium_edge, below_be, below_k, touch_k,
                                  dte, strike, expiration
  --order asc|desc                Sort order (default: desc)
  --limit N                       Keep first N sorted rows
  --output FILE                   Write output to a file instead of stdout

Local input overrides:
  --input FILE, -i FILE           Local JSON input for evaluate
  --symbol SYMBOL                 Override local input symbol
  --s0 PRICE                      Override local input current price
  --as-of YYYY-MM-DD              Override local input as-of date

Schwab options:
  --token-env NAME                Env var containing Schwab access token (default: SCHWAB_ACCESS_TOKEN)
  --access-token TOKEN            Direct token override; prefer --token-env for shell history safety
  --strike-count N                Schwab strikeCount parameter (default: 40)
  --period-years N                Schwab pricehistory years to fetch (default: 1)
  --base-url URL                  Schwab marketdata base URL override for tests

Scope warning:
  PremiumEdge is calculated from the most recent one-year historical window by default. Weekly max
  drawdown/support are informational price context only. The model excludes earnings, news,
  liquidity, position sizing, and any trade recommendation.
`;

const aliases: Record<string, string> = {
  h: 'help',
  i: 'input',
  f: 'format',
  o: 'output',
};

const normalizeFlagName = (name: string): string => aliases[name] ?? name;

const parseArgs = (argv: string[]): ParsedArgs => {
  const [first, ...rest] = argv;
  const command = first && !first.startsWith('-') ? first : 'help';
  const tokens = first && !first.startsWith('-') ? rest : argv;
  const flags = new Map<string, FlagValue>();
  const positionals: string[] = [];

  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (!token) continue;
    if (token === '--') {
      positionals.push(...tokens.slice(index + 1));
      break;
    }
    if (token.startsWith('--no-')) {
      flags.set(normalizeFlagName(token.slice(5)), false);
      continue;
    }
    if (token.startsWith('--')) {
      const raw = token.slice(2);
      const eqIndex = raw.indexOf('=');
      if (eqIndex >= 0) {
        flags.set(normalizeFlagName(raw.slice(0, eqIndex)), raw.slice(eqIndex + 1));
        continue;
      }
      const next = tokens[index + 1];
      if (next && !next.startsWith('-')) {
        flags.set(normalizeFlagName(raw), next);
        index += 1;
      } else {
        flags.set(normalizeFlagName(raw), true);
      }
      continue;
    }
    if (token.startsWith('-') && token.length === 2) {
      const flagName = normalizeFlagName(token.slice(1));
      const next = tokens[index + 1];
      if (next && !next.startsWith('-')) {
        flags.set(flagName, next);
        index += 1;
      } else {
        flags.set(flagName, true);
      }
      continue;
    }
    positionals.push(token);
  }
  return { command, positionals, flags };
};

const hasFlag = (flags: Map<string, FlagValue>, name: string): boolean => flags.has(name);
const getString = (flags: Map<string, FlagValue>, name: string): string | undefined => {
  const value = flags.get(name);
  if (typeof value === 'string') return value;
  return undefined;
};

const getNumber = (flags: Map<string, FlagValue>, name: string): number | undefined => {
  const raw = getString(flags, name);
  if (raw == null || raw === '') return undefined;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) throw new Error(`--${name} must be numeric`);
  return parsed;
};

const getInteger = (flags: Map<string, FlagValue>, name: string): number | undefined => {
  const parsed = getNumber(flags, name);
  if (parsed == null) return undefined;
  if (!Number.isInteger(parsed)) throw new Error(`--${name} must be an integer`);
  return parsed;
};

const getBoolean = (flags: Map<string, FlagValue>, name: string): boolean => flags.get(name) === true;

const parseFormat = (flags: Map<string, FlagValue>): OutputFormat => {
  const format = getString(flags, 'format') ?? 'table';
  if (format === 'table' || format === 'json' || format === 'csv') return format;
  throw new Error('--format must be table, json, or csv');
};

const parseWindowMode = (flags: Map<string, FlagValue>): WindowMode => {
  const mode = getString(flags, 'window-mode') ?? 'calendar';
  if (mode === 'calendar' || mode === 'trading-days') return mode;
  throw new Error('--window-mode must be calendar or trading-days');
};

const parseOrder = (flags: Map<string, FlagValue>): Order => {
  const order = getString(flags, 'order') ?? 'desc';
  if (order === 'asc' || order === 'desc') return order;
  throw new Error('--order must be asc or desc');
};

const parseSortField = (flags: Map<string, FlagValue>): SortField => {
  const sort = getString(flags, 'sort') ?? 'premium_edge';
  const aliases: Record<string, SortField> = {
    seller_ev: 'premium_edge',
    seller_ev_pct: 'premium_edge_pct',
    ann_yield: 'annualized_yield',
    ann_edge: 'annualized_premium_edge',
    hist_loss_prob: 'below_be',
    hist_itm_prob: 'below_k',
    hist_touch_prob: 'touch_k',
  };
  const normalized = aliases[sort] ?? sort;
  const allowed = new Set<SortField>(['premium_edge', 'premium_edge_pct', 'annualized_yield', 'annualized_premium_edge', 'below_be', 'below_k', 'touch_k', 'dte', 'strike', 'expiration']);
  if (allowed.has(normalized as SortField)) return normalized as SortField;
  throw new Error(`unsupported --sort field: ${sort}`);
};

const buildEvaluateConfig = (flags: Map<string, FlagValue>): EvaluateConfig => ({
  windowMode: parseWindowMode(flags),
  lookbackDays: getInteger(flags, 'lookback-days') ?? DEFAULT_LOOKBACK_DAYS,
  minDte: getInteger(flags, 'min-dte') ?? DEFAULT_MIN_DTE,
  maxDte: getInteger(flags, 'max-dte') ?? DEFAULT_MAX_DTE,
  minBid: getNumber(flags, 'min-bid'),
  otmOnly: getBoolean(flags, 'otm-only'),
});

const rowSortValue = (row: OptionRiskRow, field: SortField): string | number | null => {
  if (field === 'premium_edge') return row.premiumEdge;
  if (field === 'premium_edge_pct') return row.premiumEdgePctOfStrike;
  if (field === 'annualized_yield') return row.annualizedYield;
  if (field === 'annualized_premium_edge') return row.annualizedPremiumEdge;
  if (field === 'below_be') return row.expiryBelowBreakevenProb;
  if (field === 'below_k') return row.expiryBelowStrikeProb;
  if (field === 'touch_k') return row.touchedStrikeProb;
  if (field === 'dte') return row.dte;
  if (field === 'strike') return row.strike;
  return row.expiration;
};

const compareValues = (left: string | number | null, right: string | number | null): number => {
  if (left == null && right == null) return 0;
  if (left == null) return 1;
  if (right == null) return -1;
  if (typeof left === 'string' || typeof right === 'string') return String(left).localeCompare(String(right));
  return left - right;
};

const postProcess = (result: EvaluationResult, flags: Map<string, FlagValue>): EvaluationResult => {
  const sort = parseSortField(flags);
  const order = parseOrder(flags);
  const direction = order === 'asc' ? 1 : -1;
  const sorted = [...result.rows].sort((left, right) => {
    const primary = compareValues(rowSortValue(left, sort), rowSortValue(right, sort));
    if (primary !== 0) return primary * direction;
    const risk = compareValues(left.expiryBelowBreakevenProb, right.expiryBelowBreakevenProb);
    if (risk !== 0) return risk;
    const dte = compareValues(left.dte, right.dte);
    if (dte !== 0) return dte;
    return compareValues(left.strike, right.strike);
  });
  const limit = getInteger(flags, 'limit');
  if (limit != null && limit < 1) throw new Error('--limit must be >= 1');
  return {
    ...result,
    rows: limit == null ? sorted : sorted.slice(0, limit),
  };
};

const emit = async (result: EvaluationResult, flags: Map<string, FlagValue>): Promise<void> => {
  const format = parseFormat(flags);
  const rendered = formatResult(result, format);
  const output = getString(flags, 'output');
  if (output) await writeFile(output, `${rendered}\n`, 'utf8');
  else process.stdout.write(`${rendered}\n`);
  if (format === 'table') process.stderr.write(`${formatModelScopeNote()}\n`);
};

const runEvaluate = async (flags: Map<string, FlagValue>): Promise<void> => {
  const inputPath = getString(flags, 'input');
  if (!inputPath) throw new Error('evaluate requires --input FILE');
  const input = await loadEvaluateInput(inputPath, {
    symbol: getString(flags, 'symbol'),
    s0: getNumber(flags, 's0'),
    asOf: getString(flags, 'as-of'),
  });
  const result = postProcess(evaluateInput(input, buildEvaluateConfig(flags)), flags);
  await emit(result, flags);
};

const runSchwab = async (positionals: string[], flags: Map<string, FlagValue>): Promise<void> => {
  const symbol = positionals[0];
  if (!symbol) throw new Error('schwab requires SYMBOL');
  const tokenEnv = getString(flags, 'token-env') ?? 'SCHWAB_ACCESS_TOKEN';
  const accessToken = getString(flags, 'access-token') ?? process.env[tokenEnv] ?? '';
  if (!accessToken) throw new Error(`Schwab access token missing; set ${tokenEnv} or pass --access-token`);

  const minDte = getInteger(flags, 'min-dte') ?? DEFAULT_MIN_DTE;
  const maxDte = getInteger(flags, 'max-dte') ?? DEFAULT_MAX_DTE;
  const input = await fetchSchwabEvaluateInput(symbol, {
    accessToken,
    baseUrl: getString(flags, 'base-url'),
    minDte,
    maxDte,
    strikeCount: getInteger(flags, 'strike-count') ?? 40,
    periodYears: getInteger(flags, 'period-years') ?? DEFAULT_PERIOD_YEARS,
  });
  const result = postProcess(evaluateInput(input, buildEvaluateConfig(flags)), flags);
  await emit(result, flags);
};

export const main = async (argv = process.argv.slice(2)): Promise<void> => {
  const parsed = parseArgs(argv);
  if (parsed.command === 'help' || hasFlag(parsed.flags, 'help')) {
    process.stdout.write(HELP);
    return;
  }
  if (parsed.command === 'evaluate') {
    await runEvaluate(parsed.flags);
    return;
  }
  if (parsed.command === 'schwab') {
    await runSchwab(parsed.positionals, parsed.flags);
    return;
  }
  throw new Error(`Unknown command: ${parsed.command}`);
};

const isEntrypoint = (): boolean => {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1]);
  } catch {
    return fileURLToPath(import.meta.url) === process.argv[1];
  }
};

if (isEntrypoint()) {
  main().catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`sell-put-risk error: ${message}\n`);
    process.exitCode = 1;
  });
}
