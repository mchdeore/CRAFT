import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import './typescript-loader.mjs';
const clientURL = new URL('../src/lib/tools-reference.ts', import.meta.url);
const viewURL = new URL('../src/components/tools-reference.tsx', import.meta.url);
const client = existsSync(clientURL) ? await import(clientURL) : {};
const view = existsSync(viewURL) ? await import(viewURL) : {};
const App = (await import('../src/App.tsx')).default;
const tool = (name = 'search', extra = {}) => ({
  name, operation: `app.library.handlers.${name}`, description: 'Find generated documents',
  effects: 'NONE', needsApproval: false, route: 'POST /api/library/search',
  inputSchema: { type: 'object', properties: { query: { type: 'string' } } },
  outputSchema: { type: 'object' }, availableIn: ['api', 'mcp', 'chat', 'read_only_query'], ...extra,
});
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const fetchTools = signal => { assert.equal(typeof client.fetchToolReference, 'function', 'Missing authenticated reference client'); return client.fetchToolReference(signal); };
const render = props => { assert.equal(typeof view.ToolReferenceView, 'function', 'Missing reference view'); return renderToStaticMarkup(React.createElement(view.ToolReferenceView, props)); };

test('reference fetch uses the existing authenticated endpoint and signal', async () => {
  const controller = new AbortController(); let request;
  globalThis.fetch = async (url, options) => { request = { url, options }; return json([tool()]); };
  assert.deepEqual(await fetchTools(controller.signal), [tool()]);
  assert.equal(request.url, '/api/tools'); assert.equal(request.options.method, 'GET');
  assert.equal(request.options.credentials, 'same-origin'); assert.equal(request.options.signal, controller.signal);
});

test('current 401 is reported without reading or exposing its body', async () => {
  let bodyReads = 0;
  globalThis.fetch = async () => ({ ok: false, status: 401, json: () => { bodyReads++; throw Error('private body'); } });
  await assert.rejects(() => fetchTools(new AbortController().signal), error => error.code === 'unauthenticated');
  assert.equal(bodyReads, 0);
});

test('a nullable output schema is valid reference metadata', async () => {
  const metadata = tool('raw_result', { outputSchema: null });
  globalThis.fetch = async () => json([metadata]);
  assert.deepEqual(await fetchTools(new AbortController().signal), [metadata]);
});

test('server and transport failures expose a generic message', async () => {
  globalThis.fetch = async () => json({ message: 'private generated error' }, 403);
  await assert.rejects(() => fetchTools(new AbortController().signal), error => error.code === 'unavailable' && !error.message.includes('private'));
  globalThis.fetch = async () => { throw Error('private transport detail'); };
  await assert.rejects(() => fetchTools(new AbortController().signal), error => error.code === 'unavailable' && !error.message.includes('private'));
});

test('an already aborted reference does not request metadata', async () => {
  const controller = new AbortController(); controller.abort(); let calls = 0;
  globalThis.fetch = async () => { calls++; return json([tool()]); };
  await assert.rejects(() => fetchTools(controller.signal), { name: 'AbortError' }); assert.equal(calls, 0);
});

test('late success and late 401 after abort cannot become current data or auth failure', async () => {
  for (const status of [200, 401]) {
    const pending = deferred(); const controller = new AbortController();
    globalThis.fetch = () => pending.promise;
    const result = fetchTools(controller.signal); controller.abort(); pending.resolve(json([tool()], status));
    await assert.rejects(() => result, { name: 'AbortError' });
  }
});

test('abort while JSON is resolving does not publish a stale catalog', async () => {
  const pending = deferred(); const started = deferred(); const controller = new AbortController();
  globalThis.fetch = async () => ({ ok: true, status: 200, json: () => { started.resolve(); return pending.promise; } });
  const result = fetchTools(controller.signal); await started.promise; controller.abort(); pending.resolve([tool()]);
  await assert.rejects(() => result, { name: 'AbortError' });
});

test('malformed metadata and duplicate names are rejected safely', async () => {
  for (const body of [null, {}, [tool('search', { inputSchema: [] })], [tool('search', { inputSchema: null })], [tool('search', { outputSchema: 42 })], [tool('search', { availableIn: ['healthy'] })], [tool('search', { needsApproval: 'false' })], [tool(), tool()]]) {
    globalThis.fetch = async () => json(body);
    await assert.rejects(() => fetchTools(new AbortController().signal), error => error.code === 'invalid_response');
  }
  globalThis.fetch = async () => json([]); assert.deepEqual(await fetchTools(new AbortController().signal), []);
});

test('local filters match names, descriptions and declared channels without mutation', () => {
  assert.equal(typeof client.filterTools, 'function');
  const tools = [tool('write_note', { description: 'Store a note', availableIn: ['api', 'mcp', 'chat'] }), tool()];
  const original = structuredClone(tools);
  assert.deepEqual(client.filterTools(tools, '  DOCUMENTS ', 'all').map(item => item.name), ['search']);
  assert.deepEqual(client.filterTools(tools, 'write', 'chat').map(item => item.name), ['write_note']);
  assert.deepEqual(client.filterTools(tools, '', 'read_only_query').map(item => item.name), ['search']);
  assert.deepEqual(client.filterTools(tools, 'write', 'read_only_query'), []); assert.deepEqual(tools, original);
});

test('schema and description markup is rendered as text with meaningful labels', () => {
  const html = render({ tools: [tool('search', { description: '<img src=x onerror=alert(1)>', inputSchema: { example: '<script>alert(1)</script>' } })] });
  assert.match(html, /Input schema/); assert.match(html, /Output schema/); assert.match(html, /Declared channels/);
  assert.match(html, /&lt;img/); assert.match(html, /&lt;script/); assert.doesNotMatch(html, /<img|<script/);
  assert.match(html, /Filter tools/); assert.match(html, /Channel/);
});

test('null schemas and approval-required tools are described without readiness claims', () => {
  const html = render({ tools: [tool('delete_note', { effects: 'DATA', needsApproval: true, outputSchema: null, availableIn: ['api', 'mcp', 'chat'] })] });
  assert.match(html, /Native JSON schema unavailable/); assert.match(html, /Agent approval required/);
  assert.doesNotMatch(html, /service is healthy|service is running|skill installed/i);
});

test('empty catalogs and unmatched filters remain distinguishable', () => {
  assert.match(render({ tools: [] }), /No tools were returned/);
  assert.match(render({ tools: [tool()], query: 'missing' }), /No matching tools/);
});

test('load failures present an alert and retry without claiming an empty catalog', () => {
  const html = render({ tools: [], status: 'error', onRetry: () => {} });
  assert.match(html, /role="alert"/); assert.match(html, /Retry/);
  assert.doesNotMatch(html, /No tools were returned|No matching tools/);
});

test('reference starts with a loading state and is absent while signed out', () => {
  assert.equal(typeof view.ToolsReference, 'function');
  assert.match(renderToStaticMarkup(React.createElement(view.ToolsReference, { onAuthInvalid: () => {} })), /Loading tool reference/);
  assert.doesNotMatch(renderToStaticMarkup(React.createElement(App)), /Tools reference/);
});
