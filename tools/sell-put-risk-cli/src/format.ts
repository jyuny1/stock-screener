import type { EvaluationResult, OptionRiskRow } from './model.js';

export type OutputFormat = 'table' | 'json' | 'csv';

interface TableColumn {
  key: keyof OptionRiskRow;
  label: string;
  align?: 'left' | 'right';
  format: (value: OptionRiskRow[keyof OptionRiskRow]) => string;
}

const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const text = (value: unknown): string => value == null ? '' : String(value);
const money = (value: unknown): string => isNumber(value) ? value.toFixed(2) : '';
const integer = (value: unknown): string => isNumber(value) ? Math.round(value).toString() : '';
const decimal = (digits = 3) => (value: unknown): string => isNumber(value) ? value.toFixed(digits) : '';
const percent = (digits = 1) => (value: unknown): string => isNumber(value) ? `${(value * 100).toFixed(digits)}%` : '';

const tableColumns: TableColumn[] = [
  { key: 'symbol', label: 'Symbol', align: 'left', format: text },
  { key: 'expiration', label: 'Expiration', align: 'left', format: text },
  { key: 'dte', label: 'DTE', align: 'right', format: integer },
  { key: 's0', label: 'S0', align: 'right', format: money },
  { key: 'strike', label: 'Strike', align: 'right', format: money },
  { key: 'bid', label: 'Bid', align: 'right', format: money },
  { key: 'ask', label: 'Ask', align: 'right', format: money },
  { key: 'breakeven', label: 'Breakeven', align: 'right', format: money },
  { key: 'strikeDistance', label: 'StrikeDist', align: 'right', format: percent(1) },
  { key: 'breakevenDistance', label: 'BEDist', align: 'right', format: percent(1) },
  { key: 'expiryBelowStrikeProb', label: 'BelowK', align: 'right', format: percent(1) },
  { key: 'touchedStrikeProb', label: 'TouchK', align: 'right', format: percent(1) },
  { key: 'expiryBelowBreakevenProb', label: 'BelowBE', align: 'right', format: percent(1) },
  { key: 'avgExpiryLoss', label: 'AvgLoss', align: 'right', format: money },
  { key: 'premiumEdge', label: 'PremiumEdge', align: 'right', format: money },
  { key: 'premiumEdgePctOfStrike', label: 'Edge/K', align: 'right', format: percent(2) },
  { key: 'annualizedYield', label: 'AnnYield', align: 'right', format: percent(1) },
  { key: 'annualizedPremiumEdge', label: 'AnnEdge', align: 'right', format: percent(1) },
  { key: 'weeklyMaxDrawdown', label: '1Y WkMDD', align: 'right', format: percent(1) },
  { key: 'supportLevel', label: 'Support', align: 'right', format: money },
  { key: 'strikeDistanceToSupport', label: 'K/Support', align: 'right', format: percent(1) },
  { key: 'sampleSize', label: 'Samples', align: 'right', format: integer },
  { key: 'verdict', label: 'Verdict', align: 'left', format: text },
];

const pad = (value: string, width: number, align: 'left' | 'right' = 'right'): string => (
  align === 'left' ? value.padEnd(width) : value.padStart(width)
);

export const formatTable = (result: EvaluationResult): string => {
  if (result.rows.length === 0) return 'No contracts matched the filters.';
  const body = result.rows.map((row) => tableColumns.map((column) => column.format(row[column.key])));
  const widths = tableColumns.map((column, index) => Math.max(
    column.label.length,
    ...body.map((values) => values[index]?.length ?? 0),
  ));
  const header = tableColumns.map((column, index) => pad(column.label, widths[index] ?? column.label.length, column.align)).join('  ');
  const divider = widths.map((width) => '-'.repeat(width)).join('  ');
  const lines = body.map((values) => values.map((value, index) => {
    const column = tableColumns[index];
    return pad(value, widths[index] ?? value.length, column?.align);
  }).join('  '));
  return [header, divider, ...lines].join('\n');
};

const csvColumns: Array<keyof OptionRiskRow> = [
  'symbol',
  'contractSymbol',
  'expiration',
  'dte',
  's0',
  'strike',
  'bid',
  'ask',
  'breakeven',
  'strikeDistance',
  'breakevenDistance',
  'expiryBelowStrikeProb',
  'touchedStrikeProb',
  'expiryBelowBreakevenProb',
  'avgExpiryLoss',
  'premiumEdge',
  'premiumEdgePctOfStrike',
  'annualizedYield',
  'annualizedPremiumEdge',
  'oneYearWeeklyHigh',
  'oneYearWeeklyLow',
  'weeklyMaxDrawdown',
  'supportLevel',
  'supportDistance',
  'strikeDistanceToSupport',
  'sampleSize',
  'theo',
  'delta',
  'volatility',
  'openInterest',
  'volume',
  'windowMode',
  'verdict',
];

const toSnakeCase = (value: string): string => value.replace(/[A-Z]/g, (match) => `_${match.toLowerCase()}`);

const csvEscape = (value: unknown): string => {
  if (value == null) return '';
  const rendered = isNumber(value) ? String(value) : String(value);
  return /[",\n\r]/.test(rendered) ? `"${rendered.replace(/"/g, '""')}"` : rendered;
};

export const formatCsv = (result: EvaluationResult): string => {
  const header = csvColumns.map((key) => toSnakeCase(String(key))).join(',');
  const rows = result.rows.map((row) => csvColumns.map((key) => csvEscape(row[key])).join(','));
  return [header, ...rows].join('\n');
};

export const formatResult = (result: EvaluationResult, format: OutputFormat): string => {
  if (format === 'json') return JSON.stringify(result, null, 2);
  if (format === 'csv') return formatCsv(result);
  return formatTable(result);
};

export const formatModelScopeNote = (): string => [
  'Scope: PremiumEdge uses one-year historical expiry loss + bid-premium compensation by default.',
  'Weekly max drawdown/support are informational price context only.',
  'Not included: earnings, news, liquidity, position sizing, or trade recommendation.',
].join(' ');
