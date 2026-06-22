const API_SCHEMA_VERSION = 'option-screener-api-v1';
const DEFAULT_PREFIX = 'static-data';
const DEFAULT_LIMIT = 100;
const MAX_LIMIT = 500;
const DEFAULT_SORT = 'volume';
const DEFAULT_ORDER = 'desc';
const CACHE_TTL_MS = 60_000;
const DEFAULT_OPTIONS_SORT = 'volume';
const DEFAULT_SUMMARY_SORT = 'put_volume';

export interface Env {
  STATIC_DATA_BUCKET: R2Bucket;
  OPTIONS_D1?: D1Database;
  OPTION_SCREENER_API_TOKEN: string;
  STATIC_DATA_PREFIX?: string;
  DEFAULT_ROWS_LIMIT?: string;
  MAX_ROWS_LIMIT?: string;
  DEFAULT_SORT?: string;
  DEFAULT_ORDER?: string;
}

type Order = 'asc' | 'desc';
type RowValue = string | number | boolean | null | undefined | number[];
type ScanRow = Record<string, RowValue>;

type CachedRows = {
  expiresAt: number;
  rows: ScanRow[];
  rowsTotal: number;
  scanManifest: Record<string, unknown>;
  staticManifest: Record<string, unknown>;
};

const TABLE_FIELDS = [
  'symbol',
  'current_price',
  'volume',
  'adv_usd',
  'price_change_1d',
  'rs_trend',
  'rs_rating',
  'adr_percent',
  'ma_alignment',
  'market_cap',
  'gics_sector',
  'ibd_industry_group',
] as const;

const NUMERIC_FIELDS = new Set([
  'current_price',
  'volume',
  'adv_usd',
  'price_change_1d',
  'rs_trend',
  'rs_rating',
  'adr_percent',
  'market_cap',
]);

const BOOLEAN_FIELDS = new Set(['ma_alignment']);
const TEXT_FIELDS = new Set(['symbol', 'gics_sector', 'ibd_industry_group']);
const SORTABLE_FIELDS: Set<string> = new Set(TABLE_FIELDS.filter((field) => field !== 'ma_alignment'));
const FILTERABLE_FIELDS: Set<string> = new Set(TABLE_FIELDS);

const FILTER_ALIASES: Record<string, string> = {
  min_price: 'min_current_price',
  max_price: 'max_current_price',
  min_rs: 'min_rs_rating',
  max_rs: 'max_rs_rating',
  min_adr: 'min_adr_percent',
  max_adr: 'max_adr_percent',
  sector: 'gics_sector',
  industry: 'ibd_industry_group',
};

const OPTION_CONTRACT_FIELDS = [
  'snapshot_date',
  'symbol',
  'option_type',
  'contract_symbol',
  'expiration_date',
  'strike',
  'dte_at_snapshot',
  'schwab_dte',
  'bid',
  'ask',
  'last',
  'mark',
  'volume',
  'open_interest',
  'iv',
  'delta',
  'theta',
  'theta_yield_pct',
  'spread_pct',
  'roc_pct',
  'asof',
  'provider',
  'created_at',
] as const;

const OPTION_SUMMARY_FIELDS = [
  'snapshot_date',
  'symbol',
  'put_volume',
  'call_volume',
  'put_oi',
  'call_oi',
  'pcr',
  'pcr_volume',
  'pcr_oi',
  'put_contract_count',
  'call_contract_count',
  'contract_count',
  'asof',
] as const;

const OPTION_CONTRACT_SQL_FIELDS: Record<string, string> = {
  snapshot_date: 'snapshot_date',
  symbol: 'underlying_symbol AS symbol',
  option_type: 'option_type',
  contract_symbol: 'contract_symbol',
  expiration_date: 'expiration_date',
  strike: 'strike',
  dte_at_snapshot: 'dte_at_snapshot',
  schwab_dte: 'schwab_dte',
  bid: 'bid',
  ask: 'ask',
  last: 'last',
  mark: 'mark',
  volume: 'volume',
  open_interest: 'open_interest',
  iv: 'iv',
  delta: 'delta',
  theta: 'theta',
  theta_yield_pct: 'theta_yield_pct',
  spread_pct: 'spread_pct',
  roc_pct: 'roc_pct',
  asof: 'asof',
  provider: 'provider',
  created_at: 'created_at',
};

const OPTION_CONTRACT_SORT_SQL: Record<string, string> = Object.fromEntries(
  OPTION_CONTRACT_FIELDS.map((field) => [field, field === 'symbol' ? 'underlying_symbol' : field]),
);
const OPTION_SUMMARY_SORT_SQL: Record<string, string> = Object.fromEntries(OPTION_SUMMARY_FIELDS.map((field) => [field, field]));

const OPTIONS_FILTER_PARAMS = new Set([
  'limit', 'offset', 'sort', 'order', 'fields', 'latest', 'snapshot_date', 'symbol', 'option_type', 'contract_symbol',
  'expiration_date', 'min_expiration_date', 'max_expiration_date', 'min_dte', 'max_dte', 'min_strike', 'max_strike',
  'min_volume', 'max_volume', 'min_open_interest', 'max_open_interest', 'min_delta', 'max_delta', 'min_iv', 'max_iv',
  'min_theta', 'max_theta', 'min_theta_yield_pct', 'max_theta_yield_pct', 'min_spread_pct', 'max_spread_pct',
  'min_roc_pct', 'max_roc_pct',
]);

let rowsCache: CachedRows | null = null;

const jsonResponse = (payload: unknown, init: ResponseInit = {}): Response => {
  const headers = new Headers(init.headers);
  headers.set('content-type', 'application/json; charset=utf-8');
  headers.set('cache-control', 'private, max-age=60');
  return new Response(JSON.stringify(payload, null, 2), { ...init, headers });
};

const errorResponse = (
  status: number,
  code: string,
  message: string,
  details?: Record<string, unknown>,
): Response => jsonResponse({ error: { code, message, ...(details ? { details } : {}) } }, { status });

const encode = (value: string): Uint8Array => new TextEncoder().encode(value);

const constantTimeEqual = (left: string, right: string): boolean => {
  const leftBytes = encode(left);
  const rightBytes = encode(right);
  const maxLength = Math.max(leftBytes.length, rightBytes.length, 1);
  let diff = leftBytes.length ^ rightBytes.length;
  for (let index = 0; index < maxLength; index += 1) {
    diff |= (leftBytes[index] ?? 0) ^ (rightBytes[index] ?? 0);
  }
  return diff === 0;
};

const authenticate = (request: Request, env: Env): boolean => {
  const header = request.headers.get('authorization') || '';
  const match = /^Bearer\s+(.+)$/i.exec(header.trim());
  const supplied = match?.[1] || '';
  const expected = env.OPTION_SCREENER_API_TOKEN || '';
  if (!expected || !supplied) return false;
  return constantTimeEqual(supplied, expected);
};

const prefix = (env: Env): string => (env.STATIC_DATA_PREFIX || DEFAULT_PREFIX).replace(/^\/+|\/+$/g, '');
const key = (env: Env, path: string): string => `${prefix(env)}/${path.replace(/^\/+/, '')}`;

const readR2Json = async <T>(env: Env, path: string): Promise<T> => {
  const object = await env.STATIC_DATA_BUCKET.get(key(env, path));
  if (!object) {
    throw new Error(`missing R2 object: ${key(env, path)}`);
  }
  return object.json<T>();
};

const loadRows = async (env: Env): Promise<CachedRows> => {
  const now = Date.now();
  if (rowsCache && rowsCache.expiresAt > now) {
    return rowsCache;
  }

  const [staticManifest, scanManifest] = await Promise.all([
    readR2Json<Record<string, unknown>>(env, 'manifest.json'),
    readR2Json<Record<string, unknown>>(env, 'markets/us/scan/manifest.json'),
  ]);

  const chunks = Array.isArray(scanManifest.chunks) ? scanManifest.chunks : [];
  const chunkPayloads = await Promise.all(chunks.map((chunk) => {
    if (!chunk || typeof chunk !== 'object' || typeof (chunk as { path?: unknown }).path !== 'string') {
      throw new Error('invalid scan chunk manifest entry');
    }
    return readR2Json<{ rows?: ScanRow[] }>(env, String((chunk as { path: string }).path));
  }));

  const rows = chunkPayloads.flatMap((payload) => Array.isArray(payload.rows) ? payload.rows : []);
  const rowsTotal = Number(scanManifest.rows_total ?? rows.length);
  rowsCache = { expiresAt: now + CACHE_TTL_MS, rows, rowsTotal, scanManifest, staticManifest };
  return rowsCache;
};

const numberParam = (params: URLSearchParams, name: string): number | null => {
  const raw = params.get(name);
  if (raw == null || raw === '') return null;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) {
    throw errorResponse(400, 'invalid_request', `${name} must be numeric`, { field: name });
  }
  return parsed;
};

const parseInteger = (params: URLSearchParams, name: string, fallback: number): number => {
  const raw = params.get(name);
  if (raw == null || raw === '') return fallback;
  const parsed = Number(raw);
  if (!Number.isInteger(parsed)) {
    throw errorResponse(400, 'invalid_request', `${name} must be an integer`, { field: name });
  }
  return parsed;
};

const parseBoolean = (raw: string, field: string): boolean => {
  if (raw === 'true') return true;
  if (raw === 'false') return false;
  throw errorResponse(400, 'invalid_request', `${field} must be true or false`, { field });
};

const normalizeParams = (params: URLSearchParams): URLSearchParams => {
  const normalized = new URLSearchParams(params);
  for (const [alias, canonical] of Object.entries(FILTER_ALIASES)) {
    if (normalized.has(alias) && !normalized.has(canonical)) {
      normalized.set(canonical, normalized.get(alias) || '');
    }
  }
  return normalized;
};

const asNumber = (value: RowValue): number | null => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return value;
};

const matchesFilters = (row: ScanRow, params: URLSearchParams): boolean => {
  const symbolNeedle = params.get('symbol')?.trim().toUpperCase();
  if (symbolNeedle && !String(row.symbol || '').toUpperCase().includes(symbolNeedle)) return false;

  for (const field of TABLE_FIELDS) {
    if (NUMERIC_FIELDS.has(field)) {
      const min = numberParam(params, `min_${field}`);
      const max = numberParam(params, `max_${field}`);
      const value = asNumber(row[field]);
      if (min != null && (value == null || value < min)) return false;
      if (max != null && (value == null || value > max)) return false;
    } else if (BOOLEAN_FIELDS.has(field)) {
      const raw = params.get(field);
      if (raw != null && Boolean(row[field]) !== parseBoolean(raw, field)) return false;
    } else if (TEXT_FIELDS.has(field) && field !== 'symbol') {
      const raw = params.get(field);
      if (raw != null && String(row[field] || '') !== raw) return false;
    }
  }
  return true;
};

const validateQueryFields = (params: URLSearchParams): Response | null => {
  const allowed = new Set(['limit', 'offset', 'sort', 'order', 'fields']);
  for (const field of TABLE_FIELDS) {
    if (NUMERIC_FIELDS.has(field)) {
      allowed.add(`min_${field}`);
      allowed.add(`max_${field}`);
    } else {
      allowed.add(field);
    }
  }
  for (const alias of Object.keys(FILTER_ALIASES)) allowed.add(alias);

  for (const keyName of params.keys()) {
    if (!allowed.has(keyName)) {
      return errorResponse(400, 'invalid_request', `unsupported query parameter: ${keyName}`, { field: keyName });
    }
  }
  return null;
};

const compareValues = (left: RowValue, right: RowValue): number => {
  if (left == null && right == null) return 0;
  if (left == null) return 1;
  if (right == null) return -1;
  if (typeof left === 'string' || typeof right === 'string') {
    return String(left).localeCompare(String(right));
  }
  return Number(left) - Number(right);
};

const sortRows = (rows: ScanRow[], sort: string, order: Order): ScanRow[] => {
  const direction = order === 'asc' ? 1 : -1;
  return [...rows].sort((left, right) => {
    const leftValue = left[sort];
    const rightValue = right[sort];
    if (leftValue == null && rightValue != null) return 1;
    if (leftValue != null && rightValue == null) return -1;
    const comparison = compareValues(leftValue, rightValue);
    if (comparison !== 0) return comparison * direction;
    return compareValues(left.symbol, right.symbol);
  });
};

const projectRows = (rows: ScanRow[], fieldsParam: string | null): ScanRow[] => {
  if (!fieldsParam) {
    return rows.map((row) => {
      const projected: ScanRow = {};
      for (const field of TABLE_FIELDS) projected[field] = row[field];
      return projected;
    });
  }
  const fields = [...new Set(['symbol', ...fieldsParam.split(',').map((field) => field.trim()).filter(Boolean)])];
  if (fields.length > 32) {
    throw errorResponse(413, 'response_too_large', 'fields count exceeds max 32');
  }
  for (const field of fields) {
    if (!FILTERABLE_FIELDS.has(field as typeof TABLE_FIELDS[number])) {
      throw errorResponse(400, 'invalid_request', `unsupported field: ${field}`, { field });
    }
  }
  return rows.map((row) => {
    const projected: ScanRow = {};
    for (const field of fields) projected[field] = row[field];
    return projected;
  });
};

const projectGenericRows = (rows: ScanRow[], fieldsParam: string | null, allowedFields: readonly string[]): ScanRow[] => {
  if (!fieldsParam) return rows;
  const fields = [...new Set(fieldsParam.split(',').map((field) => field.trim()).filter(Boolean))];
  if (fields.length > 32) {
    throw errorResponse(413, 'response_too_large', 'fields count exceeds max 32');
  }
  for (const field of fields) {
    if (!allowedFields.includes(field)) {
      throw errorResponse(400, 'invalid_request', `unsupported field: ${field}`, { field });
    }
  }
  return rows.map((row) => {
    const projected: ScanRow = {};
    for (const field of fields) projected[field] = row[field];
    return projected;
  });
};

const stringField = (payload: Record<string, unknown>, field: string): string | null => {
  const value = payload[field];
  return typeof value === 'string' && value ? value : null;
};

const commonMeta = (env: Env, loaded: CachedRows): Record<string, unknown> => ({
  market: 'US',
  rows_total: loaded.rowsTotal,
  generated_at: stringField(loaded.scanManifest, 'generated_at') || stringField(loaded.staticManifest, 'generated_at'),
  as_of_date: stringField(loaded.scanManifest, 'as_of_date') || stringField(loaded.staticManifest, 'as_of_date'),
  data_updated_at: stringField(loaded.scanManifest, 'generated_at') || stringField(loaded.staticManifest, 'generated_at'),
  source: {
    type: 'r2-static-data',
    static_manifest_path: `${prefix(env)}/manifest.json`,
    scan_manifest_path: `${prefix(env)}/markets/us/scan/manifest.json`,
  },
});

const requireOptionsD1 = (env: Env): D1Database => {
  if (!env.OPTIONS_D1) {
    throw errorResponse(503, 'd1_unavailable', 'OPTIONS_D1 binding is not configured');
  }
  return env.OPTIONS_D1;
};

const validateAllowedParams = (params: URLSearchParams, allowed: Set<string>): Response | null => {
  for (const keyName of params.keys()) {
    if (!allowed.has(keyName)) {
      return errorResponse(400, 'invalid_request', `unsupported query parameter: ${keyName}`, { field: keyName });
    }
  }
  return null;
};

const parseDateParam = (params: URLSearchParams, name: string): string | null => {
  const raw = params.get(name)?.trim();
  if (!raw) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    throw errorResponse(400, 'invalid_request', `${name} must be YYYY-MM-DD`, { field: name });
  }
  return raw;
};

const parseOptionType = (params: URLSearchParams): string | null => {
  const raw = params.get('option_type')?.trim().toUpperCase();
  if (!raw) return null;
  if (raw !== 'PUT' && raw !== 'CALL') {
    throw errorResponse(400, 'invalid_request', 'option_type must be PUT or CALL', { field: 'option_type' });
  }
  return raw;
};

const parseSqlFields = (fieldsParam: string | null, allowedFields: readonly string[], sqlFields: Record<string, string>): string => {
  const fields = fieldsParam
    ? [...new Set(fieldsParam.split(',').map((field) => field.trim()).filter(Boolean))]
    : [...allowedFields];
  if (fields.length > 32) {
    throw errorResponse(413, 'response_too_large', 'fields count exceeds max 32');
  }
  for (const field of fields) {
    if (!allowedFields.includes(field)) {
      throw errorResponse(400, 'invalid_request', `unsupported field: ${field}`, { field });
    }
  }
  return fields.map((field) => sqlFields[field]).join(', ');
};

const latestOptionSnapshotDate = async (db: D1Database): Promise<string | null> => {
  const result = await db.prepare('SELECT MAX(snapshot_date) AS snapshot_date FROM option_contract_liquidity_snapshots').first<{ snapshot_date?: string }>();
  return typeof result?.snapshot_date === 'string' ? result.snapshot_date : null;
};

const optionMetadata = async (db: D1Database): Promise<Record<string, string>> => {
  const result = await db.prepare('SELECT key, value FROM metadata').all<{ key: string; value: string }>();
  const rows = result.results || [];
  return Object.fromEntries(rows.map((row) => [row.key, row.value]));
};

type SqlWhere = { clause: string; bindings: (string | number)[]; snapshotDate: string | null };

const buildOptionWhere = async (db: D1Database, params: URLSearchParams): Promise<SqlWhere> => {
  const clauses: string[] = [];
  const bindings: (string | number)[] = [];

  const snapshotDate = parseDateParam(params, 'snapshot_date');
  const latest = params.get('latest') == null ? true : parseBoolean(params.get('latest') || '', 'latest');
  let effectiveSnapshot = snapshotDate;
  if (!effectiveSnapshot && latest) effectiveSnapshot = await latestOptionSnapshotDate(db);
  if (effectiveSnapshot) {
    clauses.push('snapshot_date = ?');
    bindings.push(effectiveSnapshot);
  }

  const symbol = params.get('symbol')?.trim().toUpperCase();
  if (symbol) {
    clauses.push('underlying_symbol = ?');
    bindings.push(symbol);
  }

  const contractSymbol = params.get('contract_symbol')?.trim();
  if (contractSymbol) {
    clauses.push('contract_symbol = ?');
    bindings.push(contractSymbol);
  }

  const optionType = parseOptionType(params);
  if (optionType) {
    clauses.push('option_type = ?');
    bindings.push(optionType);
  }

  const expirationDate = parseDateParam(params, 'expiration_date');
  const minExpirationDate = parseDateParam(params, 'min_expiration_date');
  const maxExpirationDate = parseDateParam(params, 'max_expiration_date');
  if (expirationDate) {
    clauses.push('expiration_date = ?');
    bindings.push(expirationDate);
  }
  if (minExpirationDate) {
    clauses.push('expiration_date >= ?');
    bindings.push(minExpirationDate);
  }
  if (maxExpirationDate) {
    clauses.push('expiration_date <= ?');
    bindings.push(maxExpirationDate);
  }

  const numericFilters: [string, string, string][] = [
    ['min_dte', 'dte_at_snapshot', '>='], ['max_dte', 'dte_at_snapshot', '<='],
    ['min_strike', 'strike', '>='], ['max_strike', 'strike', '<='],
    ['min_volume', 'volume', '>='], ['max_volume', 'volume', '<='],
    ['min_open_interest', 'open_interest', '>='], ['max_open_interest', 'open_interest', '<='],
    ['min_delta', 'delta', '>='], ['max_delta', 'delta', '<='],
    ['min_iv', 'iv', '>='], ['max_iv', 'iv', '<='],
    ['min_theta', 'theta', '>='], ['max_theta', 'theta', '<='],
    ['min_theta_yield_pct', 'theta_yield_pct', '>='], ['max_theta_yield_pct', 'theta_yield_pct', '<='],
    ['min_spread_pct', 'spread_pct', '>='], ['max_spread_pct', 'spread_pct', '<='],
    ['min_roc_pct', 'roc_pct', '>='], ['max_roc_pct', 'roc_pct', '<='],
  ];
  for (const [param, column, operator] of numericFilters) {
    const value = numberParam(params, param);
    if (value != null) {
      clauses.push(`${column} ${operator} ?`);
      bindings.push(value);
    }
  }

  return { clause: clauses.length ? `WHERE ${clauses.join(' AND ')}` : '', bindings, snapshotDate: effectiveSnapshot };
};

const d1Meta = (metadata: Record<string, string>, rowsTotal: number, extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  market: 'US',
  rows_total: rowsTotal,
  generated_at: metadata.generated_at || null,
  as_of_date: metadata.as_of_date || null,
  data_updated_at: metadata.generated_at || null,
  source: { type: 'cloudflare-d1', binding: 'OPTIONS_D1', table: 'option_contract_liquidity_snapshots' },
  ...extra,
});

const handleHealth = async (env: Env): Promise<Response> => {
  await readR2Json<Record<string, unknown>>(env, 'markets/us/scan/manifest.json');
  return jsonResponse({
    schema_version: API_SCHEMA_VERSION,
    data: { status: 'ok', source: 'r2-static-data' },
    meta: { market: 'US' },
  });
};

const handleManifest = async (env: Env): Promise<Response> => {
  const loaded = await loadRows(env);
  return jsonResponse({
    schema_version: API_SCHEMA_VERSION,
    data: {
      market: 'US',
      rows_total: loaded.rowsTotal,
      default_query: { sort: DEFAULT_SORT, order: DEFAULT_ORDER, limit: DEFAULT_LIMIT, nulls: 'last' },
      columns: TABLE_FIELDS,
      filterable_fields: TABLE_FIELDS,
      sortable_fields: [...SORTABLE_FIELDS],
    },
    meta: commonMeta(env, loaded),
  });
};

const handleRows = async (request: Request, env: Env): Promise<Response> => {
  const url = new URL(request.url);
  const params = normalizeParams(url.searchParams);
  const invalid = validateQueryFields(params);
  if (invalid) return invalid;

  const configuredDefaultLimit = Number(env.DEFAULT_ROWS_LIMIT || DEFAULT_LIMIT);
  const configuredMaxLimit = Number(env.MAX_ROWS_LIMIT || MAX_LIMIT);
  const maxLimit = Number.isFinite(configuredMaxLimit) ? configuredMaxLimit : MAX_LIMIT;
  const defaultLimit = Number.isFinite(configuredDefaultLimit) ? configuredDefaultLimit : DEFAULT_LIMIT;
  const limit = parseInteger(params, 'limit', defaultLimit);
  const offset = parseInteger(params, 'offset', 0);
  if (limit < 1 || limit > maxLimit) {
    return errorResponse(400, 'invalid_request', `limit must be between 1 and ${maxLimit}`, { field: 'limit' });
  }
  if (offset < 0) {
    return errorResponse(400, 'invalid_request', 'offset must be >= 0', { field: 'offset' });
  }

  const sort = params.get('sort') || env.DEFAULT_SORT || DEFAULT_SORT;
  if (!SORTABLE_FIELDS.has(sort)) {
    return errorResponse(400, 'invalid_request', `unsupported sort field: ${sort}`, { field: 'sort' });
  }
  const order = (params.get('order') || env.DEFAULT_ORDER || DEFAULT_ORDER).toLowerCase();
  if (order !== 'asc' && order !== 'desc') {
    return errorResponse(400, 'invalid_request', 'order must be asc or desc', { field: 'order' });
  }

  try {
    const loaded = await loadRows(env);
    const filtered = loaded.rows.filter((row) => matchesFilters(row, params));
    const sorted = sortRows(filtered, sort, order);
    const page = sorted.slice(offset, offset + limit);
    const rows = projectRows(page, params.get('fields'));
    return jsonResponse({
      schema_version: API_SCHEMA_VERSION,
      data: {
        rows,
        pagination: {
          limit,
          offset,
          returned: rows.length,
          total_filtered: filtered.length,
          has_more: offset + rows.length < filtered.length,
        },
        sort: { field: sort, order, nulls: 'last' },
        filters: Object.fromEntries(params.entries()),
      },
      meta: commonMeta(env, loaded),
    });
  } catch (error) {
    if (error instanceof Response) return error;
    throw error;
  }
};

const handleOptionsManifest = async (env: Env): Promise<Response> => {
  const db = requireOptionsD1(env);
  const [metadata, countResult, latestSnapshot] = await Promise.all([
    optionMetadata(db),
    db.prepare('SELECT COUNT(*) AS count FROM option_contract_liquidity_snapshots').first<{ count: number }>(),
    latestOptionSnapshotDate(db),
  ]);
  const rowsTotal = Number(countResult?.count || 0);
  return jsonResponse({
    schema_version: API_SCHEMA_VERSION,
    data: {
      market: 'US',
      latest_snapshot_date: latestSnapshot,
      default_query: { sort: DEFAULT_OPTIONS_SORT, order: DEFAULT_ORDER, limit: DEFAULT_LIMIT, latest: true, nulls: 'last' },
      contract_columns: OPTION_CONTRACT_FIELDS,
      summary_columns: OPTION_SUMMARY_FIELDS,
      filterable_fields: [...OPTIONS_FILTER_PARAMS].filter((field) => !['limit', 'offset', 'sort', 'order', 'fields'].includes(field)),
      sortable_contract_fields: Object.keys(OPTION_CONTRACT_SORT_SQL),
      sortable_summary_fields: Object.keys(OPTION_SUMMARY_SORT_SQL),
      metadata,
    },
    meta: d1Meta(metadata, rowsTotal),
  });
};

const handleOptionContracts = async (request: Request, env: Env): Promise<Response> => {
  const db = requireOptionsD1(env);
  const url = new URL(request.url);
  const params = url.searchParams;
  const invalid = validateAllowedParams(params, OPTIONS_FILTER_PARAMS);
  if (invalid) return invalid;

  const configuredDefaultLimit = Number(env.DEFAULT_ROWS_LIMIT || DEFAULT_LIMIT);
  const configuredMaxLimit = Number(env.MAX_ROWS_LIMIT || MAX_LIMIT);
  const maxLimit = Number.isFinite(configuredMaxLimit) ? configuredMaxLimit : MAX_LIMIT;
  const defaultLimit = Number.isFinite(configuredDefaultLimit) ? configuredDefaultLimit : DEFAULT_LIMIT;
  const limit = parseInteger(params, 'limit', defaultLimit);
  const offset = parseInteger(params, 'offset', 0);
  if (limit < 1 || limit > maxLimit) return errorResponse(400, 'invalid_request', `limit must be between 1 and ${maxLimit}`, { field: 'limit' });
  if (offset < 0) return errorResponse(400, 'invalid_request', 'offset must be >= 0', { field: 'offset' });

  const sort = params.get('sort') || DEFAULT_OPTIONS_SORT;
  const sortSql = OPTION_CONTRACT_SORT_SQL[sort];
  if (!sortSql) return errorResponse(400, 'invalid_request', `unsupported sort field: ${sort}`, { field: 'sort' });
  const order = (params.get('order') || DEFAULT_ORDER).toLowerCase();
  if (order !== 'asc' && order !== 'desc') return errorResponse(400, 'invalid_request', 'order must be asc or desc', { field: 'order' });

  const selectSql = parseSqlFields(params.get('fields'), OPTION_CONTRACT_FIELDS, OPTION_CONTRACT_SQL_FIELDS);
  const where = await buildOptionWhere(db, params);
  const direction = order === 'asc' ? 'ASC' : 'DESC';
  const rowsSql = `SELECT ${selectSql} FROM option_contract_liquidity_snapshots ${where.clause} ORDER BY ${sortSql} IS NULL ASC, ${sortSql} ${direction}, underlying_symbol ASC, expiration_date ASC, strike ASC LIMIT ? OFFSET ?`;
  const countSql = `SELECT COUNT(*) AS count FROM option_contract_liquidity_snapshots ${where.clause}`;
  const [rowsResult, countResult, metadata] = await Promise.all([
    db.prepare(rowsSql).bind(...where.bindings, limit, offset).all<ScanRow>(),
    db.prepare(countSql).bind(...where.bindings).first<{ count: number }>(),
    optionMetadata(db),
  ]);
  const rows = rowsResult.results || [];
  const totalFiltered = Number(countResult?.count || 0);
  return jsonResponse({
    schema_version: API_SCHEMA_VERSION,
    data: {
      rows,
      pagination: { limit, offset, returned: rows.length, total_filtered: totalFiltered, has_more: offset + rows.length < totalFiltered },
      sort: { field: sort, order, nulls: 'last' },
      filters: Object.fromEntries(params.entries()),
    },
    meta: d1Meta(metadata, totalFiltered, { snapshot_date: where.snapshotDate }),
  });
};

const handleOptionSummary = async (request: Request, env: Env): Promise<Response> => {
  const db = requireOptionsD1(env);
  const url = new URL(request.url);
  const params = url.searchParams;
  const invalid = validateAllowedParams(params, OPTIONS_FILTER_PARAMS);
  if (invalid) return invalid;

  const configuredDefaultLimit = Number(env.DEFAULT_ROWS_LIMIT || DEFAULT_LIMIT);
  const configuredMaxLimit = Number(env.MAX_ROWS_LIMIT || MAX_LIMIT);
  const maxLimit = Number.isFinite(configuredMaxLimit) ? configuredMaxLimit : MAX_LIMIT;
  const defaultLimit = Number.isFinite(configuredDefaultLimit) ? configuredDefaultLimit : DEFAULT_LIMIT;
  const limit = parseInteger(params, 'limit', defaultLimit);
  const offset = parseInteger(params, 'offset', 0);
  if (limit < 1 || limit > maxLimit) return errorResponse(400, 'invalid_request', `limit must be between 1 and ${maxLimit}`, { field: 'limit' });
  if (offset < 0) return errorResponse(400, 'invalid_request', 'offset must be >= 0', { field: 'offset' });

  const sort = params.get('sort') || DEFAULT_SUMMARY_SORT;
  const sortSql = OPTION_SUMMARY_SORT_SQL[sort];
  if (!sortSql) return errorResponse(400, 'invalid_request', `unsupported sort field: ${sort}`, { field: 'sort' });
  const order = (params.get('order') || DEFAULT_ORDER).toLowerCase();
  if (order !== 'asc' && order !== 'desc') return errorResponse(400, 'invalid_request', 'order must be asc or desc', { field: 'order' });

  const where = await buildOptionWhere(db, params);
  const groupedSql = `
    SELECT
      snapshot_date,
      underlying_symbol AS symbol,
      SUM(CASE WHEN option_type = 'PUT' THEN volume ELSE 0 END) AS put_volume,
      SUM(CASE WHEN option_type = 'CALL' THEN volume ELSE 0 END) AS call_volume,
      SUM(CASE WHEN option_type = 'PUT' THEN open_interest ELSE 0 END) AS put_oi,
      SUM(CASE WHEN option_type = 'CALL' THEN open_interest ELSE 0 END) AS call_oi,
      CASE WHEN SUM(CASE WHEN option_type = 'CALL' THEN volume ELSE 0 END) > 0
        THEN CAST(SUM(CASE WHEN option_type = 'PUT' THEN volume ELSE 0 END) AS REAL) / SUM(CASE WHEN option_type = 'CALL' THEN volume ELSE 0 END)
        ELSE NULL END AS pcr,
      CASE WHEN SUM(CASE WHEN option_type = 'CALL' THEN volume ELSE 0 END) > 0
        THEN CAST(SUM(CASE WHEN option_type = 'PUT' THEN volume ELSE 0 END) AS REAL) / SUM(CASE WHEN option_type = 'CALL' THEN volume ELSE 0 END)
        ELSE NULL END AS pcr_volume,
      CASE WHEN SUM(CASE WHEN option_type = 'CALL' THEN open_interest ELSE 0 END) > 0
        THEN CAST(SUM(CASE WHEN option_type = 'PUT' THEN open_interest ELSE 0 END) AS REAL) / SUM(CASE WHEN option_type = 'CALL' THEN open_interest ELSE 0 END)
        ELSE NULL END AS pcr_oi,
      SUM(CASE WHEN option_type = 'PUT' THEN 1 ELSE 0 END) AS put_contract_count,
      SUM(CASE WHEN option_type = 'CALL' THEN 1 ELSE 0 END) AS call_contract_count,
      COUNT(*) AS contract_count,
      MAX(asof) AS asof
    FROM option_contract_liquidity_snapshots
    ${where.clause}
    GROUP BY snapshot_date, underlying_symbol
  `;
  const direction = order === 'asc' ? 'ASC' : 'DESC';
  const rowsSql = `SELECT * FROM (${groupedSql}) ORDER BY ${sortSql} IS NULL ASC, ${sortSql} ${direction}, symbol ASC LIMIT ? OFFSET ?`;
  const countSql = `SELECT COUNT(*) AS count FROM (SELECT 1 FROM option_contract_liquidity_snapshots ${where.clause} GROUP BY snapshot_date, underlying_symbol)`;
  const [rowsResult, countResult, metadata] = await Promise.all([
    db.prepare(rowsSql).bind(...where.bindings, limit, offset).all<ScanRow>(),
    db.prepare(countSql).bind(...where.bindings).first<{ count: number }>(),
    optionMetadata(db),
  ]);
  const rows = projectGenericRows(rowsResult.results || [], params.get('fields'), OPTION_SUMMARY_FIELDS);
  const totalFiltered = Number(countResult?.count || 0);
  return jsonResponse({
    schema_version: API_SCHEMA_VERSION,
    data: {
      rows,
      pagination: { limit, offset, returned: rows.length, total_filtered: totalFiltered, has_more: offset + rows.length < totalFiltered },
      sort: { field: sort, order, nulls: 'last' },
      filters: Object.fromEntries(params.entries()),
    },
    meta: d1Meta(metadata, totalFiltered, { snapshot_date: where.snapshotDate }),
  });
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method !== 'GET') {
      return errorResponse(405, 'method_not_allowed', 'Only GET is supported');
    }
    if (!authenticate(request, env)) {
      return errorResponse(401, 'unauthorized', 'Unauthorized');
    }

    const { pathname } = new URL(request.url);
    try {
      if (pathname === '/api/v1/health') return await handleHealth(env);
      if (pathname === '/api/v1/manifest') return await handleManifest(env);
      if (pathname === '/api/v1/rows') return await handleRows(request, env);
      if (pathname === '/api/v1/options/manifest') return await handleOptionsManifest(env);
      if (pathname === '/api/v1/options/contracts') return await handleOptionContracts(request, env);
      if (pathname === '/api/v1/options/summary') return await handleOptionSummary(request, env);
      return errorResponse(404, 'not_found', 'Not found');
    } catch (error) {
      if (error instanceof Response) return error;
      console.error(JSON.stringify({ event: 'option_screener_api_error', message: error instanceof Error ? error.message : String(error) }));
      return errorResponse(503, 'artifact_unavailable', 'Required artifact is unavailable');
    }
  },
};

export const internals = {
  TABLE_FIELDS,
  SORTABLE_FIELDS,
  constantTimeEqual,
};
