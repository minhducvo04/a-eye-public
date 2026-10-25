import test from 'node:test';
import assert from 'node:assert/strict';
import { accountAPI } from './account-api.js';

test('guest and Google use same-origin cookies and a nonce, never URL credentials', async () => {
  const calls = [];
  const api = accountAPI(async (path, options) => {
    calls.push({ path, options });
    return { ok: true, json: async () => ({ kind: 'guest' }) };
  });
  await api.signIn('guest', 'nonce');
  await api.signIn('google', 'nonce', 'synthetic-id-token');
  assert.deepEqual(calls.map(c => c.path), ['/auth/guest', '/auth/google']);
  assert.equal(calls[0].options.headers['X-Aeye-CSRF'], 'nonce');
  assert.equal(calls[1].options.credentials, 'same-origin');
  assert.deepEqual(JSON.parse(calls[1].options.body), { credential: 'synthetic-id-token' });
});

test('My data never accepts a client-selected owner', async () => {
  const calls = [];
  const api = accountAPI(async (path, options) => {
    calls.push({ path, options });
    return { ok: true, json: async () => ({}), blob: async () => 'zip' };
  });
  await api.me('other-owner');
  assert.equal(await api.export('other-owner'), 'zip');
  await api.delete('other-owner');
  assert.deepEqual(calls.map(c => c.path), ['/api/me', '/api/me/export', '/api/me']);
  assert.equal(calls[2].options.method, 'DELETE');
});

test('failed sign-in and expired sessions produce actionable errors', async () => {
  const api = accountAPI(async () => ({ ok: false, status: 401, json: async () => ({}) }));
  await assert.rejects(api.me(), /Sign in or choose Guest/);
  await assert.rejects(api.signIn('google', 'nonce', 'forged'), /Sign in or choose Guest/);
});
