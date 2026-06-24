import apiClient from './client';

export const fetchSupportResistance = async (symbol, params = {}) => {
  const normalizedSymbol = String(symbol || '').trim().toUpperCase();
  if (!normalizedSymbol) {
    throw new Error('Symbol is required');
  }

  const response = await apiClient.get(
    `/v1/support-resistance/${encodeURIComponent(normalizedSymbol)}`,
    {
      params: {
        period: '1y',
        minHistoryBars: 120,
        mergePercent: 0.5,
        mergeAtrMultiplier: 0.5,
        zigzagMinReversalPct: 3,
        zigzagAtrMultiplier: 1.5,
        minStrength: 40,
        maxLevelsPerSide: 8,
        ...params,
      },
      timeout: 60000,
    },
  );
  return response.data;
};

export const fetchSoxlSupportSnapshot = async () => {
  const response = await apiClient.get('/v1/soxl/support-snapshot', { timeout: 60000 });
  return response.data;
};
