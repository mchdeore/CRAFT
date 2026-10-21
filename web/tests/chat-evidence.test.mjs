import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import './typescript-loader.mjs';
import { fromAgUiMessages } from '@assistant-ui/react-ag-ui';
import { AssistantRuntimeProvider, ThreadPrimitive, useExternalStoreRuntime } from '@assistant-ui/react';
const { CitationProvider, useCitations } = await import('../src/components/citations/citation-context.tsx');
const { CitationList } = await import('../src/components/citations/citation-list.tsx');

// Missing new production modules produce named assertions, never a skip or
// a load-only success. The network is the only substituted boundary.
const evidence = await import('../src/lib/chat-evidence.ts').catch(() => ({}));
const tools = await import('../src/components/chat-tool.tsx').catch(() => ({}));
const text = await import('../src/components/citations/preview/text/text-renderer.tsx').catch(() => ({}));
const preview = await import('../src/components/citations/preview/use-source-preview.ts');
const { DocumentRenderer } = await import('../src/components/citations/preview/document-renderer.tsx');
function required(mod, name) { assert.equal(typeof mod[name], 'function', `${name} production export is required`); return mod[name]; }
const quote = 'Generated source paragraph. '.repeat(35);
const digest = createHash('sha256').update(quote).digest('hex');
const handle = { item: 'generated-source_1', version: 2, block: 1, locator: 'heading:Evidence', quoteSha256: digest, simulated: true };
const citation = { handle, title: 'Generated source', headingPath: 'Evidence', classification: 'unclassified', quote };
const confidence = { score: 0.8, level: 'high', reason: 'Generated evidence matched.' };
const activity = { type: 'data', name: 'agui-activity/craft.answer', data: { messageId: 'answer-1', citations: [citation], confidence } };
const metadata = { item: handle.item, version: 2, title: citation.title, path: 'generated/source.md', tags: { mission: null, silo: null, stage: null }, summary: '', summaryKind: 'extract', outline: [{ block: 1, headingPath: 'Evidence' }], versions: [{ version: 2, sha256: 'a'.repeat(64), indexedAt: '2026-10-17T00:00:00Z' }], simulated: true, classification: 'unclassified', deleted: false };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } });
const signal = () => new AbortController().signal;
function network({ status = 'current', checkStatus = 200, docStatus = 200, document = metadata } = {}) {
  const calls = [];
  const fetcher = async (url, init) => {
    calls.push({ url, init });
    if (url === '/api/library/citations/check') {
      assert.equal(init.method, 'POST'); assert.equal(init.credentials, 'same-origin');
      assert.deepEqual(JSON.parse(init.body), { handle });
      return json({ status, latestVersion: 2 }, checkStatus);
    }
    assert.equal(url, '/api/library/documents/generated-source_1?version=2');
    assert.equal(init.credentials, 'same-origin');
    return json(document, docStatus);
  };
  return { calls, fetcher };
}

test('checked source adapter exists', () => required(evidence, 'checkSource'));
test('installed saved craft.answer activity reaches the typed evidence adapter', () => {
  const messages = fromAgUiMessages([
    { id: 'answer-1', role: 'assistant', content: 'Generated answer.' },
    { id: 'activity-1', role: 'activity', activityType: 'craft.answer', content: activity.data },
  ]);
  assert.equal(messages.length, 1);
  const records = required(evidence, 'answerEvidence')(messages[0].content);
  assert.equal(records.length, 1); assert.deepEqual(records[0], { citations: [citation], confidence });
});
test('missing unknown and malformed activity never verifies', () => {
  const parse = required(evidence, 'answerEvidence');
  for (const parts of [[], [null], [{ ...activity, name: 'unknown' }], [{ ...activity, data: null }], [{ ...activity, data: { citations: [citation] } }]]) assert.deepEqual(parse(parts), []);
});
test('evidence rejects citation count and confidence bounds', () => {
  const parse = required(evidence, 'answerEvidence');
  for (const data of [{ ...activity.data, citations: Array(9).fill(citation) }, ...[NaN, Infinity, -0.1, 1.1].map(score => ({ ...activity.data, confidence: { ...confidence, score } })), { ...activity.data, confidence: { ...confidence, level: 'certain' } }, { ...activity.data, confidence: { ...confidence, reason: 'x'.repeat(241) } }]) assert.deepEqual(parse([{ ...activity, data }]), []);
});
test('malformed handles and injected source URLs are refused before network', async () => {
  const check = required(evidence, 'checkSource'); let calls = 0;
  const never = async () => { calls++; throw new Error('network should not run'); };
  for (const bad of [null, { ...citation, previewURL: 'https://example.invalid' }, { ...citation, handle: { ...handle, item: '../secret' } }, { ...citation, handle: { ...handle, item: 'x'.repeat(129) } }, { ...citation, handle: { ...handle, version: 0 } }, { ...citation, handle: { ...handle, block: 1.1 } }, { ...citation, handle: { ...handle, locator: 'x'.repeat(513) } }, { ...citation, handle: { ...handle, quoteSha256: digest.toUpperCase() } }, { ...citation, handle: { ...handle, simulated: 'yes' } }, { ...citation, title: 'x'.repeat(241) }, { ...citation, headingPath: 'x'.repeat(513) }, { ...citation, classification: 'secret' }]) assert.equal(await check(bad, signal(), never), null);
  assert.equal(calls, 0);
});
test('full quote digest mismatch and quote limit prevent network', async () => {
  const check = required(evidence, 'checkSource'); let calls = 0;
  for (const bad of [{ ...citation, quote: quote + 'changed' }, { ...citation, quote: 'x'.repeat(65537) }]) assert.equal(await check(bad, signal(), async () => { calls++; return json({}); }), null);
  assert.equal(calls, 0);
});
test('current check and exact live metadata derive bounded protected source', async () => {
  const n = network(); const result = await required(evidence, 'checkSource')(citation, signal(), n.fetcher);
  assert.ok(result); assert.equal(n.calls.length, 2); assert.equal(result.title, 'Generated source'); assert.equal(result.classification, 'unclassified'); assert.equal(result.simulated, true); assert.equal(result.version, '2'); assert.equal(result.quote, quote.slice(0, 640));
  assert.equal(result.previewURL, '/api/library/documents/generated-source_1/preview?version=2&locator=heading%3AEvidence');
  assert.equal(result.downloadURL, '/api/library/documents/generated-source_1/download?version=2&locator=heading%3AEvidence');
  assert.equal(result.mediaType, '');
});
for (const status of ['changed', 'newer_version', 'withdrawn', 'invented']) test(`citation ${status} refuses without fetching metadata or exposing content`, async () => {
  const n = network({ status }); assert.equal(await required(evidence, 'checkSource')(citation, signal(), n.fetcher), null); assert.equal(n.calls.length, 1);
});
for (const status of [401, 403, 404, 500]) test(`citation check HTTP ${status} is body-free refusal`, async () => {
  const n = network({ checkStatus: status }); assert.equal(await required(evidence, 'checkSource')(citation, signal(), n.fetcher), null); assert.equal(n.calls.length, 1);
});
for (const status of [401, 403, 404, 500]) test(`document HTTP ${status} refuses a previously current citation`, async () => {
  const n = network({ docStatus: status }); assert.equal(await required(evidence, 'checkSource')(citation, signal(), n.fetcher), null);
});
test('mismatched deleted malformed and URL-bearing metadata refuse', async () => {
  const check = required(evidence, 'checkSource');
  for (const document of [null, {}, { ...metadata, item: 'another-source' }, { ...metadata, version: 3 }, { ...metadata, title: 'Changed title' }, { ...metadata, simulated: false }, { ...metadata, classification: 'protected-b' }, { ...metadata, deleted: true }, { ...metadata, previewURL: 'https://example.invalid' }]) assert.equal(await check(citation, signal(), network({ document }).fetcher), null);
});
test('transport failures refuse without propagating provider error text', async () => {
  assert.equal(await required(evidence, 'checkSource')(citation, signal(), async () => { throw new Error('RAW_SECRET_ERROR'); }), null);
});
test('malformed nested document metadata cannot verify a source', async () => {
  const check = required(evidence, 'checkSource');
  for (const document of [{ ...metadata, tags: { mission: 2, silo: null, stage: null } }, { ...metadata, outline: [{ block: 1, headingPath: 5 }] }, { ...metadata, versions: [{ version: 2, sha256: 'bad', indexedAt: '2026-10-17T00:00:00Z' }] }]) assert.equal(await check(citation, signal(), network({ document }).fetcher), null);
});
test('aborted evidence check ignores fetch completion even when transport ignores signal', async () => {
  const controller = new AbortController(); const n = network();
  const fetcher = async (...args) => { const r = await n.fetcher(...args); controller.abort(); return r; };
  assert.equal(await required(evidence, 'checkSource')(citation, controller.signal, fetcher), null); assert.equal(n.calls.length, 1);
});
test('reopening a checked citation repeats current check and document request', async () => {
  const n = network(); const check = required(evidence, 'checkSource');
  assert.ok(await check(citation, signal(), n.fetcher)); assert.ok(await check(citation, signal(), n.fetcher)); assert.equal(n.calls.length, 4);
});
test('actual tool SSR presents all typed statuses without raw disclosure or approval action', () => {
  const Component = required(tools, 'ChatTool');
  for (const [type, expected] of [['running', 'Running'], ['complete', 'Completed'], ['incomplete', 'Failed'], ['requires-action', 'Awaiting review'], ['future-status', 'Unavailable']]) {
    const html = renderToStaticMarkup(React.createElement(Component, { type: 'tool-call', toolName: 'library.search', toolCallId: 'call-1', args: { secret: 'RAW_ARGS' }, argsText: 'RAW_ARGS', result: { secret: 'RAW_RESULT' }, status: { type, reason: 'error', error: 'RAW_ERROR' } }));
    assert.match(html, new RegExp(expected)); assert.match(html, /library.search/); assert.doesNotMatch(html, /RAW_ARGS|RAW_RESULT|RAW_ERROR|<button|<form/);
  }
});
test('tool view bounds identity and never derives counts from unknown results', () => {
  const view = required(evidence, 'toolView');
  const result = view({ toolName: 'x'.repeat(81), toolCallId: 'x'.repeat(65), result: { hits: ['RAW_HIT'], truncated: true }, status: { type: 'complete' } });
  assert.equal(result.name, 'Tool'); assert.equal(result.correlationId, undefined); assert.equal(result.hitCount, undefined);
});
test('SDK completed tool with an explicit failed result is presented as failed', () => {
  assert.equal(required(evidence, 'toolView')({ toolName: 'library.search', toolCallId: 'call-error', isError: true, status: { type: 'complete' }, result: 'RAW_PROVIDER_ERROR' }).status, 'Failed');
});

const source = { title: 'Generated text', mediaType: 'application/pdf', previewURL: '/api/library/documents/generated-source_1/preview?version=2&locator=heading%3AEvidence', downloadURL: '/api/library/documents/generated-source_1/download?version=2&locator=heading%3AEvidence', previewRequestId: 1 };
// Complete one-pixel PNG, including IHDR/IDAT/IEND chunks, rather than MIME-only text.
const tinyPng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
test('authenticated allowed workspace PNG loads actual bytes with the PNG renderer format', async () => {
  const url = '/api/conversations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/files/generated-chart.png/content';
  const controller = new AbortController();
  const result = await preview.loadDocumentPreview({ ...source, previewURL: url }, controller.signal, async (requested, init) => {
    assert.equal(requested, url); assert.equal(init.credentials, 'same-origin'); assert.equal(init.cache, 'no-store'); assert.equal(init.signal, controller.signal);
    return new Response(tinyPng, { headers: { 'content-type': 'image/png', 'content-length': String(tinyPng.length) } });
  });
  assert.equal(result.type, 'ready', 'allowed image/png must reach ready instead of the unsupported-format refusal');
  assert.equal(result.format, 'png'); assert.equal(result.blob.type, 'image/png');
  assert.deepEqual(Buffer.from(await result.blob.arrayBuffer()), tinyPng);
});
test('PNG transport retains foreign URL size and cancellation guards', async () => {
  let calls = 0;
  for (const url of ['https://example.invalid/chart.png', '//example.invalid/chart.png', '/arbitrary/chart.png']) {
    const result = await preview.loadDocumentPreview({ ...source, previewURL: url }, signal(), async () => { calls++; return new Response(tinyPng, { headers: { 'content-type': 'image/png' } }); });
    assert.equal(result.type, 'error');
  }
  assert.equal(calls, 0);
  let cancelled = false;
  const body = new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array(1024 * 1024)); }, cancel() { cancelled = true; } });
  const tooLarge = await preview.loadDocumentPreview(source, signal(), async () => new Response(body, { headers: { 'content-type': 'image/png' } }));
  assert.equal(tooLarge.type, 'error'); assert.equal(cancelled, true);
  const abort = new AbortController();
  const late = await preview.loadDocumentPreview(source, abort.signal, async () => { abort.abort(); return new Response(tinyPng, { headers: { 'content-type': 'image/png' } }); });
  assert.equal(late.type, 'error');
});
test('protected byte MIME selects text even when old metadata says PDF', async () => {
  const result = await required(preview, 'loadDocumentPreview')(source, signal(), async () => new Response('Generated text', { headers: { 'content-type': 'text/markdown; charset=utf-8' } }));
  assert.equal(result.type, 'ready'); assert.equal(result.format, 'text'); assert.equal(await result.blob.text(), 'Generated text');
});
test('protected CSV response loads exact bytes as text', async () => {
  const csv = 'mission,parameter,value,unit\r\n"Generated, mission",Cost,12345,USD\r\n';
  const controller = new AbortController();
  const result = await preview.loadDocumentPreview(source, controller.signal, async (requested, init) => {
    assert.equal(requested, source.previewURL);
    assert.equal(init.credentials, 'same-origin');
    assert.equal(init.cache, 'no-store');
    assert.equal(init.signal, controller.signal);
    return new Response(csv, { headers: { 'content-type': 'text/csv; charset=utf-8', 'content-length': String(Buffer.byteLength(csv)) } });
  });
  assert.equal(result.type, 'ready', 'protected text/csv must reach the existing text preview');
  assert.equal(result.format, 'text');
  assert.equal(result.blob.type, 'text/csv; charset=utf-8');
  assert.equal(await result.blob.text(), csv);
});
test('preview MIME allowlist retains PDF and office formats', async () => {
  const load = required(preview, 'loadDocumentPreview');
  for (const [mime, format] of [['application/pdf', 'pdf'], ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'docx'], ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx'], ['text/plain', 'text']]) {
    const result = await load(source, signal(), async () => new Response('Generated bytes', { headers: { 'content-type': mime } })); assert.equal(result.type, 'ready'); assert.equal(result.format, format);
  }
});
test('existing protected conversation workspace content remains previewable', async () => {
  const url = '/api/conversations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/files/generated.md/content';
  const result = await required(preview, 'loadDocumentPreview')({ ...source, previewURL: url }, signal(), async (requested) => { assert.equal(requested, url); return new Response('Generated file', { headers: { 'content-type': 'text/plain' } }); });
  assert.equal(result.type, 'ready'); assert.equal(result.format, 'text');
});
test('preview refuses HTML unknown MIME external URLs and response failures safely', async () => {
  const load = required(preview, 'loadDocumentPreview');
  // Protected CRAFT originals of other types fall back to a download link without reading the body.
  for (const response of [new Response('RAW_BODY', { headers: { 'content-type': 'text/html' } }), new Response('RAW_BODY', { headers: { 'content-type': 'application/octet-stream' } })]) {
    const result = await load(source, signal(), async () => response); assert.equal(result.type, 'unsupported'); assert.doesNotMatch(JSON.stringify(result), /RAW_BODY/);
  }
  const refused = await load(source, signal(), async () => new Response('RAW_BODY', { status: 403, headers: { 'content-type': 'text/plain' } }));
  assert.equal(refused.type, 'error'); assert.doesNotMatch(refused.message, /RAW_BODY|403/);
  let calls = 0; const result = await load({ ...source, previewURL: 'https://example.invalid/preview' }, signal(), async () => { calls++; return new Response(); }); assert.equal(result.type, 'error'); assert.equal(calls, 0);
});
test('preview Content-Length rejects oversized response before consuming its stream', async () => {
  let reads = 0; let cancelled = false;
  const body = new ReadableStream({ pull() { reads++; }, cancel() { cancelled = true; } });
  const result = await required(preview, 'loadDocumentPreview')(source, signal(), async () => new Response(body, { headers: { 'content-type': 'text/plain', 'content-length': '10485761' } }));
  assert.equal(result.type, 'error'); assert.ok(reads <= 1); assert.equal(cancelled, true);
});
test('preview streaming limit cancels an oversized body without Content-Length', async () => {
  let cancelled = false; let sent = 0;
  const body = new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array(1024 * 1024)); sent++; if (sent === 12) controller.close(); }, cancel() { cancelled = true; } });
  const result = await required(preview, 'loadDocumentPreview')(source, signal(), async () => new Response(body, { headers: { 'content-type': 'text/plain' } })); assert.equal(result.type, 'error'); assert.equal(cancelled, true);
});
test('preview abort ignores late bytes and never returns ready', async () => {
  const controller = new AbortController();
  const result = await required(preview, 'loadDocumentPreview')(source, controller.signal, async () => { controller.abort(); return new Response('Late text', { headers: { 'content-type': 'text/plain' } }); }); assert.equal(result.type, 'error');
});
test('preview transport failure never shows raw exception text', async () => {
  const result = await required(preview, 'loadDocumentPreview')(source, signal(), async () => { throw new Error('RAW_TRANSPORT'); }); assert.equal(result.type, 'error'); assert.doesNotMatch(result.message, /RAW_TRANSPORT/);
});
test('same source open identity changes the production preview key', () => {
  assert.notEqual(preview.sourcePreviewKey(source), preview.sourcePreviewKey({ ...source, previewRequestId: 2 }));
});
test('actual escaped text renderer has bounded display and visible truncation', () => {
  const Component = required(text, 'TextContent');
  const html = renderToStaticMarkup(React.createElement(Component, { text: '<script>RAW_SCRIPT</script>\n# Generated\n' + 'x'.repeat(100001) + 'END_SENTINEL' }));
  assert.doesNotMatch(html, /<script>|END_SENTINEL/); assert.match(html, /&lt;script&gt;/); assert.match(html, /truncated/i); assert.ok(html.length < 101000);
});

// Generic test-only DOM host for the installed ReactDOM renderer. It has no
// citation state, selection rules, synthetic React hooks, or component doubles.
// Native layout, CSS, portals and browser integration remain root's checks.
function mountedHost() {
  class HostNode {
    constructor(type, owner) { this.nodeType = type; this.ownerDocument = owner; this.parentNode = null; this.childNodes = []; this.listeners = new Map(); }
    appendChild(node) { if (node.parentNode) node.parentNode.removeChild(node); node.parentNode = this; this.childNodes.push(node); return node; }
    insertBefore(node, before) { if (!before) return this.appendChild(node); if (node.parentNode) node.parentNode.removeChild(node); const index = this.childNodes.indexOf(before); assert.ok(index >= 0); this.childNodes.splice(index, 0, node); node.parentNode = this; return node; }
    removeChild(node) { const index = this.childNodes.indexOf(node); assert.ok(index >= 0); this.childNodes.splice(index, 1); node.parentNode = null; return node; }
    get firstChild() { return this.childNodes[0] ?? null; }
    get lastChild() { return this.childNodes.at(-1) ?? null; }
    get nextSibling() { return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this) + 1] ?? null; }
    get previousSibling() { return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this) - 1] ?? null; }
    get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map(node => node.textContent).join(''); }
    set textContent(value) { this.childNodes.forEach(node => { node.parentNode = null; }); this.childNodes = []; if (value) this.appendChild(this.ownerDocument.createTextNode(String(value))); }
    contains(node) { return node === this || this.childNodes.some(child => child.contains(node)); }
    getRootNode() { let node = this; while (node.parentNode) node = node.parentNode; return node; }
    addEventListener(type, callback, options) { const listeners = this.listeners.get(type) ?? []; listeners.push({ callback, capture: options === true || options?.capture === true }); this.listeners.set(type, listeners); }
    removeEventListener(type, callback) { this.listeners.set(type, (this.listeners.get(type) ?? []).filter(value => value.callback !== callback)); }
    dispatchEvent(event) {
      event.target = this; event.preventDefault ??= () => { event.defaultPrevented = true; }; event.stopPropagation ??= () => { event.cancelBubble = true; };
      const path = []; for (let node = this; node; node = node.parentNode) path.push(node);
      for (const capture of [true, false]) for (const node of capture ? [...path].reverse() : path) {
        for (const entry of node.listeners.get(event.type) ?? []) if (entry.capture === capture) { event.currentTarget = node; entry.callback(event); }
        if (event.cancelBubble) break;
      }
      return !event.defaultPrevented;
    }
  }
  class HostElement extends HostNode {
    constructor(name, owner, namespace = 'http://www.w3.org/1999/xhtml') { super(1, owner); this.tagName = name.toUpperCase(); this.nodeName = this.tagName; this.namespaceURI = namespace; this.attributes = new Map(); this.style = { setProperty(name, value) { this[name] = value; }, removeProperty(name) { delete this[name]; } }; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); }
    setAttributeNS(_namespace, name, value) { this.setAttribute(name, value); }
    removeAttributeNS(_namespace, name) { this.removeAttribute(name); }
    hasAttribute(name) { return this.attributes.has(name); }
  }
  class HostDocument extends HostNode {
    constructor() { super(9, null); this.ownerDocument = this; this.nodeName = '#document'; this.documentElement = new HostElement('html', this); this.head = new HostElement('head', this); this.body = new HostElement('body', this); this.appendChild(this.documentElement); this.documentElement.appendChild(this.head); this.documentElement.appendChild(this.body); this.activeElement = this.body; }
    createElement(name) { return new HostElement(name, this); }
    createElementNS(namespace, name) { return new HostElement(name, this, namespace); }
    createTextNode(value) { const node = new HostNode(3, this); node.nodeName = '#text'; node.nodeValue = String(value); return node; }
    createComment(value) { const node = new HostNode(8, this); node.nodeName = '#comment'; node.nodeValue = value; return node; }
  }
  const document = new HostDocument();
  const window = { document, HTMLElement: HostElement, HTMLIFrameElement: class {}, Node: HostNode, location: { href: 'http://127.0.0.1/' } };
  document.defaultView = window;
  const old = new Map(['window', 'document', 'HTMLElement', 'Node', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries({ window, document, HTMLElement: HostElement, Node: HostNode, IS_REACT_ACT_ENVIRONMENT: true })) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  const container = document.createElement('div'); document.body.appendChild(container);
  return { container, restore() { for (const [key, descriptor] of old) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; } }, elements(name) { const result = []; const visit = node => { if (node.tagName === name.toUpperCase()) result.push(node); node.childNodes.forEach(visit); }; visit(container); return result; } };
}
const tickMounted = () => new Promise(resolve => setImmediate(resolve));
const deferredMounted = () => { let resolve; const promise = new Promise(value => { resolve = value; }); return { promise, resolve }; };
async function settleMounted(predicate, label) {
  for (let count = 0; count < 80; count++) {
    await React.act(async () => { await tickMounted(); });
    if (predicate()) return;
  }
  assert.fail(`mounted production condition did not settle: ${label}`);
}
function generatedMounted(label) {
  const quote = `Generated ${label} quote for mounted scheduling.`;
  const handle = { item: `generated-${label.toLowerCase()}`, version: 1, block: 1, locator: 'heading:Evidence', quoteSha256: createHash('sha256').update(quote).digest('hex'), simulated: true };
  const citation = { handle, title: `Generated ${label} source`, headingPath: 'Evidence', classification: 'unclassified', quote };
  const metadata = { item: handle.item, version: 1, title: citation.title, path: `generated/${label}.md`, tags: { mission: null, silo: null, stage: null }, summary: '', summaryKind: 'extract', outline: [{ block: 1, headingPath: 'Evidence' }], versions: [{ version: 1, sha256: 'a'.repeat(64), indexedAt: '2026-10-17T00:00:00Z' }], simulated: true, classification: 'unclassified', deleted: false };
  const message = { id: `answer-${label}`, role: 'assistant', createdAt: new Date('2026-10-17T00:00:00Z'), status: { type: 'complete', reason: 'stop' }, metadata: { custom: {} }, content: [{ type: 'data', name: 'agui-activity/craft.answer', data: { messageId: `answer-${label}`, citations: [citation], confidence } }] };
  return { citation, metadata, message };
}
async function mountProductionAnswers(fixtures, fetcher) {
  const host = mountedHost(); const originalFetch = globalThis.fetch; globalThis.fetch = fetcher;
  const { createRoot } = await import('react-dom/client');
  const capture = { selected: null, preview: null };
  function PreviewProbe({ source }) { capture.preview = preview.useDocumentPreview(source); return null; }
  function SelectionProbe() { capture.selected = useCitations().selectedSource; return capture.selected ? React.createElement(PreviewProbe, { key: preview.sourcePreviewKey(capture.selected), source: capture.selected }) : null; }
  function Harness({ answers }) {
    const runtime = useExternalStoreRuntime({ messages: answers.map(value => value.message), isRunning: false, onNew: async () => {} });
    return React.createElement(AssistantRuntimeProvider, { runtime }, React.createElement(CitationProvider, null, React.createElement(ThreadPrimitive.Messages, { components: { AssistantMessage: CitationList, UserMessage: () => null } }), React.createElement(SelectionProbe)));
  }
  const root = createRoot(host.container);
  await React.act(async () => { root.render(React.createElement(Harness, { answers: fixtures })); });
  await settleMounted(() => host.elements('button').filter(button => fixtures.some(value => value.citation.title === button.textContent)).length === fixtures.length, 'all actual answer lists checked');
  return { host, capture, async click(fixture) { const button = host.elements('button').find(value => value.textContent === fixture.citation.title); assert.ok(button, 'actual checked source title button must exist'); await React.act(async () => { button.dispatchEvent({ type: 'click', bubbles: true, button: 0 }); await tickMounted(); }); }, async remove(fixture) { await React.act(async () => { root.render(React.createElement(Harness, { answers: fixtures.filter(value => value !== fixture) })); await tickMounted(); }); }, async dispose() { await React.act(async () => { root.unmount(); }); globalThis.fetch = originalFetch; host.restore(); } };
}
function mountedNetwork(fixtures, { slowCheckItem, slowCheck, refusePreviewItem, slowPreviewItem, slowPreview } = {}) {
  const checks = new Map(); const previews = new Map(); const signals = new Map();
  const fetcher = async (url, init) => {
    if (url === '/api/library/citations/check') {
      const request = JSON.parse(init.body); const fixture = fixtures.find(value => value.citation.handle.item === request.handle.item); assert.ok(fixture); assert.deepEqual(request.handle, fixture.citation.handle);
      const count = (checks.get(request.handle.item) ?? 0) + 1; checks.set(request.handle.item, count); signals.set(request.handle.item, init.signal);
      if (count > 1 && request.handle.item === slowCheckItem) return slowCheck.promise;
      return json({ status: 'current', latestVersion: 1 });
    }
    const fixture = fixtures.find(value => url.startsWith(`/api/library/documents/${value.citation.handle.item}`)); assert.ok(fixture, 'only derived protected fixture routes may be fetched');
    if (url.includes('/preview?')) {
      const item = fixture.citation.handle.item; previews.set(item, (previews.get(item) ?? 0) + 1);
      if (item === slowPreviewItem) return slowPreview.promise;
      return item === refusePreviewItem ? new Response('RAW_REFUSAL_BODY', { status: 403 }) : new Response('Generated preview', { headers: { 'content-type': 'text/plain' } });
    }
    assert.equal(url, `/api/library/documents/${fixture.citation.handle.item}?version=1`); return json(fixture.metadata);
  };
  return { checks, previews, signals, fetcher };
}
test('protected preview refusal invalidates actual checked cards confidence and shared selected metadata', async () => {
  const a = generatedMounted('A'); const network = mountedNetwork([a], { refusePreviewItem: a.citation.handle.item });
  const mounted = await mountProductionAnswers([a], network.fetcher);
  try {
    assert.match(mounted.host.container.textContent, /1 checked source/); assert.match(mounted.host.container.textContent, /Confidence:/);
    await mounted.click(a); await settleMounted(() => network.previews.get(a.citation.handle.item) === 1 && (mounted.capture.preview?.type === 'error' || mounted.capture.selected === null), 'protected preview refusal');
    assert.equal(mounted.capture.selected, null, 'refusal must clear selected source title/version/download metadata');
    assert.doesNotMatch(mounted.host.container.textContent, /Generated A source|Generated A quote|checked source|Confidence:|RAW_REFUSAL_BODY/);
  } finally { await mounted.dispose(); }
});
test('two actual mounted answer lists preserve newer B selection after slow A check completes', async () => {
  const a = generatedMounted('A'); const b = generatedMounted('B'); const slowCheck = deferredMounted();
  const network = mountedNetwork([a, b], { slowCheckItem: a.citation.handle.item, slowCheck });
  const mounted = await mountProductionAnswers([a, b], network.fetcher);
  try {
    await mounted.click(a); await settleMounted(() => network.checks.get(a.citation.handle.item) === 2, 'A pending check');
    await mounted.click(b); await settleMounted(() => mounted.capture.selected?.title === b.citation.title, 'newer B selection');
    await React.act(async () => { slowCheck.resolve(json({ status: 'current', latestVersion: 1 })); await tickMounted(); });
    for (let count = 0; count < 15; count++) await React.act(async () => { await tickMounted(); });
    assert.equal(mounted.capture.selected?.title, b.citation.title, 'obsolete A completion must not reopen over B');
    assert.equal(mounted.host.elements('section').length, 2, 'both production answer lists remain mounted');
  } finally { await mounted.dispose(); }
});
test('obsolete A protected preview refusal cannot clear the newer actual mounted B selection', async () => {
  const a = generatedMounted('A'); const b = generatedMounted('B'); const slowPreview = deferredMounted();
  const network = mountedNetwork([a, b], { slowPreviewItem: a.citation.handle.item, slowPreview });
  const mounted = await mountProductionAnswers([a, b], network.fetcher);
  try {
    await mounted.click(a); await settleMounted(() => network.previews.get(a.citation.handle.item) === 1, 'A pending protected GET');
    await mounted.click(b); await settleMounted(() => mounted.capture.selected?.title === b.citation.title && mounted.capture.preview?.type === 'ready', 'B selected and preview ready');
    await React.act(async () => { slowPreview.resolve(new Response('RAW_OBSOLETE_REFUSAL', { status: 403 })); await tickMounted(); });
    for (let count = 0; count < 15; count++) await React.act(async () => { await tickMounted(); });
    assert.equal(mounted.capture.selected?.title, b.citation.title); assert.equal(mounted.capture.preview?.type, 'ready');
    assert.doesNotMatch(mounted.host.container.textContent, /RAW_OBSOLETE_REFUSAL/);
    await mounted.remove(a);
    assert.equal(mounted.capture.selected?.title, b.citation.title, 'obsolete answer teardown must not clear newer B');
  } finally { await mounted.dispose(); }
});
test('removing the owning mounted answer aborts its pending shared check while another answer remains', async () => {
  const a = generatedMounted('A'); const b = generatedMounted('B'); const slowCheck = deferredMounted();
  const network = mountedNetwork([a, b], { slowCheckItem: a.citation.handle.item, slowCheck });
  const mounted = await mountProductionAnswers([a, b], network.fetcher);
  try {
    await mounted.click(a); await settleMounted(() => network.checks.get(a.citation.handle.item) === 2, 'owning A check pending');
    await mounted.remove(a);
    assert.equal(network.signals.get(a.citation.handle.item).aborted, true, 'removed answer must abort its own pending shared request');
    await mounted.click(b); await settleMounted(() => mounted.capture.selected?.title === b.citation.title, 'remaining B opens');
    await React.act(async () => { slowCheck.resolve(json({ status: 'current', latestVersion: 1 })); await tickMounted(); });
    assert.equal(mounted.capture.selected?.title, b.citation.title);
  } finally { await mounted.dispose(); }
});

async function mountPngRenderer(blob, title = 'Generated finance chart') {
  const host = mountedHost(); const { createRoot } = await import('react-dom/client');
  const root = createRoot(host.container); const committedSources = [];
  function Harness({ bytes, name }) {
    React.useLayoutEffect(() => { committedSources.push(host.elements('img').map(node => node.getAttribute('src'))); }, [bytes]);
    return React.createElement(DocumentRenderer, { format: 'png', blob: bytes, source: { ...source, title: name } });
  }
  return {
    host, committedSources,
    async render(bytes = blob, name = title) { await React.act(async () => { root.render(React.createElement(Harness, { bytes, name })); }); },
    async dispose() { await React.act(async () => { root.unmount(); }); host.restore(); },
  };
}
test('PNG selector renders owned pixels and text alt while replacement and unmount revoke old URLs', async () => {
  const blob = new Blob([tinyPng], { type: 'image/png' });
  const mounted = await mountPngRenderer(blob, '<script>finance</script>.png');
  let firstURL; let secondURL;
  try {
    await mounted.render();
    const first = mounted.host.elements('img')[0];
    assert.ok(first, 'DocumentRenderer must render an actual PNG image');
    firstURL = first.getAttribute('src'); assert.match(firstURL, /^blob:/);
    assert.equal(first.getAttribute('alt'), 'PNG source preview: <script>finance</script>.png');
    assert.equal(mounted.host.elements('script').length, 0);
    assert.deepEqual(Buffer.from(await (await fetch(firstURL)).arrayBuffer()), tinyPng);
    const next = new Blob([tinyPng], { type: 'image/png' });
    await mounted.render(next, 'Replacement chart');
    const second = mounted.host.elements('img')[0]; assert.ok(second); assert.notEqual(second, first, 'replacement needs a new image so obsolete decode events cannot fail the new source');
    secondURL = second.getAttribute('src'); assert.notEqual(secondURL, firstURL);
    assert.equal(second.getAttribute('alt'), 'PNG source preview: Replacement chart');
    assert.ok(mounted.committedSources.at(-1).every(url => url !== firstURL), 'replacement render must not commit the old image while waiting for effects');
    await assert.rejects(fetch(firstURL), 'old source ObjectURL must be revoked');
    assert.deepEqual(Buffer.from(await (await fetch(secondURL)).arrayBuffer()), tinyPng);
    await React.act(async () => { first.dispatchEvent({ type: 'error' }); });
    assert.equal(mounted.host.elements('img')[0], second, 'obsolete decode failure cannot hide the newer image');
  } finally { await mounted.dispose(); }
  if (secondURL) await assert.rejects(fetch(secondURL), 'unmount must revoke the remaining ObjectURL');
});
test('PNG decode failure removes failed pixels and recovers on replacement without stale content', async () => {
  const malformed = new Blob(['RAW_MALFORMED_IMAGE'], { type: 'image/png' });
  const mounted = await mountPngRenderer(malformed); let failedURL; let recoveredURL;
  try {
    await mounted.render(); const image = mounted.host.elements('img')[0]; assert.ok(image, 'PNG image must be mounted for the browser decoder'); failedURL = image.getAttribute('src');
    // This generic Node DOM host cannot decode raster data. Deliver the native
    // decoder's error event to the real ReactDOM-rendered image and handler.
    await React.act(async () => { image.dispatchEvent({ type: 'error' }); });
    assert.equal(mounted.host.elements('img').length, 0);
    assert.match(mounted.host.container.textContent, /preview.*unavailable/i);
    assert.doesNotMatch(mounted.host.container.textContent, /RAW_MALFORMED_IMAGE|blob:/);
    await mounted.render(new Blob([tinyPng], { type: 'image/png' }), 'Recovered chart');
    const recovered = mounted.host.elements('img')[0]; assert.ok(recovered); recoveredURL = recovered.getAttribute('src');
    assert.equal(recovered.getAttribute('alt'), 'PNG source preview: Recovered chart');
    assert.doesNotMatch(mounted.host.container.textContent, /unavailable/i);
    await assert.rejects(fetch(failedURL));
    assert.deepEqual(Buffer.from(await (await fetch(recoveredURL)).arrayBuffer()), tinyPng);
  } finally { await mounted.dispose(); }
  if (recoveredURL) await assert.rejects(fetch(recoveredURL));
});
