import { describe, expect, it } from 'vitest';
import worker from '../src/index';
import type { Env } from '../src/index';

class FakeR2Object {
  constructor(private readonly payload: unknown) {}
  async json<T>(): Promise<T> {
    return this.payload as T;
  }
}

class FakeR2Bucket {
  constructor(private readonly objects: Record<string, unknown>) {}
  async get(key: string): Promise<FakeR2Object | null> {
    if (!(key in this.objects)) return null;
    return new FakeR2Object(this.objects[key]);
  }
}

const optionRows = [
  {
    snapshot_date: '2026-06-05', underlying_symbol: 'HIGH', option_type: 'PUT', contract_symbol: 'HIGH260626P00025000',
    expiration_date: '2026-06-26', strike: 25, dte_at_snapshot: 21, schwab_dte: 21, bid: 1.1, ask: 1.2,
    last: 1.05, mark: 1.15, volume: 100, open_interest: 500, iv: 0.4, delta: -0.2,
    theta: -0.04, theta_yield_pct: 0.16, spread_pct: 8.7, roc_pct: 4.4, asof: '2026-06-05T10:08:31Z',
    provider: 'schwab', created_at: '2026-06-05T10:08:31Z',
  },
  {
    snapshot_date: '2026-06-05', underlying_symbol: 'HIGH', option_type: 'CALL', contract_symbol: 'HIGH260626C00035000',
    expiration_date: '2026-06-26', strike: 35, dte_at_snapshot: 21, schwab_dte: 21, bid: 0.9, ask: 1.0,
    last: 0.95, mark: 0.95, volume: 50, open_interest: 300, iv: 0.35, delta: 0.25,
    theta: -0.03, theta_yield_pct: 0.086, spread_pct: 10.5, roc_pct: 2.57, asof: '2026-06-05T10:08:31Z',
    provider: 'schwab', created_at: '2026-06-05T10:08:31Z',
  },
  {
    snapshot_date: '2026-06-04', underlying_symbol: 'LOW', option_type: 'PUT', contract_symbol: 'LOW260626P00015000',
    expiration_date: '2026-06-26', strike: 15, dte_at_snapshot: 22, schwab_dte: 22, bid: 0.5, ask: 0.6,
    last: 0.55, mark: 0.55, volume: 20, open_interest: 80, iv: 0.3, delta: -0.18,
    theta: -0.02, theta_yield_pct: 0.133, spread_pct: 18.2, roc_pct: 3.33, asof: '2026-06-04T10:08:31Z',
    provider: 'schwab', created_at: '2026-06-04T10:08:31Z',
  },
];

class FakeD1Statement {
  private bindings: any[] = [];
  constructor(private readonly sql: string) {}
  bind(...bindings: any[]) {
    this.bindings = bindings;
    return this;
  }
  async first<T>(): Promise<T | null> {
    if (this.sql.includes('MAX(snapshot_date)')) return { snapshot_date: '2026-06-05' } as T;
    if (this.sql.includes('COUNT(*) AS count')) return { count: this.filtered().length } as T;
    return null;
  }
  async all<T>(): Promise<{ results: T[] }> {
    if (this.sql.includes('FROM metadata')) {
      return { results: [
        { key: 'schema_version', value: 'option-contract-liquidity-d1-v4' },
        { key: 'generated_at', value: '2026-06-05T10:08:31Z' },
        { key: 'as_of_date', value: '2026-06-05' },
      ] as T[] };
    }
    if (this.sql.includes('GROUP BY snapshot_date, underlying_symbol')) {
      const grouped = this.filtered().reduce<Record<string, any>>((acc, row) => {
        const key = `${row.snapshot_date}|${row.underlying_symbol}`;
        const item = acc[key] ||= { snapshot_date: row.snapshot_date, symbol: row.underlying_symbol, put_volume: 0, call_volume: 0, put_oi: 0, call_oi: 0, put_contract_count: 0, call_contract_count: 0, contract_count: 0, asof: row.asof };
        if (row.option_type === 'PUT') { item.put_volume += row.volume; item.put_oi += row.open_interest; item.put_contract_count += 1; }
        if (row.option_type === 'CALL') { item.call_volume += row.volume; item.call_oi += row.open_interest; item.call_contract_count += 1; }
        item.contract_count += 1;
        item.pcr = item.call_volume > 0 ? item.put_volume / item.call_volume : null;
        item.pcr_volume = item.pcr;
        item.pcr_oi = item.call_oi > 0 ? item.put_oi / item.call_oi : null;
        return acc;
      }, {});
      return { results: Object.values(grouped) as T[] };
    }
    return { results: this.filtered().map((row) => ({
      snapshot_date: row.snapshot_date,
      symbol: row.underlying_symbol,
      option_type: row.option_type,
      contract_symbol: row.contract_symbol,
      expiration_date: row.expiration_date,
      strike: row.strike,
      dte_at_snapshot: row.dte_at_snapshot,
      schwab_dte: row.schwab_dte,
      bid: row.bid,
      ask: row.ask,
      last: row.last,
      mark: row.mark,
      volume: row.volume,
      open_interest: row.open_interest,
      iv: row.iv,
      delta: row.delta,
      theta: row.theta,
      theta_yield_pct: row.theta_yield_pct,
      spread_pct: row.spread_pct,
      roc_pct: row.roc_pct,
      asof: row.asof,
      provider: row.provider,
      created_at: row.created_at,
    })) as T[] };
  }
  private filtered() {
    let rows = optionRows.slice();
    if (this.sql.includes('snapshot_date = ?')) rows = rows.filter((row) => this.bindings.includes(row.snapshot_date));
    if (this.sql.includes('underlying_symbol = ?')) rows = rows.filter((row) => this.bindings.includes(row.underlying_symbol));
    if (this.sql.includes('option_type = ?')) rows = rows.filter((row) => this.bindings.includes(row.option_type));
    return rows;
  }
}

class FakeD1Database {
  prepare(sql: string) { return new FakeD1Statement(sql); }
}

const rows = [
  {
    symbol: 'EMPTY',
    current_price: 10,
    volume: null,
    adv_usd: null,
    price_change_1d: 0,
    rs_trend: 0,
    rs_rating: 50,
    adr_percent: 1.2,
    ma_alignment: false,
    market_cap: 100,
    gics_sector: 'ETF',
    ibd_industry_group: 'ETF',
  },
  {
    symbol: 'LOW',
    current_price: 20,
    volume: 10_000_000,
    adv_usd: 200_000_000,
    price_change_1d: 1,
    rs_trend: 1,
    rs_rating: 70,
    adr_percent: 2.5,
    ma_alignment: true,
    market_cap: 1_000,
    gics_sector: 'Technology',
    ibd_industry_group: 'Software',
  },
  {
    symbol: 'HIGH',
    current_price: 30,
    volume: 100_000_000,
    adv_usd: 3_000_000_000,
    price_change_1d: -1,
    rs_trend: -1,
    rs_rating: 90,
    adr_percent: 4.2,
    ma_alignment: true,
    market_cap: 2_000,
    gics_sector: 'Technology',
    ibd_industry_group: 'Semiconductor',
  },
];

const makeEnv = (): Env => ({
  OPTION_SCREENER_API_TOKEN: 'secret-token',
  STATIC_DATA_PREFIX: 'static-data',
  OPTIONS_D1: new FakeD1Database() as unknown as D1Database,
  STATIC_DATA_BUCKET: new FakeR2Bucket({
    'static-data/manifest.json': {
      schema_version: 'static-site-v2',
      generated_at: '2026-06-05T10:08:30Z',
      as_of_date: '2026-06-05',
    },
    'static-data/markets/us/scan/manifest.json': {
      schema_version: 'static-scan-v1',
      generated_at: '2026-06-05T10:08:31Z',
      as_of_date: '2026-06-05',
      rows_total: 3,
      chunks: [{ path: 'markets/us/scan/chunks/chunk-0001.json', count: 3 }],
      api_chunks: [{ path: 'markets/us/scan/api-chunks/chunk-0001.json', count: 3 }],
    },
    'static-data/markets/us/scan/chunks/chunk-0001.json': { rows },
    'static-data/markets/us/scan/api-chunks/chunk-0001.json': { rows },
  }) as unknown as R2Bucket,
});

const request = (path: string, token = 'secret-token') => new Request(`https://api.test${path}`, {
  headers: { authorization: `Bearer ${token}` },
});

const json = async (response: Response) => response.json() as Promise<any>;

describe('screener agent api worker', () => {
  it('rejects missing bearer token', async () => {
    const response = await worker.fetch(new Request('https://api.test/api/v1/rows'), makeEnv());
    expect(response.status).toBe(401);
    expect(await json(response)).toEqual({ error: { code: 'unauthorized', message: 'Unauthorized' } });
  });

  it('returns manifest with update timestamps', async () => {
    const response = await worker.fetch(request('/api/v1/manifest'), makeEnv());
    expect(response.status).toBe(200);
    const payload = await json(response);
    expect(payload.data.default_query).toMatchObject({ sort: 'volume', order: 'desc', limit: 100 });
    expect(payload.meta.data_updated_at).toBe('2026-06-05T10:08:31Z');
    expect(payload.meta.as_of_date).toBe('2026-06-05');
  });

  it('defaults to volume desc with nulls last', async () => {
    const response = await worker.fetch(request('/api/v1/rows'), makeEnv());
    expect(response.status).toBe(200);
    const payload = await json(response);
    expect(payload.data.rows.map((row: any) => row.symbol)).toEqual(['HIGH', 'LOW', 'EMPTY']);
    expect(payload.data.sort).toEqual({ field: 'volume', order: 'desc', nulls: 'last' });
    expect(payload.meta.data_updated_at).toBe('2026-06-05T10:08:31Z');
  });

  it('filters, limits, and projects table fields', async () => {
    const response = await worker.fetch(
      request('/api/v1/rows?min_volume=1000000&gics_sector=Technology&limit=1&fields=symbol,volume'),
      makeEnv(),
    );
    expect(response.status).toBe(200);
    const payload = await json(response);
    expect(payload.data.rows).toEqual([{ symbol: 'HIGH', volume: 100_000_000 }]);
    expect(payload.data.pagination).toMatchObject({ limit: 1, offset: 0, returned: 1, total_filtered: 2, has_more: true });
  });

  it('keeps nulls last for ascending sorts too', async () => {
    const response = await worker.fetch(request('/api/v1/rows?sort=volume&order=asc'), makeEnv());
    expect(response.status).toBe(200);
    const payload = await json(response);
    expect(payload.data.rows.map((row: any) => row.symbol)).toEqual(['LOW', 'HIGH', 'EMPTY']);
  });

  it('rejects unsupported filters', async () => {
    const response = await worker.fetch(request('/api/v1/rows?foo=bar'), makeEnv());
    expect(response.status).toBe(400);
    expect((await json(response)).error.code).toBe('invalid_request');
  });

  it('rejects exact numeric filters in favor of min/max filters', async () => {
    const response = await worker.fetch(request('/api/v1/rows?volume=100'), makeEnv());
    expect(response.status).toBe(400);
    expect((await json(response)).error.details.field).toBe('volume');
  });

  it('returns D1 option manifest without changing static manifest', async () => {
    const response = await worker.fetch(request('/api/v1/options/manifest'), makeEnv());
    expect(response.status).toBe(200);
    const payload = await json(response);
    expect(payload.data.latest_snapshot_date).toBe('2026-06-05');
    expect(payload.data.contract_columns).toContain('contract_symbol');
    expect(payload.meta.source.type).toBe('cloudflare-d1');
  });

  it('queries D1 option contracts with latest snapshot and filters', async () => {
    const response = await worker.fetch(request('/api/v1/options/contracts?symbol=HIGH&option_type=PUT&fields=symbol,contract_symbol,volume'), makeEnv());
    expect(response.status).toBe(200);
    const payload = await json(response);
    expect(payload.data.rows[0]).toMatchObject({ symbol: 'HIGH', contract_symbol: 'HIGH260626P00025000', volume: 100 });
    expect(payload.meta.snapshot_date).toBe('2026-06-05');
  });

  it('returns D1 option summary aggregates', async () => {
    const response = await worker.fetch(request('/api/v1/options/summary?symbol=HIGH'), makeEnv());
    expect(response.status).toBe(200);
    const payload = await json(response);
    expect(payload.data.rows[0]).toMatchObject({ symbol: 'HIGH', put_volume: 100, call_volume: 50, pcr: 2 });
  });
});
