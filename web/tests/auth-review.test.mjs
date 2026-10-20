import test from 'node:test';
import assert from 'node:assert/strict';
import './typescript-loader.mjs';
const { AuthSession } = await import('../src/hooks/use-auth-session.ts');
const { createThreadSession } = await import('../src/lib/agent-history.ts');
const ownerA = { id: '11111111-1111-4111-8111-111111111111', username: 'generated-a', displayName: 'Generated A', roles: ['member'] };
const ownerB = { id: '22222222-2222-4222-8222-222222222222', username: 'generated-b', displayName: 'Generated B', roles: ['member'] };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const deferred = () => { let resolve, reject; const promise = new Promise((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setImmediate(resolve));

for (const outcome of ['204', 'transport failure']) test(`stale invalidation preserves queued DELETE and its ${outcome} outcome`, async () => {
  const state = new AuthSession(); globalThis.fetch = async () => json(ownerA); await state.start();
  const waiting = deferred(); globalThis.fetch = async (url, options) => { assert.equal(url, '/api/session'); assert.equal(options.method, 'DELETE'); return waiting.promise; };
  const logout = state.signOut(); await tick(); state.invalidate();
  const pendingState = state.getSnapshot();
  if (outcome === '204') waiting.resolve(new Response(null, { status: 204 })); else waiting.reject(new Error('Generated transport failure'));
  await logout;
  assert.equal(pendingState.me, null);
  assert.equal(pendingState.status, 'signing-out', 'old private callbacks must not expose sign-in while revocation is pending');
  assert.equal(state.getSnapshot().status, outcome === '204' ? 'signed-out' : 'logout-failed');
  if (outcome === '204') assert.equal(state.getSnapshot().error, undefined); else assert.match(state.getSnapshot().error, /not confirmed/i);
});
test('stale invalidation preserves failed logout and cannot permit a new login', async () => {
  const state = new AuthSession(); globalThis.fetch = async () => json(ownerA); await state.start();
  globalThis.fetch = async () => { throw new Error('Generated transport failure'); }; await state.signOut();
  state.invalidate(); assert.equal(state.getSnapshot().status, 'logout-failed');
  let requests = 0; globalThis.fetch = async () => { requests++; return json(ownerB); };
  await state.signIn({ username: 'generated-b', password: 'generated-password' }); assert.equal(requests, 0); assert.equal(state.getSnapshot().me, null);
});
test('previous authenticated generation cannot invalidate the newly signed-in owner', async () => {
  const state = new AuthSession(); globalThis.fetch = async () => json(ownerA); await state.start();
  const previousGeneration = state.getSnapshot().generation;
  globalThis.fetch = async () => new Response(null, { status: 204 }); await state.signOut();
  globalThis.fetch = async () => json(ownerB); await state.signIn({ username: 'generated-b', password: 'generated-password' });
  state.invalidate(previousGeneration); assert.deepEqual(state.getSnapshot().me, ownerB); assert.equal(state.getSnapshot().status, 'signed-in');
});
test('actual installed HttpAgent abort remains cancellation without a failure banner or payload logging', async () => {
  const output = [], failures = [], subscriberErrors = []; const original = console.error;
  console.error = (...args) => output.push(args.map(String).join(' '));
  const resources = createThreadSession('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', { onAuthInvalid: () => failures.push('auth'), onSessionInvalid: () => failures.push('renew'), onError: message => failures.push(message) });
  const subscription = resources.agent.subscribe({ onRunFailed: ({ error }) => { subscriberErrors.push(error); } });
  try {
    globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Generated private cancellation reason', 'AbortError')), { once: true }));
    const run = resources.agent.runAgent(); await tick(); resources.agent.abortRun();
    await assert.doesNotReject(run, 'installed SDK intentional abort must remain successful cancellation'); await tick();
    assert.deepEqual(failures, [], 'intentional Stop generating must not become a failed-run banner');
    assert.equal(output.length, 0, 'SDK intentional cancellation must remain silent');
    assert.ok(subscriberErrors.length > 0, 'installed SDK must deliver the cancellation to the real subscriber');
    assert.equal(subscriberErrors.every(error => error.name === 'AbortError'), true);
    assert.equal(subscriberErrors.some(error => String(error).includes('private cancellation reason')), false);
  } finally { subscription.unsubscribe(); resources.dispose(); console.error = original; }
});
const { generateId } = await import('@assistant-ui/core');
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function sseRun(input) {
  return new Response(`data: ${JSON.stringify({ type: 'RUN_STARTED', threadId: input.threadId, runId: input.runId })}\n\ndata: ${JSON.stringify({ type: 'RUN_FINISHED', threadId: input.threadId, runId: input.runId })}\n\n`, { headers: { 'content-type': 'text/event-stream' } });
}
test('installed assistant-ui opaque run ID becomes one durable UUID shared by request and lifecycle events', async () => {
  const callerId = generateId(), observed = [], wire = [];
  const resources = createThreadSession('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', { onAuthInvalid() {}, onSessionInvalid() {}, onError() {} });
  globalThis.fetch = async (_url, options) => { const input = JSON.parse(options.body); wire.push(input); return sseRun(input); };
  try {
    await resources.agent.runAgent({ runId: callerId }, {
      onRunInitialized: ({ input }) => { observed.push(input.runId); },
      onRunStartedEvent: ({ event }) => { observed.push(event.runId); },
      onRunFinishedEvent: ({ event }) => { observed.push(event.runId); },
    });
    assert.equal(UUID.test(wire[0].runId), true, 'CRAFT durable run requires UUID even when assistant-ui supplied an opaque ID');
    assert.deepEqual(observed, [wire[0].runId, wire[0].runId, wire[0].runId]);
  } finally { resources.dispose(); }
});
test('valid durable run UUID stays unchanged on both a new run and a resume', async () => {
  const runId = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', wire = [];
  const resources = createThreadSession('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', { onAuthInvalid() {}, onSessionInvalid() {}, onError() {} });
  globalThis.fetch = async (_url, options) => { const input = JSON.parse(options.body); wire.push(input); return sseRun(input); };
  try {
    await resources.agent.runAgent({ runId });
    await resources.agent.runAgent({ runId, resume: [{ interruptId: 'generated-interrupt', value: { approved: true } }] });
    assert.deepEqual(wire.map(input => input.runId), [runId, runId]);
    assert.deepEqual(wire[1].resume, [{ interruptId: 'generated-interrupt', value: { approved: true } }]);
  } finally { resources.dispose(); }
});
test('opaque resume run ID is refused without creating a different durable run or an outbound request', async () => {
  let requests = 0; const resources = createThreadSession('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', { onAuthInvalid() {}, onSessionInvalid() {}, onError() {} });
  globalThis.fetch = async (_url, options) => { requests++; return sseRun(JSON.parse(options.body)); };
  try {
    await assert.rejects(resources.agent.runAgent({ runId: generateId(), resume: [{ interruptId: 'generated-interrupt', value: { approved: true } }] }), /durable.*run.*ID/i);
    assert.equal(requests, 0);
  } finally { resources.dispose(); }
});
const citations = await import('../src/components/citations/citation-records.ts');
test('effect cleanup clears private citation epoch immediately and delayed history teardown cannot clear a new owner cache', async () => {
  citations.clearCitationCache(); const waiting = deferred(); let requests = 0;
  globalThis.fetch = async () => ++requests === 1 ? waiting.promise : json({ title: 'Generated next owner', subtitle: 'Generated', previewURL: '/generated', mediaType: 'text/plain' });
  const resources = createThreadSession('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', { onAuthInvalid() {}, onSessionInvalid() {}, onError() {} });
  const oldLookup = citations.loadCitation('/api/citations/generated');
  assert.equal(typeof resources.release, 'function', 'replay-safe effect cleanup must invalidate private cache immediately');
  resources.release();
  assert.equal(citations.currentStates(['/api/citations/generated']).get('/api/citations/generated').status, 'loading');
  await citations.loadCitation('/api/citations/generated'); await tick();
  waiting.resolve(json({ title: 'Generated old owner', subtitle: 'Generated', previewURL: '/generated', mediaType: 'text/plain' })); await oldLookup;
  assert.equal(citations.currentStates(['/api/citations/generated']).get('/api/citations/generated').record.title, 'Generated next owner'); citations.clearCitationCache();
});
const { createAgUiSubscriber } = await import('../node_modules/@assistant-ui/react-ag-ui/dist/runtime/adapter/subscriber.js');
test('installed assistant-ui subscriber accepts backend lifecycle UUID without changing event IDs', async () => {
  const dispatched = [], callerId = generateId(); let wireId;
  const resources = createThreadSession('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', { onAuthInvalid() {}, onSessionInvalid() {}, onError() {} });
  globalThis.fetch = async (_url, options) => { const input = JSON.parse(options.body); wireId = input.runId; return sseRun(input); };
  try {
    await resources.agent.runAgent({ runId: callerId }, createAgUiSubscriber({ runId: callerId, dispatch: event => dispatched.push(event) }));
    assert.deepEqual(dispatched.filter(event => event.type === 'RUN_FINISHED').map(event => event.runId), [wireId]);
    assert.equal(UUID.test(wireId), true);
  } finally { resources.dispose(); }
});
test('premature SSE EOF never becomes the installed assistant-ui subscriber synthetic success', async () => {
  const dispatched = [], callerId = generateId();
  const resources = createThreadSession('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', { onAuthInvalid() {}, onSessionInvalid() {}, onError() {} });
  globalThis.fetch = async (_url, options) => { const input = JSON.parse(options.body); return new Response(`data: ${JSON.stringify({ type: 'RUN_STARTED', threadId: input.threadId, runId: input.runId })}\n\n`, { headers: { 'content-type': 'text/event-stream' } }); };
  try {
    await assert.rejects(resources.agent.runAgent({ runId: callerId }, createAgUiSubscriber({ runId: callerId, dispatch: event => dispatched.push(event) })), /before.*complet/i);
    assert.equal(dispatched.some(event => event.type === 'RUN_FINISHED'), false);
  } finally { resources.dispose(); }
});
test('a completed next-owner run cannot authorize premature completion of an older owner run', async () => {
  const waiting = deferred(); const old = createThreadSession('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', { onAuthInvalid() {}, onSessionInvalid() {}, onError() {} });
  const next = createThreadSession('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', { onAuthInvalid() {}, onSessionInvalid() {}, onError() {} });
  globalThis.fetch = async (_url, options) => { const input = JSON.parse(options.body); if (input.threadId.startsWith('a')) return waiting.promise; return sseRun(input); };
  const oldRun = old.agent.runAgent({ runId: generateId() });
  try {
    await next.agent.runAgent({ runId: generateId() });
    waiting.resolve(new Response(`data: ${JSON.stringify({ type: 'RUN_STARTED', threadId: old.agent.threadId, runId: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc' })}\n\n`, { headers: { 'content-type': 'text/event-stream' } }));
    await assert.rejects(oldRun, /before.*complet/i);
  } finally { old.dispose(); next.dispose(); }
});
