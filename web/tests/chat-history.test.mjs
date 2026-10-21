import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import './typescript-loader.mjs';
const { ConversationSession } = await import('../src/hooks/use-agent-session.ts');
const App = (await import('../src/App.tsx')).default;
const moduleURL = new URL('../src/components/chat-history.tsx', import.meta.url);
const ui = existsSync(moduleURL) ? await import(moduleURL) : {};
const owner = '11111111-1111-4111-8111-111111111111';
const otherOwner = '22222222-2222-4222-8222-222222222222';
const A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const C = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const summary = (id = A, extra = {}) => ({ id, title: 'Generated chat', updatedAt: '2026-10-17T00:00:00Z', archived: false, pendingApprovals: 0, ...extra });
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const deferred = () => { let resolve, reject; const promise = new Promise((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; };
class MemoryStorage {
  values = new Map(); writes = [];
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, value); this.writes.push([key, value]); }
  removeItem(key) { this.values.delete(key); }
}
const pointer = `craft-conversation:${owner}`;
async function ready() {
  globalThis.sessionStorage = new MemoryStorage();
  globalThis.fetch = async () => json(summary());
  const state = new ConversationSession(owner); await state.start();
  return state;
}
function requireMethod(state, name) { assert.equal(typeof state[name], 'function', `${name} must implement owned chat navigation`); }
function renderHistory(props = {}) {
  assert.equal(typeof ui.ChatHistory, 'function', 'owned ChatHistory component must render the real picker');
  return renderToStaticMarkup(React.createElement(ui.ChatHistory, { sessionID: A, history: [], pending: false, historyPending: false, historyLimited: false, onNewChat() {}, onSelectChat() {}, onRefreshHistory() {}, onReset() {}, ...props }));
}

test('signed-out App never exposes New chat or owned history controls', () => {
  const html = renderToStaticMarkup(React.createElement(App));
  assert.match(html, /Checking sign-in/); assert.doesNotMatch(html, /New chat|Chat history|Archive and reset/);
});
test('New chat creates a distinct owned conversation without archiving previous chat', async () => {
  const state = await ready(); requireMethod(state, 'newChat');
  const calls = []; globalThis.fetch = async (url, options) => { calls.push([url, options.method, options.credentials, options.body]); return json(summary(B), 201); };
  await state.newChat(); assert.equal(state.getSnapshot().sessionID, B);
  assert.deepEqual(calls, [['/api/conversations', 'POST', 'same-origin', '{}']]);
  assert.equal(sessionStorage.getItem(pointer), B);
});
test('New chat immediately clears active ID so old keyed thread can unmount', async () => {
  const state = await ready(); requireMethod(state, 'newChat');
  const waiting = deferred(); globalThis.fetch = async () => waiting.promise;
  const creating = state.newChat(); assert.equal(state.getSnapshot().sessionID, ''); assert.equal(state.getSnapshot().pending, true);
  waiting.resolve(json(summary(B))); await creating; assert.equal(state.getSnapshot().sessionID, B);
});
test('duplicate pending New chat does not create an orphan conversation', async () => {
  const state = await ready(); requireMethod(state, 'newChat');
  const waiting = deferred(), calls = []; globalThis.fetch = async (url, options) => { calls.push([url, options.method]); return waiting.promise; };
  const creating = state.newChat(); await state.newChat(); waiting.resolve(json(summary(B))); await creating;
  assert.deepEqual(calls, [['/api/conversations', 'POST']]);
});
for (const outcome of ['403', 'transport']) test(`New chat ${outcome} failure restores only previous verified owned chat and pointer`, async () => {
  const state = await ready(); requireMethod(state, 'newChat');
  globalThis.fetch = async () => { if (outcome === 'transport') throw new Error('GENERATED_PRIVATE_TRANSPORT'); return json({ message: 'GENERATED_PRIVATE_BODY' }, 403); };
  await state.newChat(); assert.equal(state.getSnapshot().sessionID, A); assert.equal(sessionStorage.getItem(pointer), A);
  assert.ok(state.getSnapshot().error); assert.doesNotMatch(state.getSnapshot().error, /GENERATED_PRIVATE/);
});
test('owned history GET filters archived chats and orders newest first without changing active pointer', async () => {
  const state = await ready(); requireMethod(state, 'refreshHistory');
  const calls = []; globalThis.fetch = async (url, options) => { calls.push([url, options.method ?? 'GET', options.credentials]); return json([summary(A), summary(B, { updatedAt: '2026-10-18T00:00:00Z' }), summary(C, { archived: true })]); };
  await state.refreshHistory(); assert.deepEqual(state.getSnapshot().history.map(chat => chat.id), [B, A]);
  assert.deepEqual(calls, [['/api/conversations', 'GET', 'same-origin']]);
  assert.equal(state.getSnapshot().sessionID, A); assert.equal(sessionStorage.getItem(pointer), A);
  assert.equal(sessionStorage.writes.every(([, value]) => /^[0-9a-f-]{36}$/i.test(value)), true);
});
test('owned history shows newest hundred and records visible limit', async () => {
  const state = await ready(); requireMethod(state, 'refreshHistory');
  const entries = Array.from({ length: 101 }, (_, index) => summary(`${index.toString(16).padStart(8, '0')}-aaaa-4aaa-8aaa-aaaaaaaaaaaa`, { updatedAt: new Date(Date.UTC(2026, 0, 1, 0, 0, index)).toISOString() }));
  globalThis.fetch = async () => json(entries); await state.refreshHistory();
  assert.equal(state.getSnapshot().history.length, 100); assert.equal(state.getSnapshot().history[0].id, '00000064-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
  assert.equal(state.getSnapshot().history.at(-1).id, '00000001-aaaa-4aaa-8aaa-aaaaaaaaaaaa'); assert.equal(state.getSnapshot().historyLimited, true);
});
for (const [label, returned, archived, visible, limited, first, last] of [
  ['raw 100 chats', 100, 0, 100, true, '00000063-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '00000000-aaaa-4aaa-8aaa-aaaaaaaaaaaa'],
  ['raw 99 chats', 99, 0, 99, false, '00000062-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '00000000-aaaa-4aaa-8aaa-aaaaaaaaaaaa'],
  ['raw 100 chats including archived rows', 100, 20, 80, true, '00000063-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '00000014-aaaa-4aaa-8aaa-aaaaaaaaaaaa'],
]) test(`owned history ${label} determines the honest limit notice before archive filtering`, async () => {
  const state = await ready();
  const entries = Array.from({ length: returned }, (_, index) => summary(`${index.toString(16).padStart(8, '0')}-aaaa-4aaa-8aaa-aaaaaaaaaaaa`, {
    archived: index < archived,
    updatedAt: new Date(Date.UTC(2026, 0, 1, 0, 0, index)).toISOString(),
  }));
  globalThis.fetch = async () => json(entries); await state.refreshHistory();
  const snapshot = state.getSnapshot();
  assert.equal(snapshot.history.length, visible); assert.equal(snapshot.history[0].id, first); assert.equal(snapshot.history.at(-1).id, last);
  assert.equal(snapshot.historyLimited, limited);
  assert.equal(snapshot.sessionID, A); assert.equal(sessionStorage.getItem(pointer), A);
  const html = renderHistory(snapshot);
  if (limited) assert.match(html, /Chat history is limited to 100 entries\./);
  else assert.doesNotMatch(html, /Chat history is limited/);
  assert.doesNotMatch(html, /newest 100|more chats|total chats/i);
});
for (const [label, extra] of [['bad UUID', { id: 'foreign' }], ['overlong title', { title: 'x'.repeat(201) }], ['invalid time', { updatedAt: 'not-a-date' }], ['wrong archive flag', { archived: 'false' }], ['negative approvals', { pendingApprovals: -1 }], ['fractional approvals', { pendingApprovals: 0.5 }], ['unsafe approvals', { pendingApprovals: 9007199254740992 }]]) test(`owned history rejects ${label} metadata without showing response body`, async () => {
  const state = await ready(); requireMethod(state, 'refreshHistory');
  globalThis.fetch = async () => json([summary(A, extra)]); await state.refreshHistory();
  assert.deepEqual(state.getSnapshot().history, []); assert.ok(state.getSnapshot().historyError); assert.doesNotMatch(state.getSnapshot().historyError, /not-a-date|foreign|xxx/);
  assert.equal(state.getSnapshot().sessionID, A);
});
for (const body of [{ items: [] }, [summary(), summary()]]) test(`owned history rejects ${Array.isArray(body) ? 'duplicate IDs' : 'non-array response'}`, async () => {
  const state = await ready(); requireMethod(state, 'refreshHistory'); globalThis.fetch = async () => json(body);
  await state.refreshHistory(); assert.deepEqual(state.getSnapshot().history, []); assert.ok(state.getSnapshot().historyError);
});
test('owned selection verifies GET even when untrusted list already contains requested ID', async () => {
  const state = await ready(); requireMethod(state, 'refreshHistory'); requireMethod(state, 'selectChat');
  globalThis.fetch = async () => json([summary(B)]); await state.refreshHistory();
  const waiting = deferred(), calls = []; globalThis.fetch = async (url, options) => { calls.push([url, options.method ?? 'GET', options.credentials]); return waiting.promise; };
  const selecting = state.selectChat(B); assert.equal(state.getSnapshot().sessionID, ''); assert.equal(sessionStorage.getItem(pointer), A);
  waiting.resolve(json({ ...summary(B), messages: [] })); await selecting;
  assert.deepEqual(calls, [[`/api/conversations/${B}`, 'GET', 'same-origin']]); assert.equal(state.getSnapshot().sessionID, B); assert.equal(sessionStorage.getItem(pointer), B);
});
for (const outcome of ['foreign 404', 'archived', 'mismatched ID', 'malformed metadata', '403', 'transport']) test(`owned selection denies ${outcome} and restores prior verified chat`, async () => {
  const state = await ready(); requireMethod(state, 'selectChat');
  globalThis.fetch = async () => {
    if (outcome === 'transport') throw new Error('GENERATED_PRIVATE_TRANSPORT');
    if (outcome === 'foreign 404' || outcome === '403') return json({ message: 'GENERATED_PRIVATE_BODY' }, outcome === '403' ? 403 : 404);
    return json(summary(outcome === 'mismatched ID' ? C : B, outcome === 'archived' ? { archived: true } : outcome === 'malformed metadata' ? { updatedAt: 'invalid' } : {}));
  };
  await state.selectChat(B); assert.equal(state.getSnapshot().sessionID, A); assert.equal(sessionStorage.getItem(pointer), A);
  assert.ok(state.getSnapshot().error); assert.doesNotMatch(state.getSnapshot().error, /GENERATED_PRIVATE|invalid$/);
});
test('malformed selected ID is refused locally without a request or pointer change', async () => {
  const state = await ready(); requireMethod(state, 'selectChat'); let requests = 0;
  globalThis.fetch = async () => { requests++; return json(summary(B)); };
  await state.selectChat('../foreign'); assert.equal(requests, 0); assert.equal(state.getSnapshot().sessionID, A); assert.equal(sessionStorage.getItem(pointer), A); assert.ok(state.getSnapshot().error);
});
test('selected owned chat reload uses only its owner pointer and verifies owned GET', async () => {
  const state = await ready(); requireMethod(state, 'selectChat'); globalThis.fetch = async () => json(summary(B)); await state.selectChat(B);
  const calls = []; globalThis.fetch = async (url) => { calls.push(url); return json(summary(B)); };
  const reloaded = new ConversationSession(owner); await reloaded.start(); assert.equal(reloaded.getSnapshot().sessionID, B); assert.deepEqual(calls, [`/api/conversations/${B}`]);
});
for (const operation of ['newChat', 'selectChat', 'refreshHistory']) test(`${operation} 401 tears down owner history and pointer without restoration`, async () => {
  const state = await ready(); requireMethod(state, operation); requireMethod(state, 'refreshHistory');
  globalThis.fetch = async () => json([summary()]); await state.refreshHistory(); let invalid = 0; state.onAuthInvalid = () => invalid++;
  globalThis.fetch = async () => json({ message: 'GENERATED_PRIVATE_BODY' }, 401); await state[operation](operation === 'selectChat' ? B : undefined);
  assert.equal(state.getSnapshot().sessionID, ''); assert.deepEqual(state.getSnapshot().history, []); assert.equal(sessionStorage.getItem(pointer), null); assert.equal(invalid, 1);
  let requests = 0; globalThis.fetch = async () => { requests++; return json(summary(B)); }; await state.newChat(); await state.selectChat(B); await state.refreshHistory(); assert.equal(requests, 0);
});
test('slow selection A cannot overwrite newer verified selection B or pointer', async () => {
  const state = await ready(); requireMethod(state, 'selectChat');
  const waiting = deferred(), signals = []; globalThis.fetch = async (url, options) => { signals.push(options.signal); return url.endsWith(B) ? waiting.promise : json(summary(C)); };
  const old = state.selectChat(B); await state.selectChat(C); waiting.resolve(json(summary(B))); await old;
  assert.equal(signals[0].aborted, true); assert.equal(state.getSnapshot().sessionID, C); assert.equal(sessionStorage.getItem(pointer), C);
});
test('New chat supersedes slow selection and late selection failure cannot restore old chat', async () => {
  const state = await ready(); requireMethod(state, 'selectChat'); requireMethod(state, 'newChat');
  const waiting = deferred(); globalThis.fetch = async (url, options) => options.method === 'POST' ? json(summary(C)) : waiting.promise;
  const old = state.selectChat(B); await state.newChat(); waiting.resolve(json({}, 404)); await old;
  assert.equal(state.getSnapshot().sessionID, C); assert.equal(sessionStorage.getItem(pointer), C);
});
test('failed New chat during selection restores last verified ID rather than empty pending state', async () => {
  const state = await ready(); requireMethod(state, 'selectChat'); requireMethod(state, 'newChat');
  const waiting = deferred(); globalThis.fetch = async (_url, options) => options.method === 'POST' ? json({}, 403) : waiting.promise;
  const old = state.selectChat(B); await state.newChat(); waiting.resolve(json(summary(B))); await old;
  assert.equal(state.getSnapshot().sessionID, A); assert.equal(sessionStorage.getItem(pointer), A);
});
test('late old history list cannot replace newer selection metadata or active ID', async () => {
  const state = await ready(); requireMethod(state, 'refreshHistory'); requireMethod(state, 'selectChat');
  const waiting = deferred(); globalThis.fetch = async (url) => url === '/api/conversations' ? waiting.promise : json(summary(B, { title: 'Verified new selection' }));
  const listing = state.refreshHistory(); await state.selectChat(B); waiting.resolve(json([summary(A, { title: 'Obsolete list' })])); await listing;
  assert.equal(state.getSnapshot().sessionID, B); assert.equal(state.getSnapshot().history.some(chat => chat.title === 'Obsolete list'), false); assert.equal(state.getSnapshot().historyPending, false);
});
test('new history retry wins over an older response and remains read only', async () => {
  const state = await ready(); requireMethod(state, 'refreshHistory'); const waiting = deferred(); let requests = 0;
  globalThis.fetch = async () => ++requests === 1 ? waiting.promise : json([summary(B)]);
  const old = state.refreshHistory(); await state.refreshHistory(); waiting.resolve(json([summary(A)])); await old; assert.deepEqual(state.getSnapshot().history.map(chat => chat.id), [B]);
});
test('history error is body free and successful retry recovers while selected chat stays usable', async () => {
  const state = await ready(); requireMethod(state, 'refreshHistory'); globalThis.fetch = async () => json({ message: 'GENERATED_PRIVATE_BODY' }, 500);
  await state.refreshHistory(); assert.ok(state.getSnapshot().historyError); assert.doesNotMatch(state.getSnapshot().historyError, /GENERATED_PRIVATE/); assert.equal(state.getSnapshot().sessionID, A);
  globalThis.fetch = async () => json([summary(B)]); await state.refreshHistory(); assert.equal(state.getSnapshot().historyError, undefined); assert.deepEqual(state.getSnapshot().history.map(chat => chat.id), [B]);
});
for (const operation of ['selectChat', 'newChat', 'refreshHistory']) test(`disposed owner refuses late ${operation} completion and all pointer writes`, async () => {
  const state = await ready(); requireMethod(state, operation); const waiting = deferred(); globalThis.fetch = async () => waiting.promise; sessionStorage.writes = [];
  const pending = state[operation](operation === 'selectChat' ? B : undefined); state.dispose(); waiting.resolve(json(operation === 'refreshHistory' ? [summary(B)] : summary(B))); await pending;
  assert.equal(state.getSnapshot().sessionID, ''); assert.deepEqual(state.getSnapshot().history, []); assert.deepEqual(sessionStorage.writes, []);
  const next = new ConversationSession(otherOwner); globalThis.fetch = async () => json(summary(C)); await next.start(); assert.equal(next.getSnapshot().sessionID, C);
  assert.equal(sessionStorage.getItem(`craft-conversation:${otherOwner}`), C);
});
test('real owned picker renders escaped titles and separate New chat and archive controls', () => {
  const html = renderHistory({ history: [summary(A, { title: '<script>GENERATED</script>' })] });
  assert.match(html, /Chat history/); assert.match(html, /New chat/); assert.match(html, /Archive and reset/); assert.doesNotMatch(html, /Session ID|<script>/); assert.match(html, /&lt;script&gt;GENERATED&lt;\/script&gt;/);
});
test('real picker disables navigation during pending change but keeps loading state accessible', () => {
  const html = renderHistory({ pending: true, sessionID: '', historyPending: true, history: [summary(A)] });
  assert.match(html, /role="status"/); assert.match(html, /Loading chat history/); assert.match(html, /data-chat="[^"]+"[^>]*disabled/); assert.match(html, /<button[^>]*disabled[^>]*>(?:<svg.*?<\/svg>)?New chat/);
});
test('real picker makes history error retry and bounded list limitation accessible', () => {
  const html = renderHistory({ historyError: 'Could not load chat history. Try again.', historyLimited: true });
  assert.match(html, /role="alert"/); assert.match(html, /Retry history/); assert.match(html, /Chat history is limited to 100 entries\./);
  assert.doesNotMatch(html, /newest 100|more chats|total chats/i);
});

test('newer selection supersedes pending create and refuses its late pointer write', async () => {
  const state = await ready(); requireMethod(state, 'selectChat'); requireMethod(state, 'newChat');
  const waiting = deferred(), signals = []; globalThis.fetch = async (_url, options) => { signals.push(options.signal); return options.method === 'POST' ? waiting.promise : json(summary(C)); };
  const old = state.newChat(); await state.selectChat(C); waiting.resolve(json(summary(B))); await old;
  assert.equal(signals[0].aborted, true); assert.equal(state.getSnapshot().sessionID, C); assert.equal(sessionStorage.getItem(pointer), C);
});
for (const extra of [{ title: 'x'.repeat(201) }, { archived: true }, { pendingApprovals: -1 }]) test(`New chat rejects ${extra.archived ? 'archived' : extra.title ? 'overlong title' : 'invalid approvals'} response and restores verified chat`, async () => {
  const state = await ready(); requireMethod(state, 'newChat'); globalThis.fetch = async () => json(summary(B, extra)); await state.newChat();
  assert.equal(state.getSnapshot().sessionID, A); assert.equal(sessionStorage.getItem(pointer), A); assert.ok(state.getSnapshot().error);
});

test('New chat refuses a server response reusing the previous conversation ID', async () => {
  const state = await ready(); requireMethod(state, 'newChat'); globalThis.fetch = async () => json(summary(A)); await state.newChat();
  assert.equal(state.getSnapshot().sessionID, A); assert.equal(sessionStorage.getItem(pointer), A); assert.ok(state.getSnapshot().error, 'a reused ID cannot count as a new chat');
});


test('real picker distinguishes repeated default titles with readable updated dates', () => {
  const html = renderHistory({ history: [summary(A, { title: 'New conversation', updatedAt: '2026-10-17T09:12:00Z' }), summary(B, { title: 'New conversation', updatedAt: '2026-10-18T09:12:00Z' })] });
  const options = [...html.matchAll(/data-chat="([^"]+)"[^>]*title="([^"]+)"/g)].map(match => ({ value: match[1], label: match[2] }));
  assert.equal(options.length, 2); assert.deepEqual(options.map(option => option.value), [A, B]);
  assert.equal(new Set(options.map(option => option.label)).size, 2, 'default chat titles need visible distinguishing context');
  assert.match(options[0].label, /17/); assert.match(options[1].label, /18/);
  assert.equal(options.every(option => !/[0-9a-f]{8}-[0-9a-f-]{27}/i.test(option.label)), true, 'raw UUIDs remain internal identifiers');
});
test('real picker distinguishes identical titles and timestamps with compact display ordinals', () => {
  const html = renderHistory({ history: [summary(A, { title: 'New conversation' }), summary(B, { title: 'New conversation' }), summary(C, { title: 'New conversation' })] });
  const options = [...html.matchAll(/data-chat="([^"]+)"[^>]*title="([^"]+)"/g)].map(match => ({ value: match[1], label: match[2] }));
  assert.equal(options.length, 3); assert.deepEqual(options.map(option => option.value), [A, B, C]);
  assert.equal(new Set(options.map(option => option.label)).size, 3, 'equal title/time chats must remain distinguishable');
  assert.equal(options.every(option => !/[0-9a-f]{8}-[0-9a-f-]{27}/i.test(option.label)), true);
  assert.equal(options.every(option => option.label.length < 100), true, 'default-title collision labels remain compact');
});
