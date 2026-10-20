import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "./typescript-loader.mjs";
const { createThreadSession } = await import("../src/lib/agent-history.ts");
const moduleUrl = new URL("../src/lib/run-recovery.ts", import.meta.url);
const componentUrl = new URL("../src/components/run-recovery-status.tsx", import.meta.url);
const library = existsSync(moduleUrl) ? await import(moduleUrl.href) : {};
const component = existsSync(componentUrl) ? await import(componentUrl.href) : {};
const id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const runId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const approvalId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const idle = () => ({ status: "none", runId: null, startedAt: null, endedAt: null, approvalId: null, approvalStatus: null, canResume: false });
const recorded = (status = "finished") => ({ ...idle(), status, runId, startedAt: "2026-10-17T12:00:00+00:00", endedAt: "2026-10-17T12:01:00+00:00" });
const paused = (status = "pending") => ({ ...recorded("paused"), approvalId, approvalStatus: status, canResume: ["approved", "rejected"].includes(status) });
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const parse = value => { assert.equal(typeof library.parseRunRecovery, "function", "run recovery parser is required"); return library.parseRunRecovery(value); };
const controller = (fetcher, callbacks = {}) => { assert.equal(typeof library.RunRecoveryController, "function", "run recovery controller is required"); return new library.RunRecoveryController(id, { onAuthInvalid() {}, onSessionInvalid() {}, ...callbacks }, fetcher); };
const render = view => { assert.equal(typeof component.RunRecoveryStatus, "function", "run recovery status view is required"); return renderToStaticMarkup(React.createElement(component.RunRecoveryStatus, { view, onRefresh() {} })); };
const tick = () => new Promise(resolve => setImmediate(resolve));
for (const status of ["none", "running", "paused", "finished", "failed", "cancelled"]) test(`typed recorded status ${status}`, () => { const value = status === "none" ? idle() : recorded(status); assert.deepEqual(parse(value), value); });
for (const status of ["pending", "approved", "rejected", "executed", "failed"]) test(`confirmed paused approval ${status}`, () => assert.deepEqual(parse(paused(status)), paused(status)));
test("malformed required wire data is refused", () => {
  const valid = recorded();
  for (const key of Object.keys(valid)) { const value = { ...valid }; delete value[key]; assert.throws(() => parse(value)); }
  for (const value of [null, [], { ...valid, status: "unknown" }, { ...valid, runId: "bad" }, { ...valid, canResume: "true" }, { ...valid, startedAt: "bad" }, { ...paused(), approvalStatus: "unknown" }]) assert.throws(() => parse(value));
});
test("no-run and unbound paused data do not invent an approval", () => { assert.deepEqual(parse(idle()), idle()); assert.deepEqual(parse(recorded("paused")), recorded("paused")); });
test("fetch is owned same-origin and uses current lifecycle abort signal", async () => {
  const calls = []; const state = controller(async (url, options) => { calls.push({ url, options }); return response(recorded()); });
  await state.refresh(); assert.equal(calls.length, 0); state.activate(); await state.refresh();
  assert.equal(calls[0].url, `/api/conversations/${id}/run`); assert.equal(calls[0].options.credentials, "same-origin"); assert.equal(calls[0].options.signal instanceof AbortSignal, true); assert.equal(state.getSnapshot().phase, "ready"); assert.deepEqual(state.getSnapshot().data, recorded()); state.release();
});
test("loading and stable snapshots notify active subscribers", async () => {
  const waiting = deferred(); const state = controller(() => waiting.promise); let updates = 0; const unsubscribe = state.subscribe(() => updates++); state.activate(); const request = state.refresh();
  assert.equal(state.getSnapshot().phase, "loading"); assert.equal(state.getSnapshot(), state.getSnapshot()); waiting.resolve(response(idle())); await request; assert.equal(state.getSnapshot().phase, "ready"); assert.ok(updates >= 2); unsubscribe(); const before = updates; state.release(); assert.equal(updates, before);
});
test("latest refresh wins and aborts older response", async () => {
  const first = deferred(); const second = deferred(); const calls = []; const state = controller((_url, options) => { calls.push(options); return calls.length === 1 ? first.promise : second.promise; }); state.activate(); const a = state.refresh(); const b = state.refresh(); assert.equal(calls[0].signal.aborted, true);
  second.resolve(response(recorded())); await b; first.resolve(response(paused())); await a; assert.deepEqual(state.getSnapshot().data, recorded()); state.release();
});
test("late JSON cannot replace a newer lifetime", async () => {
  const json = deferred(); let count = 0; const state = controller(async () => ++count === 1 ? { ok: true, status: 200, json: () => json.promise } : response(recorded())); state.activate(); const a = state.refresh(); await tick(); state.release(); state.activate(); await state.refresh(); json.resolve(paused()); await a; assert.deepEqual(state.getSnapshot().data, recorded()); state.release();
});
for (const status of [401, 404]) test(`current HTTP ${status} invalidates only its owner boundary`, async () => {
  let auth = 0, session = 0, bodyRead = 0; const state = controller(async () => ({ ok: false, status, json() { bodyRead++; throw new Error("PRIVATE_BODY"); } }), { onAuthInvalid() { auth++; }, onSessionInvalid() { session++; } }); state.activate(); await state.refresh(); assert.equal(auth, status === 401 ? 1 : 0); assert.equal(session, status === 404 ? 1 : 0); assert.equal(bodyRead, 0); assert.equal(state.getSnapshot().phase, "error"); assert.equal(JSON.stringify(state.getSnapshot()).includes("PRIVATE_BODY"), false); state.release();
});
for (const status of [401, 404]) test(`late HTTP ${status} cannot invalidate a released chat`, async () => {
  const waiting = deferred(); let invalid = 0; const state = controller(() => waiting.promise, { onAuthInvalid() { invalid++; }, onSessionInvalid() { invalid++; } }); state.activate(); const request = state.refresh(); state.release(); waiting.resolve(response({}, status)); await request; assert.equal(invalid, 0); assert.equal(state.getSnapshot().data, null); await state.refresh(); assert.equal(invalid, 0);
});
test("superseded failed request cannot replace current result", async () => {
  const waiting = deferred(); let count = 0; const state = controller(async () => ++count === 1 ? waiting.promise : response(recorded())); state.activate(); const a = state.refresh(); await state.refresh(); waiting.reject(new Error("PRIVATE_NETWORK")); await a; assert.equal(state.getSnapshot().phase, "ready"); assert.equal(JSON.stringify(state.getSnapshot()).includes("PRIVATE_NETWORK"), false); state.release();
});
test("malformed response and network failure support manual retry", async () => {
  let count = 0; const state = controller(async () => { count++; if (count === 1) throw new Error("PRIVATE_FAILURE"); return response(count === 2 ? { status: "bad" } : recorded()); }); state.activate(); await state.refresh(); assert.equal(state.getSnapshot().phase, "error"); assert.equal(JSON.stringify(state.getSnapshot()).includes("PRIVATE_FAILURE"), false); await state.refresh(); assert.equal(state.getSnapshot().phase, "error"); await state.refresh(); assert.equal(state.getSnapshot().phase, "ready"); state.release();
});
test("StrictMode release-reactivate retains only fresh metadata", async () => {
  const waiting = deferred(); const signals = []; const state = controller(async (_url, options) => { signals.push(options.signal); return signals.length === 1 ? waiting.promise : response(recorded()); }); state.activate(); const old = state.refresh(); state.release(); state.activate(); await state.refresh(); waiting.resolve(response(paused())); await old; assert.equal(signals[0].aborted, true); assert.equal(state.getSnapshot().phase, "ready"); assert.deepEqual(state.getSnapshot().data, recorded()); state.release();
});
test("display distinguishes idle loading and unavailable metadata", () => {
  assert.match(render({ phase: "idle", data: null }), /Checking recorded run/i); assert.match(render({ phase: "loading", data: null }), /Checking recorded run/i); assert.match(render({ phase: "ready", data: idle() }), /No recorded run/i); assert.match(render({ phase: "error", data: null }), /Run status unavailable/i);
});
test("display uses recorded state and confirmed approval only", () => {
  const running = render({ phase: "ready", data: recorded("running") }); assert.match(running, /Recorded run: Running/); assert.match(running, /last recorded state/i); assert.match(running, /Refresh run status/); assert.doesNotMatch(running, /worker is running/i);
  assert.match(render({ phase: "ready", data: paused() }), /Approval pending/i); assert.doesNotMatch(render({ phase: "ready", data: recorded("paused") }), /Approval pending/i);
});
function stream(input) { return new Response(`data: ${JSON.stringify({ type: "RUN_STARTED", threadId: input.threadId, runId: input.runId })}\n\ndata: ${JSON.stringify({ type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId })}\n\n`, { headers: { "content-type": "text/event-stream" } }); }
test("current real stream finalization requests recorded metadata refresh", async () => {
  const oldFetch = globalThis.fetch; let settled = 0; globalThis.fetch = async (_url, options) => stream(JSON.parse(options.body)); const state = createThreadSession(id, { onAuthInvalid() {}, onSessionInvalid() {}, onError() {}, onRunSettled() { settled++; } });
  try { state.activate(); await state.agent.runAgent({ runId }); assert.equal(settled, 1); } finally { state.dispose(); globalThis.fetch = oldFetch; }
});
test("released real stream cannot refresh the next chat", async () => {
  const oldFetch = globalThis.fetch; const waiting = deferred(); let input, settled = 0; globalThis.fetch = async (_url, options) => { input = JSON.parse(options.body); return waiting.promise; }; const state = createThreadSession(id, { onAuthInvalid() {}, onSessionInvalid() {}, onError() {}, onRunSettled() { settled++; } });
  try { state.activate(); const run = state.agent.runAgent({ runId }).catch(() => {}); await tick(); state.release(); await tick(); waiting.resolve(stream(input)); await run; assert.equal(settled, 0); } finally { state.dispose(); globalThis.fetch = oldFetch; }
});
