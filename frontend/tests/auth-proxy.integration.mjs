// Run after npm run build. Starts isolated servers with dummy credentials only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';

async function startApp(extra) {
  const reserve = createServer();
  reserve.listen(0, '127.0.0.1'); await once(reserve, 'listening');
  const port = reserve.address().port;
  await new Promise(resolve => reserve.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const env = { ...process.env, NODE_ENV: 'production', HOSTNAME: '127.0.0.1', PORT: String(port),
    APP_PASSWORD: '', API_SECRET: '', FLY_APP_NAME: '', ALLOW_UNAUTHENTICATED_LOCAL: '', APP_ORIGIN: base, ...extra };
  const child = spawn(process.execPath, ['.next/standalone/server.js'], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '';
  child.stdout.on('data', data => output = (output + data).slice(-2000));
  child.stderr.on('data', data => output = (output + data).slice(-2000));
  for (let i = 0; i < 100; i++) {
    try { await fetch(`${base}/login`); return { base, stop: async () => { child.kill(); await once(child, 'exit'); } }; }
    catch { if (child.exitCode !== null) throw new Error(output); await delay(100); }
  }
  child.kill(); throw new Error(`App failed to start: ${output}`);
}

test('built application: login, server-only credentials, proxy methods and failure paths', async t => {
  const calls = [];
  const backend = createServer(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    calls.push({ url: req.url, method: req.method, headers: req.headers, body });
    if (req.url === '/api/redirect') { res.writeHead(302, { Location: 'https://example.invalid' }); res.end(); return; }
    if (req.url === '/api/broken') { req.socket.destroy(); return; }
    if (req.url === '/api/stream') { res.writeHead(200, { 'Content-Type': 'text/event-stream' }); res.write('data: one\n\n'); await delay(30); res.end('data: two\n\n'); return; }
    res.writeHead(req.url === '/api/missing' ? 404 : 200, { 'Content-Type': 'application/json', 'Set-Cookie': 'unsafe=upstream', 'X-Content-SHA256': req.url === '/api/bad-hash' ? 'invalid-hash' : 'a'.repeat(64) });
    res.end(JSON.stringify({ ok: true }));
  });
  backend.listen(0, '127.0.0.1'); await once(backend, 'listening');
  t.after(() => new Promise(resolve => { backend.closeAllConnections(); backend.close(resolve); }));
  const app = await startApp({ APP_PASSWORD: 'integration-only-password', API_SECRET: 'integration-only-backend-key', API_URL: `http://127.0.0.1:${backend.address().port}` });
  t.after(app.stop);
  const request = (path, options = {}) => fetch(app.base + path, { redirect: 'manual', ...options });
  assert.equal((await request('/')).status, 307);
  assert.equal((await request('/system')).status, 307);
  assert.equal((await request('/api/backend/api/test')).status, 401);
  assert.equal((await request('/api/backend/api/test', { headers: { Cookie: 'sa_session=integration-only-password' } })).status, 401);
  const login = body => request('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: app.base }, body });
  assert.equal((await login('{')).status, 400);
  assert.equal((await login(JSON.stringify({ password: 'wrong' }))).status, 401);
  assert.equal((await login('x'.repeat(9000))).status, 413);
  const signed = await login(JSON.stringify({ password: 'integration-only-password' }));
  assert.equal(signed.status, 200);
  const setCookie = signed.headers.get('set-cookie');
  assert.match(setCookie, /HttpOnly/i); assert.match(setCookie, /SameSite=strict/i);
  assert.equal(setCookie.includes('integration-only-password'), false);
  const cookie = setCookie.split(';')[0];
  const headers = { Cookie: `${cookie}; unrelated=secret`, 'X-API-Key': 'client-injected-key', Origin: app.base };
  assert.equal((await request('/', { headers })).status, 200);
  const systemPage = await request('/system', { headers });
  assert.equal(systemPage.status, 200);
  const systemHtml = await systemPage.text();
  assert.match(systemHtml, /System status/);
  assert.equal(systemHtml.includes('integration-only-backend-key'), false);
  for (const path of ['/invest','/research','/portfolio','/trade-evidence','/broker']) {
    assert.equal((await request(path)).status,307,`${path} must require a session`);
    const page=await request(path,{headers});
    assert.equal(page.status,200,path);
    const html=await page.text();
    assert.equal(html.includes('integration-only-backend-key'),false,path);
    assert.equal(html.includes('integration-only-password'),false,path);
  }
  const forwarded = await request('/api/backend/api/test?api_key=client-query-key&keep=1', { headers });
  assert.equal(forwarded.status, 200); assert.equal(forwarded.headers.get('set-cookie'), null);
  assert.equal(forwarded.headers.get('x-content-sha256'), 'a'.repeat(64));
  const observed = calls.at(-1);
  assert.equal(observed.url, '/api/test?keep=1');
  assert.equal(observed.headers['x-api-key'], 'integration-only-backend-key');
  assert.equal(observed.headers.cookie, undefined);
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    const before = calls.length;
    assert.equal((await request('/api/backend/api/test', { method, headers: { Cookie: cookie, 'Content-Type':'application/json' }, body: '{"test":1}' })).status,403);
    assert.equal(calls.length,before,'Origin-less writes must not reach the backend');
    assert.equal((await request('/api/backend/api/test', { method, headers: { ...headers, 'Content-Type': 'application/json' }, body: '{"test":1}' })).status, 200);
    assert.equal(calls.at(-1).method, method); assert.equal(calls.at(-1).body, '{"test":1}');
  }
  assert.equal((await request('/api/backend/api/test', { method: 'HEAD', headers })).status, 200);
  assert.equal((await request('/api/backend/api/test', { headers: { ...headers, Origin: 'https://evil.example' } })).status, 403);
  assert.equal((await request('/api/backend/health', { headers })).status, 400);
  assert.equal((await request('/api/backend/api/missing', { headers })).status, 404);
  assert.equal((await request('/api/backend/api/redirect', { headers })).status, 502);
  assert.equal((await request('/api/backend/api/broken', { headers })).status, 502);
  assert.equal((await request('/api/backend/api/test', { method: 'POST', headers, body: 'x'.repeat(1024 * 1024 + 1) })).status, 413);
  const stream = await request('/api/backend/api/stream', { headers });
  assert.match(stream.headers.get('content-type'), /event-stream/);
  assert.equal(await stream.text(), 'data: one\n\ndata: two\n\n');
  assert.equal((await request('/api/backend/api/bad-hash', { headers })).headers.get('x-content-sha256'), null);
  for (let i = 0; i < 10; i++) assert.equal((await login('{"password":"wrong"}')).status, 401);
  assert.equal((await login('{"password":"wrong"}')).status, 429);
});

test('built production app fails closed without configuration', async t => {
  const app = await startApp({}); t.after(app.stop);
  assert.equal((await fetch(app.base + '/')).status, 503);
  assert.equal((await fetch(app.base + '/api/backend/api/test')).status, 503);
  assert.equal((await fetch(app.base + '/api/auth/login', { method: 'POST', headers:{Origin:app.base}, body: '{}' })).status, 503);
});

test('local preview accepts its browser origin without a configured public origin', async t => {
  const backend = createServer((req, res) => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end('{"ok":true}'); });
  backend.listen(0, '127.0.0.1'); await once(backend, 'listening');
  t.after(() => new Promise(resolve => { backend.closeAllConnections(); backend.close(resolve); }));
  const app = await startApp({ APP_ORIGIN: '', ALLOW_UNAUTHENTICATED_LOCAL: '1', API_URL: `http://127.0.0.1:${backend.address().port}` });
  t.after(app.stop);
  const response = await fetch(app.base + '/api/backend/api/portfolio/learn', { method: 'POST', headers: { Origin: app.base } });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true });
});
