import apiClient from './client';

export const fetchOptionChain = async (symbol) => {
  const normalizedSymbol = String(symbol || '').trim().toUpperCase();
  if (!normalizedSymbol) {
    throw new Error('Symbol is required');
  }
  const response = await apiClient.get(`/v1/options/${encodeURIComponent(normalizedSymbol)}/chain`, {
    params: { includeUnderlyingQuote: true },
    timeout: 60000,
  });
  return response.data;
};
