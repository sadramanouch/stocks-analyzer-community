import test from 'node:test';
import assert from 'node:assert/strict';
import { createSession, validSession, passwordMatches, localAccessAllowed, sameOrigin, MAX_AGE } from '../lib/session.ts';

test('signed sessions reject tampering, raw passwords, expiry and password rotation', async () => {
  process.env.APP_PASSWORD = 'unit-test-only';
  const now = Date.now();
  const token = await createSession(now);
  assert.equal(await validSession(token, now), true);
  assert.equal(token.includes(process.env.APP_PASSWORD), false);
  assert.notEqual(token, await createSession(now));
  for (const invalid of [undefined, 'unit-test-only', token + 'x', 'x' + token, 'x'.repeat(1025)]) {
    assert.equal(await validSession(invalid, now), false);
  }
  assert.equal(await validSession(token, now + MAX_AGE * 1000), false);
  process.env.APP_PASSWORD = 'rotated';
  assert.equal(await validSession(token, now), false);
});

test('password comparison validates types and unicode without leaking raw credentials', async () => {
  process.env.APP_PASSWORD = 'test-秘密';
  assert.equal(await passwordMatches('test-秘密'), true);
  for (const invalid of [null, {}, 'wrong', 'x'.repeat(4097)]) assert.equal(await passwordMatches(invalid), false);
});

test('missing authentication fails closed in production and Fly ignores local bypass', async () => {
  delete process.env.APP_PASSWORD;
  delete process.env.FLY_APP_NAME;
  delete process.env.ALLOW_UNAUTHENTICATED_LOCAL;
  process.env.NODE_ENV = 'production';
  assert.equal(await validSession(undefined), false);
  process.env.ALLOW_UNAUTHENTICATED_LOCAL = '1';
  assert.equal(localAccessAllowed(), true);
  process.env.FLY_APP_NAME = 'test';
  assert.equal(localAccessAllowed(), false);
  process.env.NODE_ENV = 'development';
  assert.equal(localAccessAllowed(), false);
});

test('cross-site requests and mismatched origins are rejected', () => {
  process.env.APP_ORIGIN = 'https://analyzer.example';
  const request = headers => new Request('http://internal/api/backend/api/test', { headers });
  assert.equal(sameOrigin(request({ origin: process.env.APP_ORIGIN })), true);
  assert.equal(sameOrigin(request({ origin: 'https://evil.example' })), false);
  assert.equal(sameOrigin(request({ 'sec-fetch-site': 'cross-site' })), false);
  assert.equal(sameOrigin(request({})), true);
  delete process.env.APP_ORIGIN;
  const local = headers => new Request('http://localhost:4317/api/test', { headers });
  assert.equal(sameOrigin(local({ host: '127.0.0.1:4317', origin: 'http://127.0.0.1:4317' })), true);
  assert.equal(sameOrigin(local({ host: '127.0.0.1:4317', origin: 'https://evil.example' })), false);
});

test('cookie-authenticated writes require the configured origin header', () => {
  process.env.APP_ORIGIN = 'https://analyzer.example';
  for (const method of ['POST','PUT','PATCH','DELETE']) {
    const request = headers => new Request('http://internal/api/backend/api/test', {method,headers});
    assert.equal(sameOrigin(request({})),false);
    assert.equal(sameOrigin(request({origin:'null'})),false);
    assert.equal(sameOrigin(request({origin:process.env.APP_ORIGIN})),true);
    assert.equal(sameOrigin(request({origin:process.env.APP_ORIGIN,'sec-fetch-site':'cross-site'})),false);
  }
  delete process.env.APP_ORIGIN;
});
