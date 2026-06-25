import { DurableObject } from 'cloudflare:workers';
import { decryptText, encryptText } from './crypto';
import { splitCsv, verifyGithubOidcJwt, type GithubOidcPolicy } from './githubOidc';
import { refreshSchwabToken } from './schwab';
import { timingSafeEqualString } from './encoding';

export type Env = {
  SCHWAB_TOKEN_STATE: DurableObjectNamespace<SchwabTokenState>;
  GITHUB_OIDC_AUDIENCE: string;
  GITHUB_ALLOWED_REPOSITORIES: string;
  GITHUB_ALLOWED_REFS: string;
  GITHUB_ALLOWED_WORKFLOWS: string;
  GITHUB_ALLOWED_EVENTS: string;
  ACCESS_TOKEN_REFRESH_SKEW_SECONDS?: string;
  TOKEN_ENCRYPTION_KEY: string;
  SCHWAB_CLIENT_ID: string;
  SCHWAB_CLIENT_SECRET: string;
  ADMIN_BOOTSTRAP_TOKEN?: string;
};

type TokenRecord = {
  encryptedAccessToken?: string;
  encryptedRefreshToken?: string;
  accessTokenExpiresAt?: number;
  updatedAt?: string;
};

type AccessTokenResult = {
  accessToken: string;
  expiresAt: string;
  refreshed: boolean;
};

const JSON_HEADERS = { 'Content-Type': 'application/json; charset=utf-8' };

function jsonResponse(payload: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(payload), { ...init, headers: { ...JSON_HEADERS, ...(init?.headers ?? {}) } });
}

function errorResponse(status: number, code: string): Response {
  return jsonResponse({ error: code }, { status });
}

function requireEnv(env: Env, key: keyof Env): string {
  const value = env[key];
  if (typeof value !== 'string' || !value) throw new Error(`missing_${String(key)}`);
  return value;
}

function accessTokenSkewSeconds(env: Env): number {
  const parsed = Number(env.ACCESS_TOKEN_REFRESH_SKEW_SECONDS ?? '300');
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 300;
}

function policyFromEnv(env: Env): GithubOidcPolicy {
  return {
    audience: requireEnv(env, 'GITHUB_OIDC_AUDIENCE'),
    allowedRepositories: splitCsv(env.GITHUB_ALLOWED_REPOSITORIES),
    allowedRefs: splitCsv(env.GITHUB_ALLOWED_REFS),
    allowedWorkflows: splitCsv(env.GITHUB_ALLOWED_WORKFLOWS),
    allowedEvents: splitCsv(env.GITHUB_ALLOWED_EVENTS),
  };
}

function bearerToken(request: Request): string | null {
  const header = request.headers.get('Authorization') ?? '';
  const match = /^Bearer\s+(.+)$/i.exec(header);
  return match?.[1] ?? null;
}

function stateStub(env: Env): DurableObjectStub<SchwabTokenState> {
  return env.SCHWAB_TOKEN_STATE.getByName('schwab-token-state');
}

async function authorizeGithubAction(request: Request, env: Env): Promise<void> {
  const token = bearerToken(request);
  if (!token) throw new Error('missing_bearer_token');
  await verifyGithubOidcJwt(token, policyFromEnv(env));
}

async function authorizeAdmin(request: Request, env: Env): Promise<void> {
  const expected = env.ADMIN_BOOTSTRAP_TOKEN;
  const actual = bearerToken(request);
  if (!expected || !actual || !timingSafeEqualString(actual, expected)) throw new Error('admin_denied');
}

export class SchwabTokenState extends DurableObject<Env> {
  private storageKey = 'schwab-token-record';

  async getAccessToken(): Promise<AccessTokenResult> {
    const record = await this.ctx.storage.get<TokenRecord>(this.storageKey);
    if (!record?.encryptedRefreshToken) throw new Error('refresh_token_not_bootstrapped');
    const now = Math.floor(Date.now() / 1000);
    const skew = accessTokenSkewSeconds(this.env);
    if (record.encryptedAccessToken && record.accessTokenExpiresAt && record.accessTokenExpiresAt - skew > now) {
      return {
        accessToken: await decryptText(record.encryptedAccessToken, requireEnv(this.env, 'TOKEN_ENCRYPTION_KEY')),
        expiresAt: new Date(record.accessTokenExpiresAt * 1000).toISOString(),
        refreshed: false,
      };
    }

    const refreshToken = await decryptText(record.encryptedRefreshToken, requireEnv(this.env, 'TOKEN_ENCRYPTION_KEY'));
    const pair = await refreshSchwabToken({
      clientId: requireEnv(this.env, 'SCHWAB_CLIENT_ID'),
      clientSecret: requireEnv(this.env, 'SCHWAB_CLIENT_SECRET'),
      refreshToken,
    });
    const expiresAt = now + pair.expiresIn;
    const nextRecord: TokenRecord = {
      encryptedAccessToken: await encryptText(pair.accessToken, requireEnv(this.env, 'TOKEN_ENCRYPTION_KEY')),
      encryptedRefreshToken: await encryptText(pair.refreshToken, requireEnv(this.env, 'TOKEN_ENCRYPTION_KEY')),
      accessTokenExpiresAt: expiresAt,
      updatedAt: new Date().toISOString(),
    };
    await this.ctx.storage.put(this.storageKey, nextRecord);
    return { accessToken: pair.accessToken, expiresAt: new Date(expiresAt * 1000).toISOString(), refreshed: true };
  }

  async bootstrapRefreshToken(refreshToken: string): Promise<{ ok: true; updatedAt: string }> {
    if (!refreshToken.trim()) throw new Error('empty_refresh_token');
    const existing = (await this.ctx.storage.get<TokenRecord>(this.storageKey)) ?? {};
    const updatedAt = new Date().toISOString();
    await this.ctx.storage.put(this.storageKey, {
      ...existing,
      encryptedRefreshToken: await encryptText(refreshToken, requireEnv(this.env, 'TOKEN_ENCRYPTION_KEY')),
      encryptedAccessToken: undefined,
      accessTokenExpiresAt: undefined,
      updatedAt,
    });
    return { ok: true, updatedAt };
  }

  async status(): Promise<{ bootstrapped: boolean; accessTokenExpiresAt?: string; updatedAt?: string }> {
    const record = await this.ctx.storage.get<TokenRecord>(this.storageKey);
    return {
      bootstrapped: Boolean(record?.encryptedRefreshToken),
      accessTokenExpiresAt: record?.accessTokenExpiresAt ? new Date(record.accessTokenExpiresAt * 1000).toISOString() : undefined,
      updatedAt: record?.updatedAt,
    };
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    try {
      if (request.method === 'GET' && url.pathname === '/health') return jsonResponse({ ok: true });

      if (request.method === 'POST' && url.pathname === '/v1/schwab/access-token') {
        await authorizeGithubAction(request, env);
        const result = await stateStub(env).getAccessToken();
        return jsonResponse({ access_token: result.accessToken, expires_at: result.expiresAt, refreshed: result.refreshed });
      }

      if (request.method === 'GET' && url.pathname === '/admin/status') {
        await authorizeAdmin(request, env);
        return jsonResponse(await stateStub(env).status());
      }

      if (request.method === 'POST' && url.pathname === '/admin/schwab/refresh-token') {
        await authorizeAdmin(request, env);
        const payload = (await request.json()) as { refresh_token?: string };
        return jsonResponse(await stateStub(env).bootstrapRefreshToken(String(payload.refresh_token ?? '')));
      }

      return errorResponse(404, 'not_found');
    } catch (error) {
      const message = error instanceof Error ? error.message : 'unknown_error';
      const authErrors = new Set([
        'missing_bearer_token',
        'malformed_jwt',
        'unsupported_jwt_header',
        'invalid_issuer',
        'invalid_audience',
        'token_expired',
        'token_not_yet_valid',
        'token_issued_in_future',
        'repository_denied',
        'ref_denied',
        'workflow_denied',
        'event_denied',
        'jwks_fetch_failed',
        'jwk_not_found',
        'jwt_signature_invalid',
        'admin_denied',
      ]);
      const status = authErrors.has(message) ? 401 : message === 'refresh_token_not_bootstrapped' ? 409 : 500;
      console.warn(JSON.stringify({ event: 'schwab_token_broker_error', code: message, path: url.pathname }));
      return errorResponse(status, message);
    }
  },
};
