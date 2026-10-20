import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import '../tests/typescript-loader.mjs';
const auth = existsSync(new URL('../src/hooks/use-auth-session.ts', import.meta.url)) ? await import('../src/hooks/use-auth-session.ts') : {};
const sessions = await import('../src/hooks/use-agent-session.ts');
const citations = await import('../src/components/citations/citation-records.ts');
const history = await import('../src/lib/agent-history.ts');
const thread = history;
const storage = await import('../src/mocks/storage.ts');
const App = (await import('../src/App.tsx')).default;
const ownerA = { id: '11111111-1111-4111-8111-111111111111', username: 'generated-a', displayName: 'Generated A', roles: ['member'] };
const ownerB = { id: '22222222-2222-4222-8222-222222222222', username: 'generated-b', displayName: 'Generated B', roles: ['member'] };
const id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const summary = { id, title: 'Generated conversation', updatedAt: '2026-10-17T00:00:00Z', archived: false, pendingApprovals: 0 };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const tick = () => new Promise(r => setImmediate(r));
class MemoryStorage {
  values = new Map(); writes = [];
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, value); this.writes.push([key, value]); }
  removeItem(key) { this.values.delete(key); }
}
function authSession() { assert.equal(typeof auth.AuthSession, 'function', 'authenticated session lifecycle is required'); return new auth.AuthSession(); }
function conversation(owner = ownerA) { assert.equal(typeof sessions.ConversationSession, 'function', 'owner-scoped cancellable conversation lifecycle is required'); return new sessions.ConversationSession(owner.id); }
function threadSession(callbacks) { assert.equal(typeof thread.createThreadSession, 'function', 'thread lifecycle must distinguish auth and cancellation'); return thread.createThreadSession(id, callbacks); }

test('startup renders the authentication gate before any private thread', () => {
  const html = renderToStaticMarkup(React.createElement(App));
  assert.match(html, /Checking sign-in/);
  assert.doesNotMatch(html, /Session ID|Reset session|Outputs/);
});
test('signed-out startup sends only me and exposes no authenticated identity', async () => {
  const calls = []; globalThis.fetch = async (url, options) => { calls.push([url, options.credentials]); return json({ code: 'not_signed_in', message: 'generated', requestId: 'generated' }, 401); };
  const state = authSession(); await state.start();
  assert.deepEqual(calls, [['/api/me', 'same-origin']]); assert.equal(state.getSnapshot().me, null); assert.equal(state.getSnapshot().status, 'signed-out');
});
test('login accepts only the complete existing Me contract and stores no password', async () => {
  const state = authSession(); globalThis.fetch = async (url, options) => { assert.equal(url, '/api/session'); assert.equal(options.credentials, 'same-origin'); assert.deepEqual(JSON.parse(options.body), { username: 'generated-a', password: 'generated-password' }); return json(ownerA); };
  await state.signIn({ username: 'generated-a', password: 'generated-password' });
  assert.deepEqual(state.getSnapshot().me, ownerA); assert.equal(JSON.stringify(state.getSnapshot()).includes('generated-password'), false);
  globalThis.fetch = async () => json({ id: ownerA.id }); await state.signIn({ username: 'generated-a', password: 'generated-password' });
  assert.equal(state.getSnapshot().me, null); assert.match(state.getSnapshot().error, /invalid/i);
});
for (const status of [401, 403, 429]) test(`login ${status} refuses private identity with a safe explicit error`, async () => {
  const state = authSession(); globalThis.fetch = async () => json({ code: 'generated', message: 'SECRET ERROR BODY', requestId: 'generated' }, status);
  await state.signIn({ username: 'generated-a', password: 'generated-password' });
  assert.equal(state.getSnapshot().me, null); assert.ok(state.getSnapshot().error); assert.doesNotMatch(state.getSnapshot().error, /SECRET/);
});
test('late me cannot reattach after logout', async () => {
  const waiting = deferred(); const state = authSession(); globalThis.fetch = async (url) => url === '/api/me' ? waiting.promise : new Response(null, { status: 204 });
  const checking = state.start(); await state.signOut(); waiting.resolve(json(ownerA)); await checking;
  assert.equal(state.getSnapshot().me, null); assert.equal(state.getSnapshot().status, 'signed-out');
});
test('logout serializes after in-flight login and immediately removes private identity', async () => {
  const waiting = deferred(), calls = []; const state = authSession();
  globalThis.fetch = async (url, options) => { calls.push(options.method); return options.method === 'POST' ? waiting.promise : new Response(null, { status: 204 }); };
  const login = state.signIn({ username: 'generated-a', password: 'generated-password' }); await tick(); const logout = state.signOut();
  assert.equal(state.getSnapshot().me, null); assert.equal(state.getSnapshot().status, 'signing-out'); assert.deepEqual(calls, ['POST']);
  waiting.resolve(json(ownerA)); await Promise.all([login, logout]); assert.deepEqual(calls, ['POST', 'DELETE']); assert.equal(state.getSnapshot().me, null);
});
test('logout transport failure clears private UI but stays unresolved until a successful 204', async () => {
  const state = authSession(); globalThis.fetch = async () => json(ownerA); await state.start();
  globalThis.fetch = async () => { throw new Error('generated transport failure'); }; await state.signOut();
  assert.equal(state.getSnapshot().me, null); assert.equal(state.getSnapshot().status, 'logout-failed'); assert.match(state.getSnapshot().error, /not confirmed/i);
  globalThis.fetch = async () => json({}, 200); await state.signOut(); assert.equal(state.getSnapshot().status, 'logout-failed');
  globalThis.fetch = async () => new Response(null, { status: 204 }); await state.signOut(); assert.equal(state.getSnapshot().status, 'signed-out');
});
test('owner reload verifies the pointer with owned GET before displaying it', async () => {
  const store = new MemoryStorage(); globalThis.sessionStorage = store; store.setItem(`craft-conversation:${ownerA.id}`, id); store.writes = [];
  const calls = []; globalThis.fetch = async (url, options) => { calls.push([url, options?.method ?? 'GET']); return json({ ...summary, messages: [] }); };
  const state = conversation(); await state.start(); assert.equal(state.getSnapshot().sessionID, id); assert.deepEqual(calls, [[`/api/conversations/${id}`, 'GET']]); assert.deepEqual(store.writes, []);
});
test('different owner never fetches the previous owner pointer', async () => {
  globalThis.sessionStorage = new MemoryStorage(); sessionStorage.setItem(`craft-conversation:${ownerA.id}`, id);
  const calls = []; globalThis.fetch = async (url) => { calls.push(url); return json(summary); };
  const state = conversation(ownerB); await state.start(); assert.deepEqual(calls, ['/api/conversations']); assert.equal(sessionStorage.getItem(`craft-conversation:${ownerB.id}`), id);
});
for (const kind of ['archived', '404']) test(`reload discards ${kind} owned pointer before creating a fresh conversation`, async () => {
  globalThis.sessionStorage = new MemoryStorage(); sessionStorage.setItem(`craft-conversation:${ownerA.id}`, id);
  const calls = []; globalThis.fetch = async (url) => { calls.push(url); return calls.length === 1 ? (kind === '404' ? json({}, 404) : json({ ...summary, archived: true, messages: [] })) : json(summary); };
  const state = conversation(); await state.start(); assert.deepEqual(calls, [`/api/conversations/${id}`, '/api/conversations']); assert.equal(state.getSnapshot().sessionID, id);
});
test('pointer 401 invalidates auth instead of creating a new conversation', async () => {
  globalThis.sessionStorage = new MemoryStorage(); sessionStorage.setItem(`craft-conversation:${ownerA.id}`, id);
  const calls = []; globalThis.fetch = async (url) => { calls.push(url); return json({}, 401); };
  const state = conversation(); let expired = false; state.onAuthInvalid = () => { expired = true; }; await state.start();
  assert.equal(expired, true); assert.equal(state.getSnapshot().sessionID, ''); assert.equal(calls.length, 1);
});
test('dispose refuses a late conversation create and all pointer writes', async () => {
  globalThis.sessionStorage = new MemoryStorage(); const waiting = deferred(); globalThis.fetch = async () => waiting.promise;
  const state = conversation(); const initializing = state.start(); state.dispose(); waiting.resolve(json(summary)); await initializing;
  assert.equal(state.getSnapshot().sessionID, ''); assert.deepEqual(sessionStorage.writes, []);
});
test('StrictMode cleanup and restart exposes one current conversation and aborts old requests', async () => {
  globalThis.sessionStorage = new MemoryStorage(); const waiting = deferred(); const signals = []; let n = 0;
  globalThis.fetch = async (_url, options) => { signals.push(options.signal); return ++n === 1 ? waiting.promise : json(summary); };
  const state = conversation(); const old = state.start(); state.dispose(); await state.start(); waiting.resolve(json({ ...summary, id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' })); await old;
  assert.equal(signals[0].aborted, true); assert.equal(state.getSnapshot().sessionID, id); assert.deepEqual(sessionStorage.writes, [[`craft-conversation:${ownerA.id}`, id]]);
});
test('reset archive failure stays explicit and creates no replacement', async () => {
  globalThis.sessionStorage = new MemoryStorage(); const state = conversation(); globalThis.fetch = async () => json(summary); await state.start();
  const calls = []; globalThis.fetch = async (url) => { calls.push(url); return json({}, 403); }; await state.reset();
  assert.deepEqual(calls, [`/api/conversations/${id}`]); assert.ok(state.getSnapshot().error); assert.equal(state.getSnapshot().sessionID, id);
});
test('reset racing dispose cannot create a replacement after archive settles', async () => {
  globalThis.sessionStorage = new MemoryStorage(); const state = conversation(); globalThis.fetch = async () => json(summary); await state.start();
  const waiting = deferred(), calls = []; globalThis.fetch = async (url) => { calls.push(url); return waiting.promise; }; const resetting = state.reset(); state.dispose(); waiting.resolve(json({ ...summary, archived: true })); await resetting;
  assert.deepEqual(calls, [`/api/conversations/${id}`]); assert.equal(sessionStorage.writes.length, 1);
});
test('history distinguishes authentication from missing conversation', async () => {
  globalThis.fetch = async () => json({}, 401); await assert.rejects(history.createSessionHistory(id).load(), error => error.name === 'AuthenticationRequiredError');
  globalThis.fetch = async () => json({}, 404); await assert.rejects(history.createSessionHistory(id).load(), error => error instanceof history.SessionNotFoundError);
});
test('late history is ignored after its signal is aborted', async () => {
  const waiting = deferred(), controller = new AbortController(); globalThis.fetch = async () => waiting.promise;
  const loading = history.createSessionHistory(id, controller.signal).load(); controller.abort(); waiting.resolve(json({ ...summary, messages: [] })); await assert.rejects(loading, error => error.name === 'AbortError');
});
test('pending citation cannot repopulate the next owner cache', async () => {
  assert.equal(typeof citations.clearCitationCache, 'function'); assert.equal(typeof citations.loadCitation, 'function'); assert.equal(typeof citations.currentStates, 'function');
  const waiting = deferred(); let n = 0; globalThis.fetch = async () => ++n === 1 ? waiting.promise : json({ title: 'Generated B', subtitle: 'Generated', previewURL: '/generated', mediaType: 'text/plain' });
  const old = citations.loadCitation('/api/citations/generated'); citations.clearCitationCache(); await citations.loadCitation('/api/citations/generated'); waiting.resolve(json({ title: 'Generated A', subtitle: 'Generated', previewURL: '/generated', mediaType: 'text/plain' })); await old;
  assert.equal(citations.currentStates(['/api/citations/generated']).get('/api/citations/generated').record.title, 'Generated B'); citations.clearCitationCache();
});
test('mock answers remain in memory and are never persisted', () => {
  globalThis.sessionStorage = new MemoryStorage(); sessionStorage.setItem('craft-mock-sessions', 'legacy-generated'); sessionStorage.writes = [];
  storage.saveSessions({ [id]: { ...summary, messages: [{ id: 'generated-message', role: 'assistant', content: 'generated-answer' }] } });
  assert.equal(storage.loadSessions()[id].messages[0].content, 'generated-answer'); assert.deepEqual(sessionStorage.writes, []); assert.equal(sessionStorage.getItem('craft-mock-sessions'), null);
});
test('run HTTP 401 invalidates authentication without renewing conversation', async () => {
  let expired = 0, renewed = 0; const state = threadSession({ onAuthInvalid: () => expired++, onSessionInvalid: () => renewed++, onError: () => {} });
  globalThis.fetch = async () => json({ code: 'not_signed_in', message: 'generated', requestId: 'generated' }, 401);
  await state.agent.runAgent().catch(() => {}); assert.equal(expired, 1); assert.equal(renewed, 0); state.dispose();
});
test('streamed RUN_ERROR auth failure invalidates identity and does not renew', async () => {
  let expired = 0, renewed = 0; const state = threadSession({ onAuthInvalid: () => expired++, onSessionInvalid: () => renewed++, onError: () => {} });
  globalThis.fetch = async () => new Response(`data: ${JSON.stringify({ type: 'RUN_STARTED', threadId: id, runId: id })}\n\ndata: ${JSON.stringify({ type: 'RUN_ERROR', code: 'not_signed_in', message: 'generated' })}\n\n`, { headers: { 'content-type': 'text/event-stream' } });
  await state.agent.runAgent().catch(() => {}); assert.equal(expired, 1); assert.equal(renewed, 0); state.dispose();
});
test('thread disposal aborts active SSE and refuses stale callback attachment', async () => {
  let expired = 0; const state = threadSession({ onAuthInvalid: () => expired++, onSessionInvalid: () => {}, onError: () => {} }); const waiting = deferred(); let signal;
  globalThis.fetch = async (_url, options) => { signal = options.signal; return waiting.promise; };
  const run = state.agent.runAgent().catch(() => {}); await tick(); state.dispose(); assert.equal(signal.aborted, true); waiting.resolve(json({}, 401)); await run; assert.equal(expired, 0);
});
test('proxy routes all API surfaces to the configured loopback without cookie or Origin rewrite', async () => {
  const resolveConfig = async suffix => { const declared = (await import('../vite.config.ts?' + suffix)).default; return typeof declared === 'function' ? await declared({ command: 'serve', mode: 'development', isSsrBuild: false, isPreview: false }) : declared; };
  process.env.CRAFT_BACKEND_URL = 'http://127.0.0.1:53510'; const config = await resolveConfig('valid');
  assert.deepEqual(Object.keys(config.server.proxy).sort(), ['/api', '/health', '/mcp']);
  for (const value of Object.values(config.server.proxy)) { assert.equal(value.target ?? value, 'http://127.0.0.1:53510'); if (typeof value === 'object') { assert.equal(value.changeOrigin, false); assert.equal(value.cookieDomainRewrite, undefined); assert.equal(value.cookiePathRewrite, undefined); assert.equal(value.headers, undefined); } }
  process.env.CRAFT_BACKEND_URL = 'http://example.com:53510'; await assert.rejects(resolveConfig('external'), /loopback/i);
});
test('SDK run failure rejects with status while logs and errors omit response body', async () => {
  const output = []; const original = console.error; console.error = (...args) => output.push(args.map(String).join(' '));
  const state = threadSession({ onAuthInvalid: () => {}, onSessionInvalid: () => {}, onError: () => {} });
  try {
    globalThis.fetch = async () => json({ code: 'generated', message: 'GENERATED_PRIVATE_BODY', requestId: 'generated' }, 400);
    await assert.rejects(state.agent.runAgent(), error => error.status === 400 && !String(error).includes('GENERATED_PRIVATE_BODY'));
    assert.equal(output.some(line => line.includes('GENERATED_PRIVATE_BODY')), false);
  } finally { console.error = original; state.dispose(); }
});
test('StrictMode effect replay defers create until the surviving mount', async () => {
  globalThis.sessionStorage = new MemoryStorage(); let requests = 0; globalThis.fetch = async () => { requests++; return json(summary); };
  const state = conversation(); assert.equal(typeof state.mount, 'function', 'hook lifecycle must defer create through the StrictMode replay');
  const cleanupFirst = state.mount(); cleanupFirst(); const cleanupSecond = state.mount(); await tick();
  assert.equal(requests, 1); assert.equal(state.getSnapshot().sessionID, id); cleanupSecond();
});
test('successful archive followed by create failure never restores the archived thread', async () => {
  globalThis.sessionStorage = new MemoryStorage(); const state = conversation(); globalThis.fetch = async () => json(summary); await state.start();
  globalThis.fetch = async (_url, options) => options.method === 'PATCH' ? json({ ...summary, archived: true }) : json({}, 500);
  await state.reset(); assert.equal(state.getSnapshot().sessionID, ''); assert.ok(state.getSnapshot().error); assert.equal(sessionStorage.getItem(`craft-conversation:${ownerA.id}`), null);
});
test('thread disposal clears citation cache and refuses its pending completion', async () => {
  citations.clearCitationCache(); const waiting = deferred(); globalThis.fetch = async () => waiting.promise;
  const state = threadSession({ onAuthInvalid: () => {}, onSessionInvalid: () => {}, onError: () => {} }); const lookup = citations.loadCitation('/api/citations/generated');
  state.dispose(); waiting.resolve(json({ title: 'Generated private citation', subtitle: 'Generated', previewURL: '/generated', mediaType: 'text/plain' })); await lookup;
  assert.equal(citations.currentStates(['/api/citations/generated']).get('/api/citations/generated').status, 'loading');
});
test('signed-out authentication removes legacy candidate body and ownerless ID storage', async () => {
  globalThis.sessionStorage = new MemoryStorage(); sessionStorage.setItem('craft-mock-sessions', 'generated-legacy-body'); sessionStorage.setItem('pi-agent-session-id', id); sessionStorage.setItem('unrelated-setting', 'generated-setting');
  const state = authSession(); globalThis.fetch = async () => json({}, 401); await state.start();
  assert.equal(sessionStorage.getItem('craft-mock-sessions'), null); assert.equal(sessionStorage.getItem('pi-agent-session-id'), null); assert.equal(sessionStorage.getItem('unrelated-setting'), 'generated-setting');
});
