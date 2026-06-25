export const SCHWAB_TOKEN_URL = 'https://api.schwabapi.com/v1/oauth/token';

export type SchwabTokenPair = {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
};

export async function refreshSchwabToken(params: {
  clientId: string;
  clientSecret: string;
  refreshToken: string;
  tokenUrl?: string;
}): Promise<SchwabTokenPair> {
  const basic = btoa(`${params.clientId}:${params.clientSecret}`);
  const body = new URLSearchParams({ grant_type: 'refresh_token', refresh_token: params.refreshToken });
  const response = await fetch(params.tokenUrl ?? SCHWAB_TOKEN_URL, {
    method: 'POST',
    headers: {
      Authorization: `Basic ${basic}`,
      'Content-Type': 'application/x-www-form-urlencoded',
      Accept: 'application/json',
    },
    body,
  });
  if (!response.ok) throw new Error(`schwab_refresh_failed_${response.status}`);
  const payload = (await response.json()) as { access_token?: string; refresh_token?: string; expires_in?: number };
  if (!payload.access_token || !payload.refresh_token) throw new Error('schwab_refresh_missing_tokens');
  return {
    accessToken: payload.access_token,
    refreshToken: payload.refresh_token,
    expiresIn: Number(payload.expires_in ?? 1800),
  };
}
