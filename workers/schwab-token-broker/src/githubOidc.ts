import { base64UrlToBytes, bytesToArrayBuffer, parseJwtPart } from './encoding';

export const GITHUB_OIDC_ISSUER = 'https://token.actions.githubusercontent.com';
export const GITHUB_OIDC_JWKS_URL = `${GITHUB_OIDC_ISSUER}/.well-known/jwks`;

export type GithubOidcClaims = {
  iss?: string;
  aud?: string | string[];
  exp?: number;
  nbf?: number;
  iat?: number;
  repository?: string;
  ref?: string;
  workflow?: string;
  event_name?: string;
  run_id?: string;
  actor?: string;
  sub?: string;
};

type JwtHeader = {
  alg?: string;
  kid?: string;
  typ?: string;
};

type Jwk = JsonWebKey & { kid?: string; alg?: string };

type Jwks = { keys?: Jwk[] };

export type GithubOidcPolicy = {
  audience: string;
  allowedRepositories: string[];
  allowedRefs: string[];
  allowedWorkflows: string[];
  allowedEvents: string[];
  nowSeconds?: number;
};

export function splitCsv(value: string | undefined): string[] {
  return (value ?? '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

export function validateGithubClaims(claims: GithubOidcClaims, policy: GithubOidcPolicy): void {
  const now = policy.nowSeconds ?? Math.floor(Date.now() / 1000);
  if (claims.iss !== GITHUB_OIDC_ISSUER) throw new Error('invalid_issuer');
  const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!audiences.includes(policy.audience)) throw new Error('invalid_audience');
  if (typeof claims.exp !== 'number' || claims.exp <= now) throw new Error('token_expired');
  if (typeof claims.nbf === 'number' && claims.nbf > now + 30) throw new Error('token_not_yet_valid');
  if (typeof claims.iat === 'number' && claims.iat > now + 30) throw new Error('token_issued_in_future');
  if (!claims.repository || !policy.allowedRepositories.includes(claims.repository)) throw new Error('repository_denied');
  if (!claims.ref || !policy.allowedRefs.includes(claims.ref)) throw new Error('ref_denied');
  if (!claims.workflow || !policy.allowedWorkflows.includes(claims.workflow)) throw new Error('workflow_denied');
  if (!claims.event_name || !policy.allowedEvents.includes(claims.event_name)) throw new Error('event_denied');
}

export async function verifyGithubOidcJwt(jwt: string, policy: GithubOidcPolicy): Promise<GithubOidcClaims> {
  const parts = jwt.split('.');
  if (parts.length !== 3) throw new Error('malformed_jwt');
  const [headerPart, payloadPart, signaturePart] = parts;
  const header = parseJwtPart<JwtHeader>(headerPart);
  if (header.alg !== 'RS256' || !header.kid) throw new Error('unsupported_jwt_header');
  const claims = parseJwtPart<GithubOidcClaims>(payloadPart);
  validateGithubClaims(claims, policy);

  const jwksResponse = await fetch(GITHUB_OIDC_JWKS_URL, { headers: { Accept: 'application/json' } });
  if (!jwksResponse.ok) throw new Error('jwks_fetch_failed');
  const jwks = (await jwksResponse.json()) as Jwks;
  const jwk = jwks.keys?.find((key) => key.kid === header.kid);
  if (!jwk) throw new Error('jwk_not_found');
  const key = await crypto.subtle.importKey(
    'jwk',
    jwk,
    { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
    false,
    ['verify'],
  );
  const verified = await crypto.subtle.verify(
    'RSASSA-PKCS1-v1_5',
    key,
    bytesToArrayBuffer(base64UrlToBytes(signaturePart)),
    bytesToArrayBuffer(new TextEncoder().encode(`${headerPart}.${payloadPart}`)),
  );
  if (!verified) throw new Error('jwt_signature_invalid');
  return claims;
}
