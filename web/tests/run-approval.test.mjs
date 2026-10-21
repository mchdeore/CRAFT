import test from "node:test";
import assert from "node:assert/strict";
import { statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "./typescript-loader.mjs";

// Only the two NEW, exactly absent paths may become missing-surface assertions.
// Existing dependency, syntax and module evaluation errors must propagate.
async function newModule(url) {
  try { statSync(url); }
  catch (error) {
    if (error?.code === "ENOENT" && error.path === fileURLToPath(url)) return {};
    throw error;
  }
  return import(url.href);
}
const library = await newModule(new URL("../src/lib/run-approval.ts", import.meta.url));
const component = await newModule(new URL("../src/components/run-approval-panel.tsx", import.meta.url));
const { createThreadSession } = await import("../src/lib/agent-history.ts");

const conversationId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const runId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const approvalId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const otherConversationId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const otherRunId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
const otherApprovalId = "ffffffff-ffff-4fff-8fff-ffffffffffff";
const nativeId = "generated-native-interrupt-id";
const requestedAt = "2026-10-17T12:00:00+00:00";
const rawFailure = "GENERATED_UNSAFE_HTTP_OR_NETWORK_BODY";
const tick = () => new Promise(resolve => setImmediate(resolve));
const flush = async () => { await tick(); await tick(); };
const clone = value => structuredClone(value);
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
};
const response = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json" },
});
const summary = (status = "pending", changes = {}) => ({
  id: approvalId, conversationId, runId, tool: "WriteClient", summary: "Generated write proposal",
  status, requestedAt, ...changes,
});
const detail = (status = "pending", changes = {}) => ({
  ...summary(status), arguments: { text: "GENERATED_ARGUMENT", nested: { count: 1, enabled: false } },
  evidence: [{ sourceId: "generated-source", part: 1 }], reasoning: "GENERATED_REASONING",
  retrievalPath: [{ operation: "GENERATED_READ", count: 1 }],
  events: [{ kind: "requested", actorId: null, at: requestedAt }], ...changes,
});
const recorded = (status = "paused", approvalStatus = "pending", changes = {}) => ({
  status, runId, startedAt: requestedAt, endedAt: requestedAt,
  approvalId: status === "paused" ? approvalId : null,
  approvalStatus: status === "paused" ? approvalStatus : null,
  canResume: status === "paused" && ["approved", "rejected"].includes(approvalStatus), ...changes,
});
const metadata = (status = "pending", changes = {}) => ({ phase: "ready", data: recorded("paused", status, changes) });
const idleMetadata = () => ({ phase: "ready", data: {
  status: "none", runId: null, startedAt: null, endedAt: null,
  approvalId: null, approvalStatus: null, canResume: false,
} });

function parse(value) {
  assert.equal(typeof library.parseApprovalDetail, "function", "owner approval detail parser is required");
  return library.parseApprovalDetail(value);
}
function controller(fetcher, callbacks = {}, ownerId = conversationId) {
  assert.equal(typeof library.RunApprovalController, "function", "current approval controller is required");
  const options = { onAuthInvalid() {}, onRecoveryRefresh() {}, async onResume() {}, ...callbacks };
  return fetcher === undefined
    ? new library.RunApprovalController(ownerId, options)
    : new library.RunApprovalController(ownerId, options, fetcher);
}
async function ready(fetcher = async () => response(detail()), callbacks = {}, status = "pending") {
  const state = controller(fetcher, callbacks);
  state.activate();
  state.setRecovery(metadata(status));
  await flush();
  assert.equal(state.getSnapshot().phase, "ready");
  return state;
}
function noActions(view) {
  assert.equal(view.canDecide, false);
  assert.equal(view.canResume, false);
}
function safeView(view) { assert.equal(JSON.stringify(view).includes(rawFailure), false); }
async function denied(action) {
  try { await action(); } catch { /* Refusal may reject or leave the safe snapshot unchanged. */ }
}
function render(view, callbacks = {}) {
  assert.equal(typeof component.RunApprovalPanel, "function", "owner approval panel is required");
  return renderToStaticMarkup(React.createElement(component.RunApprovalPanel, {
    view, onRefresh() {}, onDecide() {}, onResume() {}, ...callbacks,
  }));
}
const view = (status = "pending", changes = {}) => ({
  phase: "ready", data: detail(status), busy: null,
  canDecide: status === "pending", canResume: ["approved", "rejected"].includes(status), ...changes,
});
const buttons = markup => [...markup.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)]
  .map(match => ({ attributes: match[1], text: match[2].replace(/<[^>]*>/g, "") }));
function disabledActions(markup) {
  for (const button of buttons(markup).filter(value => /^(Approve|Reject|Continue)/i.test(value.text.trim()))) {
    assert.match(button.attributes, /\bdisabled\b/);
  }
}

// Static matrix: 5 cases.
for (const status of ["pending", "approved", "rejected", "executed", "failed"]) test(`owner detail projects canonical ${status} status`, () => {
  const value = detail(status);
  assert.deepEqual(parse(value), value);
});
// Static matrix: 5 cases.
for (const kind of ["requested", "approved", "rejected", "executed", "failed"]) test(`owner event validates canonical ${kind} kind`, () => {
  const value = detail("pending", { events: [{ kind, actorId: otherConversationId, at: requestedAt, note: null }] });
  assert.deepEqual(parse(value), value);
});
test("owner detail requires every own wire field and rejects malformed typed fields", () => {
  const valid = detail();
  for (const key of Object.keys(valid)) {
    const value = { ...valid }; delete value[key]; assert.throws(() => parse(value));
  }
  for (const value of [null, [], true, {}, Object.create(valid)]) assert.throws(() => parse(value));
  const malformed = [
    { id: "bad" }, { conversationId: "bad" }, { runId: "bad" }, { status: "unknown" },
    { requestedAt: "bad" }, { requestedAt: "2026-10-17" }, { tool: 1 }, { summary: null },
    { arguments: null }, { arguments: [] }, { evidence: {} }, { evidence: [null] },
    { evidence: [[]] }, { reasoning: null }, { retrievalPath: {} }, { retrievalPath: [false] },
    { events: {} }, { events: [null] }, { events: [{ kind: "other", actorId: null, at: requestedAt }] },
    { events: [{ kind: "requested", at: requestedAt }] },
    { events: [{ kind: "requested", actorId: "bad", at: requestedAt }] },
    { events: [{ kind: "requested", actorId: null, at: "bad" }] },
    { events: [{ kind: "requested", actorId: null, at: requestedAt, note: 1 }] },
  ];
  for (const value of malformed) assert.throws(() => parse({ ...valid, ...value }));
});
test("detail projects declared owner fields and discards unknown privilege or native envelope fields", () => {
  const value = detail();
  assert.deepEqual(parse({ ...value, approved: true, canResume: true, state: { native: rawFailure },
    metadata: { privilege: rawFailure }, events: [{ ...value.events[0], hiddenNative: rawFailure }] }), value);
});
test("detail JSON values stay data and reject non-JSON values without exposing them", () => {
  const value = detail("pending", { arguments: { nullValue: null, array: [1, true, "<script>", { x: false }] } });
  assert.deepEqual(parse(value), value);
  const cyclic = {}; cyclic.self = cyclic;
  for (const argument of [cyclic, { x: undefined }, { x: () => {} }, { x: NaN }, { x: Infinity }, { x: 1n }]) {
    assert.throws(() => parse(detail("pending", { arguments: argument })));
  }
  assert.throws(() => parse({ ...detail(), reasoning: { message: rawFailure } }), error => !String(error).includes(rawFailure));
});
test("parser projects a detached JSON detail rather than retaining unknown owner response keys", () => {
  const wire = detail(); const parsed = parse(wire);
  assert.deepEqual(parsed, wire);
  wire.extra = rawFailure;
  assert.equal(Object.hasOwn(parsed, "extra"), false);
});

test("inactive controller has stable safe snapshots and dispatches no fetch or actions", async () => {
  let reads = 0, resumes = 0, refreshes = 0;
  const state = controller(async () => { reads++; return response(detail()); }, {
    onRecoveryRefresh() { refreshes++; }, onResume() { resumes++; },
  });
  try {
    assert.equal(state.getSnapshot(), state.getSnapshot());
    assert.equal(state.getSnapshot().phase, "idle"); assert.equal(state.getSnapshot().data, null);
    assert.equal(state.getSnapshot().busy, null); noActions(state.getSnapshot());
    state.setRecovery(metadata()); await state.refresh(); await denied(() => state.decide(true)); await denied(() => state.resume());
    assert.equal(reads, 0); assert.equal(resumes, 0); assert.equal(refreshes, 0);
  } finally { state.release(); }
});
// Static matrix: 2 cases.
for (const order of ["activate-first", "metadata-first"]) test(`fresh ${order} lifecycle reads detail exactly once`, async () => {
  const calls = []; const state = controller(async (url, options) => { calls.push({ url, options }); return response(detail()); });
  try {
    if (order === "activate-first") { state.activate(); state.setRecovery(metadata()); }
    else { state.setRecovery(metadata()); assert.equal(calls.length, 0); state.activate(); }
    await flush();
    assert.equal(calls.length, 1); assert.equal(calls[0].url, `/api/approvals/${approvalId}`);
    assert.equal(calls[0].options.method, "GET"); assert.equal(calls[0].options.credentials, "same-origin");
    assert.equal(calls[0].options.signal instanceof AbortSignal, true);
    assert.equal(state.getSnapshot().phase, "ready"); assert.deepEqual(state.getSnapshot().data, detail());
    assert.equal(state.getSnapshot().canDecide, true); assert.equal(state.getSnapshot().canResume, false);
    state.activate(); state.setRecovery(clone(metadata())); await flush(); assert.equal(calls.length, 1);
  } finally { state.release(); }
});
test("bound subscriptions receive stable changed snapshots and unsubscribe cleanly", async () => {
  const wait = deferred(); const state = controller(() => wait.promise);
  const snapshots = []; const get = state.getSnapshot; const subscribe = state.subscribe;
  const unsubscribe = subscribe(() => snapshots.push(get()));
  try {
    state.activate(); state.setRecovery(metadata()); assert.equal(get().phase, "loading"); noActions(get());
    assert.equal(get(), get()); wait.resolve(response(detail())); await flush();
    assert.equal(get().phase, "ready"); assert.ok(snapshots.length >= 2);
    assert.equal(snapshots.at(-1), get());
    unsubscribe(); const before = snapshots.length; await state.refresh(); assert.equal(snapshots.length, before);
  } finally { unsubscribe(); state.release(); }
});
test("default native fetch is called without the controller as receiver for reads and boolean decisions", async () => {
  const original = globalThis.fetch; const calls = [];
  globalThis.fetch = function (url, options) {
    assert.ok(this === undefined || this === globalThis, "native host fetch receiver must stay detached");
    calls.push({ url, options }); return Promise.resolve(response(options.method === "POST" ? summary("approved") : detail()));
  };
  let state;
  try {
    state = controller(undefined); state.activate(); state.setRecovery(metadata()); await flush();
    assert.equal(state.getSnapshot().phase, "ready"); await state.decide(true);
    assert.equal(calls.length, 2); assert.equal(calls[0].options.credentials, "same-origin");
    assert.equal(calls[1].options.credentials, "same-origin"); assert.deepEqual(JSON.parse(calls[1].options.body), { approve: true });
  } finally { state?.release(); globalThis.fetch = original; }
});
test("injected native fetch is also called detached", async () => {
  const fetcher = function (_url, options) {
    assert.equal(this, undefined); return Promise.resolve(response(options.method === "POST" ? summary("rejected") : detail()));
  };
  const state = await ready(fetcher);
  try { await state.decide(false); } finally { state.release(); }
});
test("repeated identical ready metadata and settled reads never poll or duplicate owner detail requests", async () => {
  let calls = 0; const state = await ready(async () => { calls++; return response(detail()); });
  try {
    for (let i = 0; i < 3; i++) state.setRecovery(clone(metadata()));
    await flush(); assert.equal(calls, 1);
    await state.refresh(); assert.equal(calls, 2); await flush(); assert.equal(calls, 2);
  } finally { state.release(); }
});
test("metadata loading, null, unbound, nonpaused and malformed contexts clear old detail and actions", async () => {
  const invalidContexts = [
    { phase: "idle", data: null }, { phase: "loading", data: recorded() }, { phase: "error", data: recorded() },
    { phase: "ready", data: null }, idleMetadata(),
    { phase: "ready", data: recorded("running") }, { phase: "ready", data: recorded("finished") },
    metadata("pending", { approvalId: null, approvalStatus: null, canResume: false }),
    metadata("pending", { runId: null }), metadata("pending", { runId: "bad" }),
    metadata("pending", { approvalId: "bad" }), metadata("pending", { approvalStatus: "unknown" }),
    metadata("pending", { canResume: "true" }), metadata("pending", { canResume: true }),
  ];
  for (const context of invalidContexts) {
    let calls = 0; const state = await ready(async () => { calls++; return response(detail()); });
    try {
      state.setRecovery(context); assert.equal(state.getSnapshot().data, null); noActions(state.getSnapshot());
      await state.refresh(); await denied(() => state.decide(true)); await denied(() => state.resume());
      assert.equal(calls, 1);
    } finally { state.release(); }
  }
});
// Static matrix: 3 cases.
for (const identity of ["id", "conversationId", "runId"]) test(`owner ${identity} mismatch cannot expose detail or dispatch`, async () => {
  const wrong = { id: otherApprovalId, conversationId: otherConversationId, runId: otherRunId };
  const calls = []; let resumed = 0;
  const state = controller(async (_url, options) => { calls.push(options); return response(detail("pending", { [identity]: wrong[identity] })); }, { onResume() { resumed++; } });
  try {
    state.activate(); state.setRecovery(metadata()); await flush(); assert.equal(state.getSnapshot().phase, "error");
    assert.equal(state.getSnapshot().data, null); noActions(state.getSnapshot());
    await denied(() => state.decide(true)); await denied(() => state.resume());
    assert.equal(calls.length, 1); assert.equal(resumed, 0);
  } finally { state.release(); }
});
// Static matrix: 4 cases.
for (const pair of [["pending", "approved"], ["approved", "pending"], ["rejected", "approved"], ["approved", "rejected"]]) test(`detail ${pair[1]} cannot authorize metadata ${pair[0]}`, async () => {
  let posts = 0, resumes = 0;
  const state = controller(async (_url, options) => { if (options.method === "POST") posts++; return response(detail(pair[1])); }, { onResume() { resumes++; } });
  try {
    state.activate(); state.setRecovery(metadata(pair[0])); await flush(); noActions(state.getSnapshot());
    await denied(() => state.decide(true)); await denied(() => state.resume()); assert.equal(posts, 0); assert.equal(resumes, 0);
  } finally { state.release(); }
});
test("changed run and approval IDs abort old reads and accept only the new linked detail", async () => {
  const first = deferred(); const calls = [];
  const fresh = detail("pending", { id: otherApprovalId, runId: otherRunId });
  const state = controller(async (url, options) => { calls.push({ url, options }); return calls.length === 1 ? first.promise : response(fresh); });
  try {
    state.activate(); state.setRecovery(metadata());
    state.setRecovery(metadata("pending", { runId: otherRunId, approvalId: otherApprovalId }));
    assert.equal(calls[0].options.signal.aborted, true); assert.equal(state.getSnapshot().data, null);
    await flush(); assert.deepEqual(state.getSnapshot().data, fresh); assert.equal(calls[1].url, `/api/approvals/${otherApprovalId}`);
    let bodyReads = 0; first.resolve({ ok: true, status: 200, json() { bodyReads++; return detail(); } }); await flush();
    assert.equal(bodyReads, 0); assert.deepEqual(state.getSnapshot().data, fresh);
  } finally { state.release(); }
});
test("metadata loading immediately aborts a pending read and cannot be revived by late JSON", async () => {
  const json = deferred(); let signal;
  const state = controller(async (_url, options) => { signal = options.signal; return { ok: true, status: 200, json: () => json.promise }; });
  try {
    state.activate(); state.setRecovery(metadata()); await flush();
    state.setRecovery({ phase: "loading", data: recorded() }); assert.equal(signal.aborted, true);
    assert.equal(state.getSnapshot().data, null); noActions(state.getSnapshot());
    json.resolve(detail()); await flush(); assert.equal(state.getSnapshot().data, null); noActions(state.getSnapshot());
  } finally { state.release(); }
});
test("latest manual detail refresh wins and aborts the earlier response", async () => {
  const first = deferred(); const calls = [];
  const fresh = detail("pending", { summary: "Generated fresh proposal" });
  const state = controller(async (_url, options) => { calls.push(options); return calls.length === 1 ? first.promise : response(fresh); });
  try {
    state.activate(); state.setRecovery(metadata()); await state.refresh(); assert.equal(calls[0].signal.aborted, true);
    first.resolve(response(detail())); await flush(); assert.deepEqual(state.getSnapshot().data, fresh);
  } finally { state.release(); }
});
test("a superseded network rejection cannot replace current owner detail or emit callbacks", async () => {
  const first = deferred(); let calls = 0, callbacks = 0;
  const state = controller(async () => ++calls === 1 ? first.promise : response(detail()), {
    onAuthInvalid() { callbacks++; }, onRecoveryRefresh() { callbacks++; }, onResume() { callbacks++; },
  });
  try {
    state.activate(); state.setRecovery(metadata()); await state.refresh(); first.reject(new Error(rawFailure)); await flush();
    assert.equal(state.getSnapshot().phase, "ready"); safeView(state.getSnapshot()); assert.equal(callbacks, 0);
  } finally { state.release(); }
});
// Static matrix: 5 cases.
for (const status of [401, 403, 404, 409, 500]) test(`current owner detail HTTP ${status} uses a safe refreshable approval boundary`, async () => {
  let auth = 0, invalidConversation = 0, bodyReads = 0, resumes = 0;
  const state = controller(async () => ({ ok: false, status, json() { bodyReads++; throw new Error(rawFailure); }, text() { bodyReads++; return rawFailure; } }), {
    onAuthInvalid() { auth++; }, onSessionInvalid() { invalidConversation++; }, onResume() { resumes++; },
  });
  try {
    state.activate(); state.setRecovery(metadata()); await flush(); assert.equal(state.getSnapshot().phase, "error");
    assert.equal(state.getSnapshot().data, null); noActions(state.getSnapshot()); safeView(state.getSnapshot());
    assert.equal(auth, status === 401 ? 1 : 0); assert.equal(invalidConversation, 0); assert.equal(bodyReads, 0);
    await denied(() => state.resume()); assert.equal(resumes, 0);
  } finally { state.release(); }
});
// Static matrix: 3 cases.
for (const status of [401, 404, 409]) test(`late owner detail HTTP ${status} cannot change a reactivated current context`, async () => {
  const first = deferred(); let calls = 0, callbacks = 0, bodyReads = 0;
  const state = controller(async () => ++calls === 1 ? first.promise : response(detail("pending", { id: otherApprovalId, runId: otherRunId })), {
    onAuthInvalid() { callbacks++; }, onSessionInvalid() { callbacks++; }, onRecoveryRefresh() { callbacks++; },
  });
  try {
    state.activate(); state.setRecovery(metadata()); state.release(); state.activate();
    state.setRecovery(metadata("pending", { approvalId: otherApprovalId, runId: otherRunId })); await flush();
    first.resolve({ ok: false, status, json() { bodyReads++; return { message: rawFailure }; } }); await flush();
    assert.equal(callbacks, 0); assert.equal(bodyReads, 0); assert.equal(state.getSnapshot().data.id, otherApprovalId);
  } finally { state.release(); }
});
test("late owner JSON and JSON rejection cannot cross release or replace the next detail", async () => {
  for (const rejected of [false, true]) {
    const json = deferred(); let calls = 0, callbacks = 0;
    const fresh = detail("pending", { id: otherApprovalId, runId: otherRunId });
    const state = controller(async () => ++calls === 1 ? { ok: true, status: 200, json: () => json.promise } : response(fresh), {
      onAuthInvalid() { callbacks++; }, onRecoveryRefresh() { callbacks++; },
    });
    try {
      state.activate(); state.setRecovery(metadata()); await flush(); state.release(); state.activate();
      state.setRecovery(metadata("pending", { approvalId: otherApprovalId, runId: otherRunId })); await flush();
      if (rejected) json.reject(new Error(rawFailure)); else json.resolve(detail()); await flush();
      assert.deepEqual(state.getSnapshot().data, fresh); safeView(state.getSnapshot()); assert.equal(callbacks, 0);
    } finally { state.release(); }
  }
});
// Static matrix: 2 cases.
for (const order of ["activate-first", "metadata-first"]) test(`StrictMode ${order} resupply discards old context and accepts only fresh JSON`, async () => {
  const first = deferred(); const calls = [];
  const state = controller(async (_url, options) => { calls.push(options); return calls.length === 1 ? first.promise : response(detail()); });
  try {
    state.activate(); state.setRecovery(metadata()); state.release();
    assert.equal(calls[0].signal.aborted, true); assert.equal(state.getSnapshot().data, null); noActions(state.getSnapshot());
    state.activate(); await flush(); assert.equal(calls.length, 1); state.release();
    if (order === "activate-first") { state.activate(); state.setRecovery(metadata()); }
    else { state.setRecovery(metadata()); state.activate(); }
    await flush(); assert.equal(calls.length, 2);
    first.resolve(response(detail("approved"))); await flush(); assert.deepEqual(state.getSnapshot().data, detail());
  } finally { state.release(); }
});
test("malformed JSON and network errors expose generic text with a manual retry", async () => {
  let calls = 0;
  const state = controller(async () => {
    calls++; if (calls === 1) throw new Error(rawFailure);
    if (calls === 2) return { ok: true, status: 200, json() { throw new Error(rawFailure); } };
    if (calls === 3) return response({ ...detail(), arguments: null });
    return response(detail());
  });
  try {
    state.activate(); state.setRecovery(metadata()); await flush();
    assert.equal(state.getSnapshot().phase, "error"); safeView(state.getSnapshot());
    await state.refresh(); assert.equal(state.getSnapshot().phase, "error"); safeView(state.getSnapshot());
    await state.refresh(); assert.equal(state.getSnapshot().phase, "error"); await state.refresh();
    assert.equal(state.getSnapshot().phase, "ready"); assert.equal(calls, 4);
  } finally { state.release(); }
});

// Static matrix: 2 cases.
for (const approve of [true, false]) test(`explicit boolean ${approve} decision continues once after fresh verification`, async () => {
  const wait = deferred(); const calls = []; const resumed = []; let refreshes = 0, wireStatus = "pending";
  const state = await ready(async (url, options) => {
    calls.push({ url, options }); return options.method === "POST" ? wait.promise : response(detail(wireStatus));
  }, { onRecoveryRefresh() { refreshes++; }, async onResume(...ids) { resumed.push(ids); } });
  try {
    assert.equal(calls.filter(call => call.options.method === "POST").length, 0); assert.deepEqual(resumed, []);
    const decision = state.decide(approve); assert.equal(state.getSnapshot().busy, "decision"); noActions(state.getSnapshot());
    await denied(() => state.decide(!approve)); await denied(() => state.resume());
    const posts = calls.filter(call => call.options.method === "POST"); assert.equal(posts.length, 1);
    assert.equal(posts[0].url, `/api/approvals/${approvalId}/decision`);
    assert.equal(posts[0].options.credentials, "same-origin"); assert.equal(posts[0].options.signal instanceof AbortSignal, true);
    assert.equal(new Headers(posts[0].options.headers).get("content-type"), "application/json");
    assert.deepEqual(JSON.parse(posts[0].options.body), { approve });
    wireStatus = approve ? "approved" : "rejected";
    wait.resolve(response({ ...summary(wireStatus), state: { native: rawFailure }, approved: approve })); await decision;
    assert.equal(refreshes, 1); assert.deepEqual(resumed, []); noActions(state.getSnapshot()); safeView(state.getSnapshot());
    await denied(() => state.decide(approve)); await denied(() => state.resume());
    assert.equal(calls.filter(call => call.options.method === "POST").length, 1);
    // Detail alone after a decision cannot replace stale pending metadata.
    await state.refresh(); noActions(state.getSnapshot()); await denied(() => state.resume()); assert.deepEqual(resumed, []);
    // Once the recorded decision is freshly verified, the run continues without a second click, exactly once.
    state.setRecovery(metadata(wireStatus)); await flush(); assert.deepEqual(resumed, [[runId, approvalId]]);
    assert.equal(state.getSnapshot().canDecide, false); assert.equal(state.getSnapshot().canResume, false);
    await denied(() => state.resume()); assert.equal(resumed.length, 1);
  } finally { state.release(); }
});
test("decision rejects truthy nonbooleans before making a POST", async () => {
  let posts = 0;
  const state = await ready(async (_url, options) => { if (options.method === "POST") posts++; return response(detail()); });
  try {
    for (const approve of ["true", 1, null, undefined, {}, []]) await denied(() => state.decide(approve));
    assert.equal(posts, 0);
  } finally { state.release(); }
});
// Static matrix: 4 cases.
for (const status of ["approved", "rejected", "executed", "failed"]) test(`${status} detail cannot send another decision`, async () => {
  let posts = 0;
  const state = await ready(async (_url, options) => { if (options.method === "POST") posts++; return response(detail(status)); }, {}, status);
  try { assert.equal(state.getSnapshot().canDecide, false); await denied(() => state.decide(true)); assert.equal(posts, 0); }
  finally { state.release(); }
});
test("canonical decision summary is validated for owner, run, approval, status and required types", async () => {
  const invalid = [
    { id: otherApprovalId }, { conversationId: otherConversationId }, { runId: otherRunId },
    { status: "pending" }, { status: "rejected" }, { status: "unknown" }, { tool: null },
    { summary: 1 }, { requestedAt: "bad" },
  ];
  for (const change of invalid) {
    let refreshes = 0, resumes = 0;
    const state = await ready(async (_url, options) => response(options.method === "POST" ? summary("approved", change) : detail()), {
      onRecoveryRefresh() { refreshes++; }, onResume() { resumes++; },
    });
    try {
      await state.decide(true); assert.equal(state.getSnapshot().phase, "error"); noActions(state.getSnapshot());
      assert.equal(refreshes, 0); await denied(() => state.resume()); assert.equal(resumes, 0);
    } finally { state.release(); }
  }
});
// Static matrix: 4 cases.
for (const status of [401, 404, 409, 500]) test(`current decision HTTP ${status} is generic and cannot delete the conversation or resume`, async () => {
  let auth = 0, invalidConversation = 0, reads = 0, resumes = 0;
  const state = await ready(async (_url, options) => options.method === "GET" ? response(detail()) : {
    ok: false, status, json() { reads++; throw new Error(rawFailure); }, text() { reads++; return rawFailure; },
  }, { onAuthInvalid() { auth++; }, onSessionInvalid() { invalidConversation++; }, onResume() { resumes++; } });
  try {
    await state.decide(true); assert.equal(state.getSnapshot().phase, "error"); noActions(state.getSnapshot()); safeView(state.getSnapshot());
    assert.equal(auth, status === 401 ? 1 : 0); assert.equal(invalidConversation, 0); assert.equal(reads, 0);
    await denied(() => state.resume()); assert.equal(resumes, 0);
  } finally { state.release(); }
});
// Static matrix: 2 cases.
for (const status of [401, 409]) test(`late decision HTTP ${status} after release emits no auth or recovery callback`, async () => {
  const wait = deferred(); let callbacks = 0, signal;
  const state = await ready(async (_url, options) => {
    if (options.method === "GET") return response(detail()); signal = options.signal; return wait.promise;
  }, { onAuthInvalid() { callbacks++; }, onRecoveryRefresh() { callbacks++; }, onResume() { callbacks++; } });
  const decision = state.decide(true); state.release(); assert.equal(signal.aborted, true);
  wait.resolve(response({ message: rawFailure }, status)); await decision;
  assert.equal(callbacks, 0); assert.equal(state.getSnapshot().data, null); assert.equal(state.getSnapshot().busy, null);
  noActions(state.getSnapshot()); safeView(state.getSnapshot());
});
test("late canonical decision JSON or JSON failure cannot authorize the reactivated context", async () => {
  for (const reject of [false, true]) {
    const json = deferred(); let callbacks = 0;
    const state = await ready(async (_url, options) => options.method === "GET" ? response(detail()) : { ok: true, status: 200, json: () => json.promise }, {
      onAuthInvalid() { callbacks++; }, onRecoveryRefresh() { callbacks++; }, onResume() { callbacks++; },
    });
    try {
      const decision = state.decide(true); await flush(); state.release(); state.activate(); state.setRecovery(metadata()); await flush();
      if (reject) json.reject(new Error(rawFailure)); else json.resolve(summary("approved")); await decision;
      assert.equal(callbacks, 0); assert.deepEqual(state.getSnapshot().data, detail()); assert.equal(state.getSnapshot().canResume, false);
    } finally { state.release(); }
  }
});
test("changed metadata while a decision is pending aborts it and prevents stale refresh callbacks", async () => {
  const wait = deferred(); let refreshes = 0, signal;
  const state = await ready(async (_url, options) => {
    if (options.method === "POST") { signal = options.signal; return wait.promise; }
    return response(detail());
  }, { onRecoveryRefresh() { refreshes++; } });
  try {
    const decision = state.decide(true); state.setRecovery({ phase: "loading", data: null });
    assert.equal(signal.aborted, true); assert.equal(state.getSnapshot().busy, null); noActions(state.getSnapshot());
    wait.resolve(response(summary("approved"))); await decision; assert.equal(refreshes, 0); assert.equal(state.getSnapshot().data, null);
  } finally { state.release(); }
});
test("decision network failure stays generic and retries only after a fresh manual read", async () => {
  let posts = 0, refreshes = 0;
  const state = await ready(async (_url, options) => {
    if (options.method === "GET") return response(detail());
    if (++posts === 1) throw new Error(rawFailure); return response(summary("approved"));
  }, { onRecoveryRefresh() { refreshes++; } });
  try {
    await state.decide(true); assert.equal(state.getSnapshot().phase, "error"); safeView(state.getSnapshot());
    await flush(); assert.equal(posts, 1); await denied(() => state.decide(true)); assert.equal(posts, 1);
    await state.refresh(); await state.decide(true); assert.equal(posts, 2); assert.equal(refreshes, 1);
  } finally { state.release(); }
});

// Static matrix: 2 cases.
for (const status of ["approved", "rejected"]) test(`fresh matching ${status} continuation has a synchronous duplicate-action guard`, async () => {
  const wait = deferred(); const resumes = []; let posts = 0;
  const state = await ready(async (_url, options) => { if (options.method === "POST") posts++; return response(detail(status)); }, {
    onResume(...ids) { resumes.push(ids); return wait.promise; },
  }, status);
  try {
    assert.equal(state.getSnapshot().canResume, true); const resume = state.resume();
    assert.equal(state.getSnapshot().busy, "resume"); noActions(state.getSnapshot());
    await denied(() => state.resume()); await denied(() => state.decide(true));
    assert.deepEqual(resumes, [[runId, approvalId]]); assert.equal(posts, 0);
    wait.resolve(); await resume; assert.equal(state.getSnapshot().busy, null); noActions(state.getSnapshot());
    await denied(() => state.resume()); assert.equal(resumes.length, 1);
  } finally { state.release(); }
});
test("approved detail with canResume false cannot continue until fresh matching metadata and detail", async () => {
  let resumes = 0, calls = 0;
  const state = controller(async () => { calls++; return response(detail("approved")); }, { onResume() { resumes++; } });
  try {
    state.activate(); state.setRecovery(metadata("approved", { canResume: false })); await flush();
    assert.equal(state.getSnapshot().canResume, false); await denied(() => state.resume()); assert.equal(resumes, 0);
    state.setRecovery(metadata("approved")); assert.equal(state.getSnapshot().data, null); noActions(state.getSnapshot());
    await flush(); assert.equal(calls, 2); assert.equal(state.getSnapshot().canResume, true);
  } finally { state.release(); }
});
test("current continuation failure requests one metadata refresh and supports only manual recovery", async () => {
  let resumes = 0, refreshes = 0;
  const state = await ready(async () => response(detail("approved")), {
    onResume() { resumes++; return Promise.reject(new Error(rawFailure)); }, onRecoveryRefresh() { refreshes++; },
  }, "approved");
  try {
    await denied(() => state.resume()); assert.equal(resumes, 1); assert.equal(refreshes, 1);
    assert.equal(state.getSnapshot().phase, "error"); assert.equal(state.getSnapshot().busy, null); noActions(state.getSnapshot()); safeView(state.getSnapshot());
    await flush(); await denied(() => state.resume()); assert.equal(resumes, 1);
    await state.refresh(); assert.equal(state.getSnapshot().canResume, true);
  } finally { state.release(); }
});
// Static matrix: 2 cases.
for (const reject of [false, true]) test(`released continuation ${reject ? "failure" : "success"} cannot publish or refresh a new context`, async () => {
  const wait = deferred(); let callbacks = 0;
  const state = await ready(async () => response(detail("approved")), {
    onResume() { return wait.promise; }, onRecoveryRefresh() { callbacks++; }, onAuthInvalid() { callbacks++; },
  }, "approved");
  try {
    const resume = Promise.resolve(state.resume()).catch(() => {}); state.release(); state.activate();
    state.setRecovery(metadata("approved")); await flush(); const fresh = state.getSnapshot();
    if (reject) wait.reject(new Error(rawFailure)); else wait.resolve(); await resume;
    assert.equal(state.getSnapshot(), fresh); assert.equal(callbacks, 0); safeView(state.getSnapshot());
  } finally { state.release(); }
});
test("loading metadata during continuation invalidates its late error and recovery refresh", async () => {
  const wait = deferred(); let refreshes = 0;
  const state = await ready(async () => response(detail("approved")), {
    onResume() { return wait.promise; }, onRecoveryRefresh() { refreshes++; },
  }, "approved");
  try {
    const resume = Promise.resolve(state.resume()).catch(() => {}); state.setRecovery({ phase: "loading", data: null });
    assert.equal(state.getSnapshot().data, null); assert.equal(state.getSnapshot().busy, null); noActions(state.getSnapshot());
    wait.reject(new Error(rawFailure)); await resume; assert.equal(refreshes, 0); safeView(state.getSnapshot());
  } finally { state.release(); }
});

test("approval panel shows only current ready owner detail and explicit pending decisions", () => {
  let decisions = 0, resumes = 0;
  const markup = render(view(), { onDecide() { decisions++; }, onResume() { resumes++; } });
  assert.match(markup, /WriteClient/); assert.match(markup, /Generated write proposal/);
  assert.match(markup, /Approve/); assert.match(markup, /Reject/); assert.doesNotMatch(markup, /Continue run|Continue without this change/);
  assert.equal(buttons(markup).filter(button => /^(Approve|Reject)$/.test(button.text.trim())).length, 2);
  assert.equal(decisions, 0); assert.equal(resumes, 0);
});
test("approval details render escaped plain text and JSON for arguments, reasoning, evidence and retrieval", () => {
  const unsafe = "<img src=x onerror=generated()><script>GENERATED_SCRIPT</script>";
  const value = detail("pending", {
    summary: unsafe, arguments: { text: unsafe, nested: { approved: false } }, reasoning: unsafe,
    evidence: [{ title: unsafe }], retrievalPath: [{ operation: unsafe }],
    events: [{ kind: "requested", actorId: otherConversationId, at: requestedAt, note: "GENERATED_EVENT_NOTE" }],
  });
  const markup = render(view("pending", { data: parse(value) }));
  assert.match(markup, /&lt;img/); assert.match(markup, /&lt;script/); assert.match(markup, /requested/); assert.match(markup, /2026-10-17/);
  assert.match(markup, /nested/); assert.match(markup, /false/); assert.match(markup, /title/); assert.match(markup, /operation/);
  assert.doesNotMatch(markup, /<img\b|<script\b/); assert.doesNotMatch(markup, /GENERATED_EVENT_NOTE/);
  assert.equal(markup.includes(otherConversationId), false);
});
// Static matrix: 2 cases.
for (const status of ["approved", "rejected"]) test(`panel ${status} requires a separate correctly labelled continuation`, () => {
  const markup = render(view(status));
  assert.match(markup, status === "approved" ? /Continue run/ : /Continue without this change/);
  assert.equal(buttons(markup).filter(button => /^(Approve|Reject)$/.test(button.text.trim())).length, 0);
  assert.equal(buttons(markup).filter(button => /^Continue/.test(button.text.trim())).length, 1);
});
test("idle and loading approval views have no actionable or stale proposal controls", () => {
  const empty = render({ phase: "idle", data: null, busy: null, canDecide: false, canResume: false });
  assert.doesNotMatch(empty, /Approve|Reject|Continue run|Continue without this change/);
  const loading = render(view("pending", { phase: "loading" }));
  disabledActions(loading); assert.doesNotMatch(loading, /GENERATED_ARGUMENT/);
});
test("generic unavailable approval offers manual refresh and no enabled actions", () => {
  const markup = render({ phase: "error", data: null, busy: null, canDecide: false, canResume: false, message: "Approval unavailable." });
  assert.match(markup, /Approval unavailable/i); assert.match(markup, /Refresh approval/i); disabledActions(markup);
});
// Static matrix: 2 cases.
for (const busy of ["decision", "resume"]) test(`panel ${busy} busy flag disables pending action buttons independently of cached capability flags`, () => {
  const markup = render(view("pending", { busy, canDecide: true })); disabledActions(markup);
  const continued = render(view("approved", { busy, canResume: true })); disabledActions(continued);
});
// Static matrix: 2 cases.
for (const status of ["executed", "failed"]) test(`panel ${status} detail exposes no decision or continuation`, () => {
  const markup = render(view(status));
  assert.equal(buttons(markup).filter(button => /^(Approve|Reject|Continue)/.test(button.text.trim())).length, 0);
});

function eventsResponse(events) {
  return new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(""), { headers: { "content-type": "text/event-stream" } });
}
function started(input) { return { type: "RUN_STARTED", threadId: input.threadId, runId: input.runId }; }
function finished(input, changes = {}) { return { type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId, ...changes }; }
function nativeInterrupt(changes = {}) {
  return { id: nativeId, reason: "function_approval", expiresAt: "2099-01-01T00:00:00Z",
    metadata: { agent_framework: { function_call: { name: "WriteClient", call_id: "generated-call", arguments: { text: "GENERATED_ARGUMENT" } } }, craft: { approvalId } }, ...changes };
}
function pauseResponse(input, interrupts = [nativeInterrupt()], additionalEvents = []) {
  return eventsResponse([started(input), ...additionalEvents, finished(input, { outcome: { type: "interrupt", interrupts } })]);
}
function successResponse(input, additionalEvents = []) { return eventsResponse([started(input), ...additionalEvents, finished(input)]); }
function assertApprovalWire(call, expectedRunId = runId, expectedApprovalId = approvalId, expectedConversationId = conversationId) {
  const input = JSON.parse(call.options.body);
  assert.equal(call.url, "/api/agent/runs"); assert.equal(call.options.method, "POST");
  assert.equal(call.options.credentials, "same-origin"); assert.equal(call.options.signal instanceof AbortSignal, true);
  assert.equal(input.threadId, expectedConversationId); assert.equal(input.runId, expectedRunId);
  assert.deepEqual(input.resume, { approvalId: expectedApprovalId }); assert.deepEqual(input.state, {}); assert.deepEqual(input.messages, []);
  assert.deepEqual(input.tools, []); assert.deepEqual(input.context, []); assert.deepEqual(input.forwardedProps, {});
  assert.deepEqual(Object.keys(input).sort(), ["context", "forwardedProps", "messages", "resume", "runId", "state", "threadId", "tools"]);
  assert.equal(call.options.body.includes(nativeId), false);
  assert.equal(call.options.body.includes("GENERATED_SAVED"), false);
  assert.equal(call.options.body.includes('"approved"'), false);
  return input;
}
function requireResume(state) {
  assert.equal(typeof state.resumeApproval, "function", "narrow guarded session resumeApproval(runId, approvalId) is required");
  return state.resumeApproval.bind(state);
}
async function actualSession(fetcher, body, callbacks = {}, ownerId = conversationId) {
  const originalFetch = globalThis.fetch;
  const originalError = console.error;
  const logged = [];
  globalThis.fetch = fetcher;
  console.error = (...args) => logged.push(args.map(String).join(" "));
  const state = createThreadSession(ownerId, { onAuthInvalid() {}, onSessionInvalid() {}, onError() {}, ...callbacks });
  try { state.activate(); await body(state, logged); }
  finally { state.dispose(); globalThis.fetch = originalFetch; console.error = originalError; }
}
const savedMessages = [{ id: "generated-saved-message", role: "assistant", content: "GENERATED_SAVED_CONTENT" }];
const savedState = { clientOnly: "GENERATED_SAVED_STATE", citations: [{ id: "generated-citation", sourceId: "generated-source" }] };
const savedEvents = [{ type: "STATE_SNAPSHOT", snapshot: savedState }, { type: "MESSAGES_SNAPSHOT", messages: savedMessages }];

test("actual installed SDK same-client approval resume covers its bound native interrupt without sending native state or payload", async () => {
  const calls = []; const initialized = []; let settled = 0, stateRef, pendingBeforeHttp;
  await actualSession(function (url, options) {
    assert.ok(this === undefined || this === globalThis);
    const call = { url, options }; calls.push(call); const input = JSON.parse(options.body);
    if (calls.length === 1) return Promise.resolve(pauseResponse(input, [nativeInterrupt()], savedEvents));
    pendingBeforeHttp = clone(stateRef.agent.pendingInterrupts); assertApprovalWire(call);
    return Promise.resolve(successResponse(input));
  }, async state => {
    stateRef = state; const resume = requireResume(state);
    const subscription = state.agent.subscribe({ onRunInitialized({ input }) { initialized.push(clone(input)); } });
    try {
      await state.agent.runAgent({ runId }); assert.deepEqual(state.agent.pendingInterrupts, [nativeInterrupt()]);
      assert.deepEqual(state.agent.state, savedState); assert.deepEqual(state.agent.messages, savedMessages);
      await resume(runId, approvalId); assert.equal(calls.length, 2);
      assert.deepEqual(initialized[1].resume, [{ interruptId: nativeId, status: "resolved" }]);
      assert.deepEqual(pendingBeforeHttp, [nativeInterrupt()]); assert.deepEqual(state.agent.pendingInterrupts, []);
      assert.deepEqual(state.agent.state, savedState); assert.deepEqual(state.agent.messages, savedMessages); assert.equal(settled, 2);
    } finally { subscription.unsubscribe(); }
  }, { onRunSettled() { settled++; } });
});
test("actual installed SDK fresh-client approval resume uses only an internal approval-ID marker", async () => {
  const calls = []; const initialized = []; let stateRef, pendingBeforeHttp, settled = 0;
  await actualSession(async (url, options) => {
    calls.push({ url, options }); pendingBeforeHttp = clone(stateRef.agent.pendingInterrupts);
    return successResponse(assertApprovalWire(calls[0]));
  }, async state => {
    stateRef = state; const resume = requireResume(state);
    const subscription = state.agent.subscribe({ onRunInitialized({ input }) { initialized.push(clone(input)); } });
    try {
      await resume(runId, approvalId); assert.equal(calls.length, 1); assert.deepEqual(pendingBeforeHttp, []);
      assert.deepEqual(initialized[0].resume, [{ interruptId: approvalId, status: "resolved" }]);
      assert.deepEqual(state.agent.pendingInterrupts, []); assert.equal(settled, 1);
    } finally { subscription.unsubscribe(); }
  }, { onRunSettled() { settled++; } });
});
test("approval wire adapter preserves ordinary run messages, state, tools, context and forwarded properties", async () => {
  const calls = [];
  await actualSession(async (url, options) => {
    const call = { url, options }; calls.push(call); const input = JSON.parse(options.body);
    if (calls.length === 1) return pauseResponse(input, [nativeInterrupt()], savedEvents);
    return successResponse(input);
  }, async state => {
    const resume = requireResume(state); await state.agent.runAgent({ runId }); await resume(runId, approvalId);
    const parameters = { runId: otherRunId, tools: [{ name: "GeneratedRead", description: "Generated read", parameters: { type: "object" } }],
      context: [{ description: "Generated context", value: "GENERATED_CONTEXT" }], forwardedProps: { generatedFlag: false } };
    await state.agent.runAgent(parameters);
    assertApprovalWire(calls[1]); const normal = JSON.parse(calls[2].options.body);
    assert.deepEqual(normal, { threadId: conversationId, ...parameters, state: savedState, messages: savedMessages });
    assert.equal(Object.hasOwn(normal, "resume"), false);
  });
});
test("owned adapter never rewrites a normal SDK ResumeEntry array into the server approval object", async () => {
  const calls = [];
  await actualSession(async (url, options) => { calls.push({ url, options }); return successResponse(JSON.parse(options.body)); }, async state => {
    requireResume(state);
    const resume = [{ interruptId: "generated-unowned-resume", status: "resolved", payload: { generatedValue: false } }];
    await state.agent.runAgent({ runId, resume }); assert.deepEqual(JSON.parse(calls[0].options.body).resume, resume);
  });
});
test("ordinary durable run UUID generation and resume UUID rejection remain intact", async () => {
  const calls = [];
  await actualSession(async (url, options) => { calls.push({ url, options }); return successResponse(JSON.parse(options.body)); }, async state => {
    requireResume(state); await state.agent.runAgent();
    assert.match(JSON.parse(calls[0].options.body).runId, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
    await assert.rejects(state.agent.runAgent({ runId: "bad", resume: [] }), /durable UUID/i); assert.equal(calls.length, 1);
  });
});
test("narrow session resume requires durable run, approval and conversation UUIDs before HTTP", async () => {
  let calls = 0;
  await actualSession(async () => { calls++; throw new Error("Unexpected generated HTTP"); }, async state => {
    const resume = requireResume(state);
    for (const ids of [["bad", approvalId], [undefined, approvalId], [runId, "bad"], [runId, undefined], [runId, { approvalId }]]) {
      await assert.rejects(Promise.resolve().then(() => resume(...ids))); assert.equal(calls, 0);
    }
  });
  await actualSession(async () => { calls++; throw new Error("Unexpected generated HTTP"); }, async state => {
    await assert.rejects(Promise.resolve().then(() => requireResume(state)(runId, approvalId))); assert.equal(calls, 0);
  }, {}, "bad-conversation");
});
test("normal pending-interrupt coverage stays enforced before the explicit owned resume path", async () => {
  const calls = [];
  await actualSession(async (url, options) => {
    calls.push({ url, options }); const input = JSON.parse(options.body);
    return calls.length === 1 ? pauseResponse(input) : successResponse(input);
  }, async state => {
    const resume = requireResume(state); await state.agent.runAgent({ runId });
    const pending = clone(state.agent.pendingInterrupts);
    await assert.rejects(state.agent.runAgent({ runId, resume: [] }), /pending interrupt|not addressed/i);
    assert.equal(calls.length, 1); assert.deepEqual(state.agent.pendingInterrupts, pending);
    await resume(runId, approvalId); assert.equal(calls.length, 2); assertApprovalWire(calls[1]);
  });
});
// Static matrix: 3 cases.
for (const shape of ["missing-binding", "different-binding", "multiple-pending"]) test(`SDK ${shape} pending interruptions are refused without clearing or bypassing them`, async () => {
  const interruptions = shape === "missing-binding" ? [nativeInterrupt({ metadata: {} })]
    : shape === "different-binding" ? [nativeInterrupt({ metadata: { craft: { approvalId: otherApprovalId } } })]
    : [nativeInterrupt(), nativeInterrupt({ id: "generated-second-native-interrupt", metadata: { craft: { approvalId: otherApprovalId } } })];
  let calls = 0;
  await actualSession(async (_url, options) => { calls++; return pauseResponse(JSON.parse(options.body), interruptions); }, async state => {
    const resume = requireResume(state); await state.agent.runAgent({ runId }); const pending = clone(state.agent.pendingInterrupts);
    await assert.rejects(Promise.resolve().then(() => resume(runId, approvalId))); assert.equal(calls, 1);
    assert.deepEqual(state.agent.pendingInterrupts, pending);
  });
});
test("same-client pending interrupt from another durable run is refused before HTTP", async () => {
  let calls = 0;
  await actualSession(async (_url, options) => { calls++; return pauseResponse(JSON.parse(options.body)); }, async state => {
    const resume = requireResume(state); await state.agent.runAgent({ runId }); const pending = clone(state.agent.pendingInterrupts);
    await assert.rejects(Promise.resolve().then(() => resume(otherRunId, approvalId))); assert.equal(calls, 1);
    assert.deepEqual(state.agent.pendingInterrupts, pending);
  });
});
test("actual installed SDK expiry validation prevents owned resume and retains the expired pending interrupt", async () => {
  const expired = nativeInterrupt({ expiresAt: "2000-01-01T00:00:00Z" }); let calls = 0, initialized = 0;
  await actualSession(async (_url, options) => { calls++; return pauseResponse(JSON.parse(options.body), [expired]); }, async state => {
    const resume = requireResume(state); await state.agent.runAgent({ runId });
    const subscription = state.agent.subscribe({ onRunInitialized() { initialized++; } });
    try {
      await assert.rejects(Promise.resolve().then(() => resume(runId, approvalId)), /expired/i);
      assert.equal(calls, 1); assert.equal(initialized, 0); assert.deepEqual(state.agent.pendingInterrupts, [expired]);
    } finally { subscription.unsubscribe(); }
  });
});
test("session owned-resume busy guard prevents a second native request before the first settles", async () => {
  const wait = deferred(); const calls = [];
  await actualSession(async (url, options) => { calls.push({ url, options }); return wait.promise; }, async state => {
    const resume = requireResume(state); const first = resume(runId, approvalId); await flush();
    await denied(() => resume(runId, approvalId)); assert.equal(calls.length, 1);
    wait.resolve(successResponse(assertApprovalWire(calls[0]))); await first;
  });
});
test("owned resume refuses an already active ordinary SDK run without dispatching another request", async () => {
  const wait = deferred(); let calls = 0, input;
  await actualSession(async (_url, options) => { calls++; input = JSON.parse(options.body); return wait.promise; }, async state => {
    const resume = requireResume(state); const run = state.agent.runAgent({ runId }); await flush();
    await denied(() => resume(runId, approvalId)); assert.equal(calls, 1);
    wait.resolve(successResponse(input)); await run;
  });
});
test("owned resume requires a real terminal stream event and refuses bare EOF without settled refresh", async () => {
  let settled = 0, finalized = 0;
  await actualSession(async (url, options) => eventsResponse([started(assertApprovalWire({ url, options }))]), async state => {
    const resume = requireResume(state);
    const subscription = state.agent.subscribe({ onRunFinalized() { finalized++; } });
    try {
      await assert.rejects(Promise.resolve().then(() => resume(runId, approvalId)), /ended before completion/i);
      assert.equal(settled, 0);
      // Installed SDK finalization itself may run on EOF; owned settled refresh is guarded.
      assert.ok(finalized <= 1);
    } finally { subscription.unsubscribe(); }
  }, { onRunSettled() { settled++; } });
});
// Static matrix: 2 cases.
for (const wrong of ["runId", "threadId"]) test(`owned resume mismatched terminal ${wrong} cannot manufacture completion`, async () => {
  let settled = 0;
  await actualSession(async (url, options) => {
    const input = assertApprovalWire({ url, options });
    return eventsResponse([started(input), finished(input, { [wrong]: wrong === "runId" ? otherRunId : otherConversationId })]);
  }, async state => {
    await assert.rejects(Promise.resolve().then(() => requireResume(state)(runId, approvalId)));
    assert.equal(settled, 0);
  }, { onRunSettled() { settled++; } });
});
// Static matrix: 4 cases.
for (const status of [401, 404, 409, 500]) test(`actual resumed HTTP ${status} preserves status and safe errors without deleting an owned conversation`, async () => {
  let auth = 0, invalidConversation = 0, settled = 0; const errors = [];
  await actualSession(async (url, options) => {
    assertApprovalWire({ url, options }); return response({ code: "generated_error", message: rawFailure }, status);
  }, async (state, logged) => {
    await assert.rejects(Promise.resolve().then(() => requireResume(state)(runId, approvalId)), error => error.status === status && !String(error).includes(rawFailure));
    await flush(); assert.equal(auth, status === 401 ? 1 : 0); assert.equal(invalidConversation, 0);
    assert.equal(errors.some(message => message.includes(rawFailure)), false); assert.equal(logged.some(message => message.includes(rawFailure)), false);
    assert.equal(settled, 1);
  }, { onAuthInvalid() { auth++; }, onSessionInvalid() { invalidConversation++; }, onError(message) { errors.push(message); }, onRunSettled() { settled++; } });
});
test("real resumed RUN_ERROR auth event invalidates current auth with one guarded settled refresh", async () => {
  let auth = 0, invalidConversation = 0, settled = 0; const errors = [];
  await actualSession(async (url, options) => {
    const input = assertApprovalWire({ url, options });
    return eventsResponse([started(input), { type: "RUN_ERROR", code: "not_signed_in", message: rawFailure }]);
  }, async (state, logged) => {
    await denied(() => requireResume(state)(runId, approvalId)); await flush();
    assert.equal(auth, 1); assert.equal(invalidConversation, 0); assert.equal(settled, 1);
    assert.equal(errors.some(message => message.includes(rawFailure)), false); assert.equal(logged.some(message => message.includes(rawFailure)), false);
  }, { onAuthInvalid() { auth++; }, onSessionInvalid() { invalidConversation++; }, onError(message) { errors.push(message); }, onRunSettled() { settled++; } });
});
test("real Stop cancels the owned resume stream and refreshes only its still-current run", async () => {
  let signal, readerCancelled = 0, settled = 0, auth = 0; const errors = [];
  await actualSession(async (url, options) => {
    signal = options.signal; const input = assertApprovalWire({ url, options });
    const body = new ReadableStream({
      start(stream) {
        stream.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(started(input))}\n\n`));
        // A native fetch body rejects pending reads when its request is aborted.
        const abort = () => stream.error(new DOMException("Generated request aborted", "AbortError"));
        signal.addEventListener("abort", abort, { once: true });
      },
    });
    const getReader = body.getReader.bind(body);
    body.getReader = (...args) => {
      const reader = getReader(...args);
      const cancel = reader.cancel.bind(reader);
      reader.cancel = reason => { readerCancelled++; return cancel(reason); };
      return reader;
    };
    return new Response(body, { headers: { "content-type": "text/event-stream" } });
  }, async state => {
    const resume = requireResume(state);
    const run = Promise.resolve().then(() => resume(runId, approvalId)).catch(() => {});
    await flush(); state.agent.abortRun(); await run; await flush();
    assert.equal(signal.aborted, true); assert.equal(readerCancelled, 1); assert.equal(settled, 1); assert.equal(auth, 0);
    assert.deepEqual(errors, []);
  }, { onRunSettled() { settled++; }, onAuthInvalid() { auth++; }, onError(message) { errors.push(message); } });
});
test("release aborts real owned stream, hides its settled refresh and refuses further owned requests", async () => {
  const wait = deferred(); let signal, input, calls = 0, callbacks = 0;
  await actualSession(async (url, options) => {
    calls++; signal = options.signal; input = assertApprovalWire({ url, options }); return wait.promise;
  }, async state => {
    const resume = requireResume(state); const run = Promise.resolve().then(() => resume(runId, approvalId)).catch(() => {});
    await flush(); state.release(); await flush(); assert.equal(signal.aborted, true);
    await denied(() => resume(runId, approvalId)); assert.equal(calls, 1);
    wait.resolve(successResponse(input)); await run; await flush(); assert.equal(callbacks, 0);
  }, { onRunSettled() { callbacks++; }, onAuthInvalid() { callbacks++; }, onSessionInvalid() { callbacks++; }, onError() { callbacks++; } });
});
// Static matrix: 2 cases.
for (const status of [401, 409]) test(`late real resumed HTTP ${status} after release cannot invalidate a new lifetime`, async () => {
  const wait = deferred(); let calls = 0, callbacks = 0;
  await actualSession(async (url, options) => { calls++; assertApprovalWire({ url, options }); return wait.promise; }, async state => {
    const resume = requireResume(state); const old = Promise.resolve().then(() => resume(runId, approvalId)).catch(() => {});
    await flush(); state.release(); state.activate(); wait.resolve(response({ message: rawFailure }, status)); await old; await flush();
    assert.equal(calls, 1); assert.equal(callbacks, 0);
  }, { onRunSettled() { callbacks++; }, onAuthInvalid() { callbacks++; }, onSessionInvalid() { callbacks++; }, onError() { callbacks++; } });
});
test("disposed owned session cannot dispatch a fresh-client approval resume", async () => {
  let calls = 0;
  await actualSession(async () => { calls++; throw new Error("Unexpected generated HTTP"); }, async state => {
    const resume = requireResume(state); state.dispose(); await denied(() => resume(runId, approvalId)); assert.equal(calls, 0);
  });
});
