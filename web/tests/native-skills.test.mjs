import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "./typescript-loader.mjs";
const { ConversationSession } = await import("../src/hooks/use-agent-session.ts");
const { createThreadSession } = await import("../src/lib/agent-history.ts");
const { SkillsView } = await import("../src/components/skills-workspace.tsx");
const { openSkillDraft } = await import("../src/lib/skills.ts");
const owner = "11111111-1111-4111-8111-111111111111";
const A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const selection = () => ({ id: C, revision: 2, name: "Engineering" });
const summary = id => ({ id, title: "Generated chat", groupName: null, updatedAt: "2026-10-18T00:00:00Z", archived: false, pendingApprovals: 0 });
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
async function conversation(fetcher, body) {
  const oldFetch = globalThis.fetch, oldStorage = globalThis.sessionStorage;
  const stored = new Map();
  globalThis.fetch = fetcher; globalThis.sessionStorage = { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key) };
  const session = new ConversationSession(owner);
  try { await session.start(); await body(session, stored); } finally { session.dispose(); globalThis.fetch = oldFetch; globalThis.sessionStorage = oldStorage; }
}
test("Try skill accepts an immutable revision with its new chat; ordinary new chat clears it", async () => {
  const ids = [A, B, C];
  await conversation(async () => json(summary(ids.shift())), async (session, stored) => {
    const chosen = selection();
    assert.equal(await session.newChat(chosen), true);
    assert.equal(session.getSnapshot().sessionID, B);
    assert.deepEqual(session.getSnapshot().selectedSkill, chosen);
    chosen.revision = 3; chosen.name = "Changed";
    assert.deepEqual(session.getSnapshot().selectedSkill, selection());
    assert.deepEqual([...stored.values()], [B]);
    await session.newChat();
    assert.equal(session.getSnapshot().selectedSkill, undefined);
    assert.equal(session.getSnapshot().sessionID, C);
  });
});
test("A superseded Try response cannot attach its skill or switch the accepted chat", async () => {
  const stale = deferred(); let calls = 0;
  await conversation(async (url, options) => { calls++; return calls === 1 ? json(summary(A)) : options?.method === "POST" ? stale.promise : json(summary(C)); }, async session => {
    const pending = session.newChat(selection()); await session.selectChat(C); stale.resolve(json(summary(B))); await pending;
    assert.equal(session.getSnapshot().sessionID, C); assert.equal(session.getSnapshot().selectedSkill, undefined);
  });
});
test("Invalid selection cannot create a chat or change current state", async () => {
  let calls = 0;
  await conversation(async () => { calls++; return json(summary(A)); }, async session => {
    await assert.rejects(session.newChat({ ...selection(), revision: 0 }));
    assert.equal(calls, 1); assert.equal(session.getSnapshot().sessionID, A);
  });
});
async function nativeSession(body, chosen = selection()) {
  const oldFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    const input = JSON.parse(init.body); calls.push({ url, init, input });
    const events = [{ type: "RUN_STARTED", threadId: input.threadId, runId: input.runId }, { type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId }];
    return new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(""), { headers: { "content-type": "text/event-stream" } });
  };
  const session = createThreadSession(A, { onAuthInvalid() {}, onSessionInvalid() {}, onError() {} }, chosen);
  try { await body(session, calls); } finally { session.dispose(); globalThis.fetch = oldFetch; }
}
test("Actual SDK normal run sends exact selected reference, copied before later edits", async () => {
  const chosen = selection();
  await nativeSession(async (session, calls) => {
    chosen.revision = 7; await session.agent.runAgent();
    assert.deepEqual(calls[0].input.skill, { id: C, revision: 2 });
    assert.equal(calls[0].input.threadId, A); assert.equal(calls[0].init.credentials, "same-origin");
    assert.equal(calls[0].init.body.includes("Engineering"), false);
  }, chosen);
});
test("Explicit approval resume never sends a browser selected skill", async () => {
  await nativeSession(async (session, calls) => {
    await session.resumeApproval(B, C);
    assert.equal(calls[0].input.skill, undefined);
    assert.deepEqual(calls[0].input.resume, { approvalId: C });
    assert.deepEqual(calls[0].input.state, {});
  });
});
const saved = changes => ({ ...selection(), description: "Generated", instructions: "List parameters", exampleInputs: [], toolNames: ["list_parameters"], unavailableTools: [], createdAt: "2026-10-18T00:00:00Z", updatedAt: "2026-10-18T00:00:00Z", ...changes });
const tool = { name: "list_parameters", availableIn: ["api", "mcp", "chat", "read_only_query"] };
function markup(editor, extra = {}) { return renderToStaticMarkup(React.createElement(SkillsView, { listStatus: "ready", catalog: { items: [], nextCursor: null }, editor, tools: [tool], toolStatus: "ready", onTry() {}, ...extra })); }
function tryButton(html) { const button = html.match(/<button\b([^>]*)>([^<]*Try skill[^<]*)<\/button>/); assert.ok(button, "Try skill button is required"); return button; }
test("Clean available saved revision offers Try skill; no stale uninstalled statement", () => {
  const html = markup(openSkillDraft(saved())); assert.doesNotMatch(tryButton(html)[1], /disabled/);
  assert.doesNotMatch(html, /skill use is not installed/i);
});
for (const [name, edit, extra] of [
  ["unsaved edits", { dirty: true }, {}], ["unavailable tools", { fields: { ...openSkillDraft(saved()).fields, toolNames: ["removed_tool"] } }, {}], ["loading tool inventory", {}, { toolStatus: "loading" }], ["busy new chat", {}, { tryPending: true }],
]) test(`Try skill is disabled for ${name}`, () => { assert.match(tryButton(markup({ ...openSkillDraft(saved()), ...edit }, extra))[1], /disabled/); });

test("Selected skill 404 reports an error without replacing the conversation", async () => {
  const oldFetch = globalThis.fetch, oldError = console.error;
  let renewed = 0; const errors = [];
  globalThis.fetch = async () => json({ code: "not_found" }, 404); console.error = () => {};
  const session = createThreadSession(A, { onAuthInvalid() {}, onSessionInvalid() { renewed++; }, onError(error) { errors.push(error); } }, selection());
  try { await assert.rejects(session.agent.runAgent()); assert.equal(renewed, 0); assert.equal(errors.length, 1); }
  finally { session.dispose(); globalThis.fetch = oldFetch; console.error = oldError; }
});

test("Try captures revision before waiting for new conversation", async () => {
  const pending = deferred(); let calls = 0;
  await conversation(async () => ++calls === 1 ? json(summary(A)) : pending.promise, async session => {
    const chosen = selection(); const creation = session.newChat(chosen); chosen.revision = 9;
    pending.resolve(json(summary(B))); await creation;
    assert.deepEqual(session.getSnapshot().selectedSkill, selection());
    session.dispose(); assert.equal(session.getSnapshot().selectedSkill, undefined);
  });
});
test("Fresh owned activation clears prior transient selection", async () => {
  const ids = [A, B, B];
  await conversation(async () => json(summary(ids.shift())), async session => {
    await session.newChat(selection()); await session.start();
    assert.equal(session.getSnapshot().sessionID, B); assert.equal(session.getSnapshot().selectedSkill, undefined);
  });
});

test("Successful archive followed by failed replacement clears the old skill", async () => {
  let created = 0;
  await conversation(async (url, init) => init?.method === "PATCH" ? json(summary(B)) : ++created < 3 ? json(summary(created === 1 ? A : B)) : json({ code: "unavailable" }, 503), async session => {
    await session.newChat(selection()); await session.reset();
    assert.equal(session.getSnapshot().sessionID, ""); assert.equal(session.getSnapshot().selectedSkill, undefined);
  });
});
