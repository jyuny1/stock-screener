import { describe, expect, it } from 'vitest';
import { GITHUB_OIDC_ISSUER, splitCsv, validateGithubClaims } from '../src/githubOidc';

describe('GitHub OIDC policy validation', () => {
  const now = 1_800_000_000;
  const policy = {
    audience: 'schwab-token-broker',
    allowedRepositories: ['jyuny1/stock-screener'],
    allowedRefs: ['refs/heads/main'],
    allowedWorkflows: ['SOXL Price D1 Import'],
    allowedEvents: ['schedule', 'workflow_dispatch'],
    nowSeconds: now,
  };

  const claims = {
    iss: GITHUB_OIDC_ISSUER,
    aud: 'schwab-token-broker',
    exp: now + 300,
    nbf: now - 30,
    iat: now - 30,
    repository: 'jyuny1/stock-screener',
    ref: 'refs/heads/main',
    workflow: 'SOXL Price D1 Import',
    event_name: 'schedule',
  };

  it('accepts the allowed SOXL workflow identity', () => {
    expect(() => validateGithubClaims(claims, policy)).not.toThrow();
  });

  it('rejects another repository', () => {
    expect(() => validateGithubClaims({ ...claims, repository: 'attacker/repo' }, policy)).toThrow('repository_denied');
  });

  it('rejects another branch', () => {
    expect(() => validateGithubClaims({ ...claims, ref: 'refs/heads/develop' }, policy)).toThrow('ref_denied');
  });

  it('parses comma-separated allowlists', () => {
    expect(splitCsv('SOXL Price D1 Import, Static Site,,')).toEqual(['SOXL Price D1 Import', 'Static Site']);
  });
});
