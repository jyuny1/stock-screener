import apiClient from './client';
import { fetchSupportResistance, fetchSoxlSupportSnapshot } from './supportResistance';

vi.mock('./client', () => ({
  default: {
    get: vi.fn(),
  },
}));

describe('supportResistance api helper', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('fetches normalized symbol with production defaults', async () => {
    apiClient.get.mockResolvedValueOnce({ data: { symbol: 'SOXL', levels: [] } });

    const result = await fetchSupportResistance('soxl');

    expect(result).toEqual({ symbol: 'SOXL', levels: [] });
    expect(apiClient.get).toHaveBeenCalledWith('/v1/support-resistance/SOXL', {
      params: {
        period: '1y',
        minHistoryBars: 120,
        mergePercent: 0.5,
        mergeAtrMultiplier: 0.5,
        zigzagMinReversalPct: 3,
        zigzagAtrMultiplier: 1.5,
        minStrength: 40,
        maxLevelsPerSide: 8,
      },
      timeout: 60000,
    });
  });

  it('allows caller overrides', async () => {
    apiClient.get.mockResolvedValueOnce({ data: { symbol: 'AAPL', levels: [] } });

    await fetchSupportResistance('aapl', { minStrength: 65, strikes: '190,195' });

    expect(apiClient.get).toHaveBeenCalledWith('/v1/support-resistance/AAPL', {
      params: expect.objectContaining({ minStrength: 65, strikes: '190,195' }),
      timeout: 60000,
    });
  });

  it('fetches SOXL D1 support snapshot from v1 endpoint', async () => {
    apiClient.get.mockResolvedValueOnce({ data: { symbol: 'SOXL', sellPutSupportBuckets: [] } });

    const result = await fetchSoxlSupportSnapshot();

    expect(result).toEqual({ symbol: 'SOXL', sellPutSupportBuckets: [] });
    expect(apiClient.get).toHaveBeenCalledWith('/v1/soxl/support-snapshot', { timeout: 60000 });
  });
});
