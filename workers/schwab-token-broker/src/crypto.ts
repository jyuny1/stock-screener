import { base64ToBytes, bytesToArrayBuffer, bytesToBase64, textDecoder, textEncoder } from './encoding';

async function importAesKey(secret: string): Promise<CryptoKey> {
  const raw = textEncoder.encode(secret);
  if (![16, 24, 32].includes(raw.length)) {
    throw new Error('TOKEN_ENCRYPTION_KEY must be 16, 24, or 32 bytes');
  }
  return crypto.subtle.importKey('raw', raw, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
}

export async function encryptText(plaintext: string, secret: string): Promise<string> {
  const key = await importAesKey(secret);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, bytesToArrayBuffer(textEncoder.encode(plaintext)));
  const combined = new Uint8Array(iv.length + encrypted.byteLength);
  combined.set(iv, 0);
  combined.set(new Uint8Array(encrypted), iv.length);
  return bytesToBase64(combined);
}

export async function decryptText(ciphertext: string, secret: string): Promise<string> {
  const key = await importAesKey(secret);
  const combined = base64ToBytes(ciphertext);
  if (combined.length <= 12) throw new Error('invalid_ciphertext');
  const iv = combined.slice(0, 12);
  const body = combined.slice(12);
  const decrypted = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, bytesToArrayBuffer(body));
  return textDecoder.decode(decrypted);
}
