import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import './typescript-loader.mjs';
import { mountedHost } from './source-import-host.mjs';
const clientURL = new URL('../src/lib/skills.ts', import.meta.url);
const viewURL = new URL('../src/components/skills-workspace.tsx', import.meta.url);
const client = existsSync(clientURL) ? await import(clientURL) : {};
const view = existsSync(viewURL) ? await import(viewURL) : {};
const id = '01997bd1-0000-7000-8000-000000000001';
const mutation = '01997bd1-0000-7000-8000-000000000002';
const nextMutation = '01997bd1-0000-7000-8000-000000000003';
const skill = (changes = {}) => ({ id, revision: 2, name: 'Engineering', description: 'Generated example', instructions: 'Compare parameters', exampleInputs: ['Compare power'], toolNames: ['list_documents'], unavailableTools: [], createdAt: '2026-10-18T00:00:00+00:00', updatedAt: '2026-10-18T01:00:00+00:00', ...changes });
const summary = (changes = {}) => { const { instructions, exampleInputs, ...row } = skill(changes); return row; };
const tool = () => ({ name: 'list_documents', operation: 'app.library.handlers.list_documents', description: 'List generated documents', effects: 'NONE', needsApproval: false, route: 'GET /api/library/documents', inputSchema: {}, outputSchema: {}, availableIn: ['api', 'mcp', 'chat', 'read_only_query'] });
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const signal = () => new AbortController().signal;
const requireFunction = name => { assert.equal(typeof client[name], 'function', `Missing skills client ${name}`); return client[name]; };
const render = props => { assert.equal(typeof view.SkillsView, 'function', 'Missing Skills editor'); return renderToStaticMarkup(React.createElement(view.SkillsView, props)); };

test('skills list uses authenticated same-origin cursor endpoint', async () => {
  let request; const active = signal();
  globalThis.fetch = async (url, options) => { request = { url, options }; return json({ items: [summary()], nextCursor: id }); };
  assert.equal((await requireFunction('fetchSkills')(active, id)).items[0].id, id);
  assert.equal(request.url, `/api/skills?after=${id}`);
  assert.equal(request.options.credentials, 'same-origin'); assert.equal(request.options.signal, active);
});

test('open and historical revision use actual typed-query routes', async () => {
  const requests = []; globalThis.fetch = async url => { requests.push(url); return json(skill()); };
  assert.deepEqual(await requireFunction('fetchSkill')(id, signal()), skill());
  assert.deepEqual(await requireFunction('fetchSkill')(id, signal(), 2), skill());
  assert.deepEqual(requests, [`/api/skills/${id}`, `/api/skills/${id}/versions?revision=2`]);
});

test('save sends exact mutation and expected revision without a body owner or path id', async () => {
  const request = { id, expectedRevision: 2, mutationId: mutation, name: 'Edited', description: '', instructions: 'Test', exampleInputs: [], toolNames: [] }; let observed;
  globalThis.fetch = async (url, options) => { observed = { url, options }; return json(skill({ revision: 3, name: 'Edited' })); };
  assert.equal((await requireFunction('saveSkill')(request, signal())).revision, 3);
  assert.equal(observed.url, `/api/skills/${id}`); assert.equal(observed.options.method, 'PUT');
  assert.equal(observed.options.credentials, 'same-origin');
  assert.deepEqual(JSON.parse(observed.options.body), Object.fromEntries(Object.entries(request).filter(([key]) => key !== 'id')));
});

test('current auth conflict and server errors never read or expose raw bodies', async () => {
  for (const [status, code] of [[401, 'unauthenticated'], [409, 'conflict'], [404, 'not_found'], [400, 'invalid_input'], [503, 'unavailable']]) {
    let reads = 0; globalThis.fetch = async () => ({ ok: false, status, json: () => { reads++; throw Error('private body'); } });
    await assert.rejects(() => requireFunction('fetchSkill')(id, signal()), error => error.code === code && !error.message.includes('private'));
    assert.equal(reads, 0);
  }
});

test('malformed definitions and duplicate pages are rejected safely', async () => {
  for (const body of [skill({ revision: 0 }), skill({ instructions: null }), skill({ toolNames: ['invented name'] }), skill({ unavailableTools: ['read_note'] }), skill({ exampleInputs: [4] }), skill({ id: '../../private' })]) {
    globalThis.fetch = async () => json(body);
    await assert.rejects(() => requireFunction('fetchSkill')(id, signal()), error => error.code === 'invalid_response');
  }
  globalThis.fetch = async () => json({ items: [summary(), summary()], nextCursor: null });
  await assert.rejects(() => requireFunction('fetchSkills')(signal()), error => error.code === 'invalid_response');
});

test('invalid IDs and revisions fail before a request', async () => {
  let calls = 0; globalThis.fetch = async () => { calls++; return json(skill()); };
  for (const [identifier, revision] of [['../../private', 1], [id, 0], [id, 1.5]]) await assert.rejects(() => requireFunction('fetchSkill')(identifier, signal(), revision));
  assert.equal(calls, 0);
});

test('aborted fetch and late JSON cannot replace another owner state', async () => {
  const active = new AbortController(); globalThis.fetch = async () => { active.abort(); return json(skill()); };
  await assert.rejects(() => requireFunction('fetchSkill')(id, active.signal), error => error.name === 'AbortError');
  const later = new AbortController(); globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => { later.abort(); return skill(); } });
  await assert.rejects(() => requireFunction('fetchSkill')(id, later.signal), error => error.name === 'AbortError');
});

test('network errors are generic', async () => {
  globalThis.fetch = async () => { throw Error('private host credential'); };
  await assert.rejects(() => requireFunction('fetchSkills')(signal()), error => error.code === 'unavailable' && !error.message.includes('private'));
});

test('new draft keeps one ID and opens the saved immutable revision', () => {
  const draft = requireFunction('newSkillDraft')(id); assert.equal(draft.id, id); assert.equal(draft.expectedRevision, 0);
  const saved = skill(); const opened = requireFunction('openSkillDraft')(saved); assert.equal(opened.expectedRevision, 2); assert.equal(opened.id, id); assert.equal(opened.dirty, false);
  opened.fields.toolNames.push('read_note'); assert.deepEqual(saved.toolNames, ['list_documents']);
});

test('failed save retries an exact snapshot and edits allocate a new mutation', () => {
  const opened = requireFunction('openSkillDraft')(skill());
  const edited = requireFunction('changeSkillDraft')(opened, { name: 'Edited' });
  const first = requireFunction('prepareSkillSave')(edited, mutation);
  const retry = requireFunction('prepareSkillSave')(first.draft, nextMutation);
  assert.deepEqual(retry.request, first.request); assert.equal(retry.request.mutationId, mutation);
  const changed = requireFunction('changeSkillDraft')(retry.draft, { instructions: 'Changed' });
  const next = requireFunction('prepareSkillSave')(changed, nextMutation);
  assert.equal(next.request.mutationId, nextMutation); assert.equal(next.request.expectedRevision, 2);
  assert.equal(first.request.instructions, 'Compare parameters');
});

test('save snapshots do not share mutable arrays with the draft or caller', () => {
  const draft = requireFunction('openSkillDraft')(skill()); const prepared = requireFunction('prepareSkillSave')(draft, mutation);
  draft.fields.exampleInputs.push('Later'); prepared.request.toolNames.push('read_note');
  const retry = requireFunction('prepareSkillSave')(prepared.draft, nextMutation);
  assert.deepEqual(retry.request.exampleInputs, ['Compare power']); assert.deepEqual(retry.request.toolNames, ['list_documents']);
});

test('editing another field preserves original empty and multiline examples', () => {
  const opened = requireFunction('openSkillDraft')(skill({ exampleInputs: ['', 'one\ntwo'] }));
  const changed = requireFunction('changeSkillDraft')(opened, { name: 'Renamed' });
  assert.deepEqual(requireFunction('prepareSkillSave')(changed, mutation).request.exampleInputs, ['', 'one\ntwo']);
});

test('Skills page shows meaningful empty and loading states', () => {
  assert.match(render({ listStatus: 'loading', catalog: null, editor: null }), /Loading skills/);
  assert.match(render({ listStatus: 'ready', catalog: { items: [], nextCursor: null }, editor: null }), /No saved skills/);
});

test('Skills page exposes actual current revisions and cursor controls', () => {
  const markup = render({ listStatus: 'ready', catalog: { items: [summary()], nextCursor: id }, editor: null });
  assert.match(markup, /Engineering/); assert.match(markup, /Revision 2/); assert.match(markup, /Open/); assert.match(markup, /Load more/);
});

test('editor labels source fields and uses actual tool reference checkboxes', () => {
  const markup = render({ listStatus: 'ready', catalog: { items: [], nextCursor: null }, editor: requireFunction('openSkillDraft')(skill()), tools: [tool()], toolStatus: 'ready' });
  for (const label of ['Name', 'Description', 'Instructions', 'Example inputs', 'Tool references', 'Save skill', 'Reload saved version']) assert.ok(markup.includes(label), label);
  assert.match(markup, /list_documents/); assert.match(markup, /type="checkbox"/); assert.match(markup, /Revision 2/);
});

test('stored content is escaped and unavailable references remain visible', () => {
  const row = skill({ name: '<script>alert(1)</script>', instructions: '<img src=x onerror=alert(1)>', toolNames: ['read_note'], unavailableTools: ['read_note'] });
  const markup = render({ listStatus: 'ready', catalog: { items: [summary({ name: row.name })], nextCursor: null }, editor: requireFunction('openSkillDraft')(row), tools: [], toolStatus: 'ready' });
  assert.ok(!markup.includes('<script>')); assert.ok(!markup.includes('<img src=x')); assert.match(markup, /Unavailable/); assert.match(markup, /read_note/);
});

test('save conflict preserves the draft and provides reload recovery', () => {
  const error = new (requireFunction('SkillError'))('conflict');
  const markup = render({ listStatus: 'ready', catalog: { items: [], nextCursor: null }, editor: requireFunction('openSkillDraft')(skill({ instructions: 'My unsaved edit' })), error, toolStatus: 'ready', tools: [] });
  assert.match(markup, /My unsaved edit/); assert.match(markup, /Reload saved version/); assert.match(markup, /role="alert"/);
});

test('saving disables editing and tool failures offer retry safely', () => {
  const markup = render({ listStatus: 'ready', catalog: { items: [], nextCursor: null }, editor: requireFunction('openSkillDraft')(skill()), busy: 'saving', tools: [], toolStatus: 'error' });
  assert.match(markup, /Saving/); assert.match(markup, /disabled/); assert.match(markup, /Retry tool references/);
});

test('valid Unicode definitions use Python character bounds', async () => {
  const row = skill({ name: '😀'.repeat(150), instructions: '😀'.repeat(50001), exampleInputs: ['😀'.repeat(2000)] });
  globalThis.fetch = async () => json(row);
  assert.deepEqual(await requireFunction('fetchSkill')(id, signal()), row);
});

test('pending tool metadata does not call saved references unavailable', () => {
  const markup = render({ listStatus: 'ready', catalog: { items: [], nextCursor: null }, editor: requireFunction('openSkillDraft')(skill()), tools: [], toolStatus: 'loading' });
  assert.ok(!markup.includes('Unavailable')); assert.match(markup, /list_documents/);
});

test('browser text controls admit the full valid Unicode range', () => {
  const markup = render({ listStatus: 'ready', catalog: { items: [], nextCursor: null }, editor: requireFunction('openSkillDraft')(skill()), toolStatus: 'ready', tools: [] });
  for (const [label, minimum] of [['Name', 400], ['Description', 4000], ['Instructions', 200000]]) {
    const tag = markup.match(new RegExp(`${label}<(?:input|textarea)[^>]*>`))[0];
    const bound = tag.match(/maxlength="(\d+)"/i);
    assert.ok(!bound || Number(bound[1]) >= minimum, `${label} must permit valid Unicode characters`);
  }
});

test('tool insertion replaces the UTF-16 selection and preserves the draft fields', () => {
  const fields = requireFunction('openSkillDraft')(skill({ instructions: '😀 before and after' })).fields;
  const inserted = requireFunction('insertSkillTool')(fields, [tool()], 'list_documents', 10, 13);
  assert.equal(inserted.fields.instructions, '😀 before /list_documents after');
  assert.equal(inserted.caret, 25);
  assert.deepEqual(inserted.fields.toolNames, ['list_documents']);
  assert.deepEqual(inserted.fields.exampleInputs, ['Compare power']);
  assert.equal(fields.instructions, '😀 before and after');
  inserted.fields.exampleInputs.push('Later');
  assert.deepEqual(fields.exampleInputs, ['Compare power']);
});

test('tool insertion rejects unregistered names and an oversized document without changing it', () => {
  const insert = requireFunction('insertSkillTool');
  const fields = requireFunction('openSkillDraft')(skill()).fields;
  for (const name of ['invented_tool', '../list_documents']) {
    assert.throws(() => insert(fields, [tool()], name, 0, 0), error => error.code === 'invalid_input');
  }
  const full = { ...fields, instructions: 'x'.repeat(100000) };
  assert.throws(() => insert(full, [tool()], 'list_documents', 0, 0), error => error.code === 'invalid_input');
  assert.equal(full.instructions.length, 100000);
  assert.deepEqual(fields.toolNames, ['list_documents']);
});

async function mountedEditor(row, tools = [tool()]) {
  const host = mountedHost();
  const { createRoot } = await import('react-dom/client');
  const root = createRoot(host.container);
  let current;
  function Harness() {
    const [draft, setDraft] = React.useState(() => client.openSkillDraft(row));
    current = draft;
    return React.createElement(view.SkillsView, {
      listStatus: 'ready', catalog: { items: [], nextCursor: null }, editor: draft, tools, toolStatus: 'ready',
      onChange: changes => setDraft(previous => client.changeSkillDraft(previous, changes)),
    });
  }
  try { await React.act(async () => root.render(React.createElement(Harness))); }
  catch (error) { try { await React.act(async () => root.unmount()); } finally { host.restore(); } throw error; }
  const instructions = host.elements('textarea').find(element => element.parentNode?.textContent.startsWith('Instructions'));
  assert.ok(instructions, 'Instructions control remains labelled');
  instructions.focus = () => { instructions.ownerDocument.activeElement = instructions; };
  instructions.setSelectionRange = (start, end) => { instructions.selectionStart = start; instructions.selectionEnd = end; };
  return {
    host, instructions, current: () => current,
    async close() { try { await React.act(async () => root.unmount()); } finally { host.restore(); } },
  };
}

test('the tool sidebar inserts at the caret and the next save retains its revision contract', async () => {
  const mounted = await mountedEditor(skill({ instructions: '😀 before and after', toolNames: [] }));
  try {
    mounted.instructions.setSelectionRange(10, 13); mounted.instructions.focus();
    const insert = mounted.host.elements('button').find(element => element.getAttribute('aria-label') === 'Insert /list_documents');
    assert.ok(insert, 'A registered tool can be inserted from the sidebar');
    await React.act(async () => insert.dispatchEvent({ type: 'click', bubbles: true, button: 0 }));
    assert.equal(mounted.instructions.value, '😀 before /list_documents after');
    assert.equal(mounted.instructions.selectionStart, 25);
    assert.equal(mounted.instructions.selectionEnd, 25);
    assert.equal(mounted.instructions.ownerDocument.activeElement, mounted.instructions);
    assert.equal(mounted.current().dirty, true);
    const saved = client.prepareSkillSave(mounted.current(), mutation);
    assert.equal(saved.request.id, id); assert.equal(saved.request.expectedRevision, 2);
    assert.equal(saved.request.mutationId, mutation); assert.deepEqual(saved.request.toolNames, ['list_documents']);
    assert.deepEqual(client.prepareSkillSave(saved.draft, nextMutation).request, saved.request);
  } finally { await mounted.close(); }
});

test('inserting an identical selected tool reference still collapses the selection', async () => {
  const original = '/list_documents';
  const mounted = await mountedEditor(skill({ instructions: original }));
  try {
    mounted.instructions.setSelectionRange(0, original.length); mounted.instructions.focus();
    const insert = mounted.host.elements('button').find(element => element.getAttribute('aria-label') === 'Insert /list_documents');
    assert.ok(insert);
    await React.act(async () => insert.dispatchEvent({ type: 'click', bubbles: true, button: 0 }));
    assert.equal(mounted.instructions.value, original);
    assert.equal(mounted.instructions.selectionStart, original.length);
    assert.equal(mounted.instructions.selectionEnd, original.length);
    assert.deepEqual(mounted.current().fields.toolNames, ['list_documents']);
  } finally { await mounted.close(); }
});

test('inserting a template preserves selected unsaved instructions and existing examples', async () => {
  const original = 'Keep this generated instruction.';
  const mounted = await mountedEditor(skill({ instructions: original, exampleInputs: ['', 'one\ntwo'] }));
  try {
    mounted.instructions.setSelectionRange(0, original.length); mounted.instructions.focus();
    const insert = mounted.host.elements('button').find(element => element.textContent === 'Insert template');
    assert.ok(insert, 'The document editor offers template insertion');
    await React.act(async () => insert.dispatchEvent({ type: 'click', bubbles: true, button: 0 }));
    assert.notEqual(mounted.instructions.value, original);
    assert.ok(mounted.instructions.value.includes(original), 'Template insertion must not replace selected work');
    assert.equal(mounted.current().fields.description, 'Generated example');
    assert.deepEqual(mounted.current().fields.exampleInputs, ['', 'one\ntwo']);
    assert.deepEqual(mounted.current().fields.toolNames, ['list_documents']);
    assert.equal(mounted.current().dirty, true);
  } finally { await mounted.close(); }
});

test('tool search uses real catalog purpose and parameters without changing the document selection', async () => {
  const described = { ...tool(), inputSchema: { type: 'object', properties: { query: { type: 'string', description: 'Words to look for' } }, required: ['query'] } };
  const other = { ...tool(), name: 'read_note', description: 'Read generated notes' };
  const mounted = await mountedEditor(skill({ instructions: 'Keep my document.' }), [described, other]);
  try {
    mounted.instructions.setSelectionRange(5, 7);
    const sidebar = mounted.host.elements('aside')[0];
    assert.ok(sidebar, 'Registered tools have a searchable sidebar');
    assert.ok(sidebar.textContent.includes('Words to look for'));
    assert.ok(sidebar.textContent.includes('query'));
    assert.ok(sidebar.textContent.includes('string'));
    const search = mounted.host.elements('input').find(element => element.getAttribute('aria-label') === 'Search tools');
    assert.ok(search); search.value = '  NOTES  ';
    await React.act(async () => search.dispatchEvent({ type: 'input', bubbles: true }));
    const insertions = mounted.host.elements('button').filter(element => element.getAttribute('aria-label')?.startsWith('Insert /'));
    assert.equal(insertions.length, 1); assert.equal(insertions[0].getAttribute('aria-label'), 'Insert /read_note');
    assert.equal(mounted.instructions.value, 'Keep my document.');
    assert.equal(mounted.instructions.selectionStart, 5); assert.equal(mounted.instructions.selectionEnd, 7);
    assert.deepEqual(mounted.current().fields.toolNames, ['list_documents']);
    assert.equal(mounted.current().dirty, false);
  } finally { await mounted.close(); }
});
