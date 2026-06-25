import { describe, expect, it } from 'vitest';
import { decryptText, encryptText } from '../src/crypto';

describe('token encryption', () => {
  it('round-trips without storing plaintext', async () => {
    const ciphertext = await encryptText('refresh-token-value', '12345678901234567890123456789012');
    expect(ciphertext).not.toContain('refresh-token-value');
    await expect(decryptText(ciphertext, '12345678901234567890123456789012')).resolves.toBe('refresh-token-value');
  });

  it('requires an AES-sized key', async () => {
    await expect(encryptText('value', 'short')).rejects.toThrow('TOKEN_ENCRYPTION_KEY');
  });
});
