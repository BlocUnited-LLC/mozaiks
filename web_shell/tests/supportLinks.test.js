import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  buildSupportRequestPayload,
  getSupportApiBaseUrl,
  resolveSupportRequestScope,
} from '../../chat-ui/src/utils/supportLinks.js';

test('support payload keeps the subject app separate from execution identity', () => {
  assert.deepEqual(
    buildSupportRequestPayload({ message: 'help', appId: 'customer-app', userId: 'forged-user' }),
    { message: 'help', severity: 'low', subject_app_id: 'customer-app' },
  );
});

test('support API base handles same-origin and separate API hosts', () => {
  const previousWindow = globalThis.window;
  globalThis.window = { location: { origin: 'http://localhost:5173' } };
  try {
    assert.equal(getSupportApiBaseUrl({ getHttpBaseUrl: () => '' }), 'http://localhost:5173');
    assert.equal(
      getSupportApiBaseUrl({ getHttpBaseUrl: () => 'https://api.example/' }),
      'https://api.example',
    );
  } finally {
    globalThis.window = previousWindow;
  }
});

test('profile scope lookup sends the canonical access token', async () => {
  const previousFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ app_id: 'mozaiks-factory', user_id: 'user-1' }) };
  };
  try {
    const scope = await resolveSupportRequestScope({
      api: { getHttpBaseUrl: () => 'https://api.example' },
      auth: { getAccessToken: () => 'verified-token' },
      fallbackAppId: 'customer-app',
    });
    assert.equal(scope.appId, 'mozaiks-factory');
    assert.equal(calls[0].url, 'https://api.example/api/me');
    assert.equal(calls[0].options.headers.Authorization, 'Bearer verified-token');
  } finally {
    globalThis.fetch = previousFetch;
  }
});
