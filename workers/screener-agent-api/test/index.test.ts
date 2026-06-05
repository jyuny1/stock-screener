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
  SCREENER_AGENT_API_TOKEN: 'secret-token',
  STATIC_DATA_PREFIX: 'static-data',
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
    },
    'static-data/markets/us/scan/chunks/chunk-0001.json': { rows },
  }) as unknown as R2Bucket,
});

const request = (path: string, token = 'secret-token') => new Request(`https://api.test${path}`, {
  headers: { authorization: `Bearer ${token}` },
});

const json = async (response: Response) => response.json() as Promise<any>;

describe('screener agent api worker', () => {
  it('rejects missing bearer token', async () => {
    const response = await worker.fetch(new Request('https://api.test/api/screener/rows'), makeEnv());
    expect(response.status).toBe(401);
    expect(await json(response)).toEqual({ error: { code: 'unauthorized', message: 'Unauthorized' } });
  });

  it('returns manifest with update timestamps', async () => {
    const response = await worker.fetch(request('/api/screener/manifest'), makeEnv());
    expect(response.status).toBe(200);
    const payload = await json(response);
    expect(payload.data.default_query).toMatchObject({ sort: 'volume', order: 'desc', limit: 100 });
    expect(payload.meta.data_updated_at).toBe('2026-06-05T10:08:31Z');
    expect(payload.meta.as_of_date).toBe('2026-06-05');
  });

  it('defaults to volume desc with nulls last', async () => {
    const response = await worker.fetch(request('/api/screener/rows'), makeEnv());
    expect(response.status).toBe(200);
    const payload = await json(response);
    expect(payload.data.rows.map((row: any) => row.symbol)).toEqual(['HIGH', 'LOW', 'EMPTY']);
    expect(payload.data.sort).toEqual({ field: 'volume', order: 'desc', nulls: 'last' });
    expect(payload.meta.data_updated_at).toBe('2026-06-05T10:08:31Z');
  });

  it('filters, limits, and projects table fields', async () => {
    const response = await worker.fetch(
      request('/api/screener/rows?min_volume=1000000&gics_sector=Technology&limit=1&fields=symbol,volume'),
      makeEnv(),
    );
    expect(response.status).toBe(200);
    const payload = await json(response);
    expect(payload.data.rows).toEqual([{ symbol: 'HIGH', volume: 100_000_000 }]);
    expect(payload.data.pagination).toMatchObject({ limit: 1, offset: 0, returned: 1, total_filtered: 2, has_more: true });
  });

  it('keeps nulls last for ascending sorts too', async () => {
    const response = await worker.fetch(request('/api/screener/rows?sort=volume&order=asc'), makeEnv());
    expect(response.status).toBe(200);
    const payload = await json(response);
    expect(payload.data.rows.map((row: any) => row.symbol)).toEqual(['LOW', 'HIGH', 'EMPTY']);
  });

  it('rejects unsupported filters', async () => {
    const response = await worker.fetch(request('/api/screener/rows?foo=bar'), makeEnv());
    expect(response.status).toBe(400);
    expect((await json(response)).error.code).toBe('invalid_request');
  });

  it('rejects exact numeric filters in favor of min/max filters', async () => {
    const response = await worker.fetch(request('/api/screener/rows?volume=100'), makeEnv());
    expect(response.status).toBe(400);
    expect((await json(response)).error.details.field).toBe('volume');
  });
});
