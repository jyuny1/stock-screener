import { afterEach, describe, expect, it, vi } from 'vitest';
import { refreshSchwabToken } from '../src/schwab';

describe('Schwab token refresh client', () => {
  afterEach(() => vi.restoreAllMocks());

  it('posts refresh token grant and normalizes the token pair', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ access_token: 'access', refresh_token: 'refresh-next', expires_in: 1800 }), { status: 200 }),
    );

    await expect(
      refreshSchwabToken({ clientId: 'client', clientSecret: 'secret', refreshToken: 'refresh-old' }),
    ).resolves.toEqual({ accessToken: 'access', refreshToken: 'refresh-next', expiresIn: 1800 });

    const [, init] = fetchMock.mock.calls[0];
    expect(init?.method).toBe('POST');
    expect(String(init?.body)).toContain('grant_type=refresh_token');
    expect(String(init?.body)).toContain('refresh_token=refresh-old');
  });

  it('does not expose Schwab response bodies on refresh errors', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ error: 'invalid_grant' }), { status: 400 }));

    await expect(
      refreshSchwabToken({ clientId: 'client', clientSecret: 'secret', refreshToken: 'bad' }),
    ).rejects.toThrow('schwab_refresh_failed_400');
  });
});
