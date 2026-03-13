/**
 * Token store helpers.
 *
 * Call persistToken() after a successful login so the platform bridge can
 * read the access token synchronously on each request.
 */

import { storage } from '../platform/mmkvInstance';

const ACCESS_TOKEN_KEY = 'mozaiks_access_token';

export function persistToken(token: string): void {
  storage.set(ACCESS_TOKEN_KEY, token);
}

export function clearToken(): void {
  storage.delete(ACCESS_TOKEN_KEY);
}

export function getToken(): string | null {
  return storage.getString(ACCESS_TOKEN_KEY) ?? null;
}
