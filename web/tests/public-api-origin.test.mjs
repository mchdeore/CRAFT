import test from "node:test";
import assert from "node:assert/strict";
import { statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { registerHooks } from "node:module";
import { createHash } from "node:crypto";
import "./typescript-loader.mjs";

const origin = "https://api.generated.example";
// Match Vite's public compile-time definition in this isolated test process.
registerHooks({ load(url, context, next) {
  const loaded = next(url, context);
  if (url.endsWith("/src/lib/api-client.ts")) {
    return { ...loaded, source: String(loaded.source).replace(/import\.meta\.env\b/g, JSON.stringify({ VITE_API_ORIGIN: origin })) };
  }
  return loaded;
} });
const moduleURL = new URL("../src/lib/api-client.ts", import.meta.url);
let api = {};
try { statSync(moduleURL); api = await import(moduleURL.href); }
catch (error) { if (error?.code !== "ENOENT" || error.path !== fileURLToPath(moduleURL)) throw error; }
const { AuthSession } = await import("../src/hooks/use-auth-session.ts");
const { ConversationSession } = await import("../src/hooks/use-agent-session.ts");
const { createSessionHistory, createThreadSession } = await import("../src/lib/agent-history.ts");
const { fetchSkills, saveSkill } = await import("../src/lib/skills.ts");
const { fetchToolReference } = await import("../src/lib/tools-reference.ts");
const { RunRecoveryController } = await import("../src/lib/run-recovery.ts");
const { RunApprovalController } = await import("../src/lib/run-approval.ts");
const { checkSource } = await import("../src/lib/chat-evidence.ts");
const { loadDocumentPreview } = await import("../src/components/citations/preview/use-source-preview.ts");
const { listWorkspaceFiles, workspaceFileURL } = await import("../src/lib/workspace-files.ts");
const A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const D = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const owner = "11111111-1111-4111-8111-111111111111";
const stamp = "2026-10-18T00:00:00Z";
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const summary = id => ({ id, title: "Generated conversation", updatedAt: stamp, archived: false, pendingApprovals: 0 });
const idle = () => ({ status: "none", runId: null, startedAt: null, endedAt: null, approvalId: null, approvalStatus: null, canResume: false });
const paused = () => ({ status: "paused", runId: B, startedAt: stamp, endedAt: stamp, approvalId: C, approvalStatus: "pending", canResume: false });
const approvalSummary = status => ({ id: C, conversationId: A, runId: B, tool: "WriteClient", summary: "Generated proposal", status, requestedAt: stamp });
const approvalDetail = () => ({ ...approvalSummary("pending"), arguments: { text: "Generated note" }, evidence: [], reasoning: "Generated reasoning", retrievalPath: [], events: [{ kind: "requested", actorId: null, at: stamp }] });
const callbacks = { onAuthInvalid() {}, onSessionInvalid() {}, onError() {} };
const tick = () => new Promise(done => setImmediate(done));
function factory(...args) { assert.equal(typeof api.createAPIClient, "function", "createAPIClient production export is required"); return api.createAPIClient(...args); }
function remote(calls, paths) {
  assert.deepEqual(calls.map(call => call.url), paths.map(path => origin + path));
  for (const call of calls) assert.equal(call.init?.credentials, "include");
}
async function network(responder, body) {
  const oldFetch = globalThis.fetch, oldStorage = globalThis.sessionStorage;
  const values = new Map(); const calls = [];
  globalThis.sessionStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  globalThis.fetch = async (input, init) => { const call = { input, url: String(input), init }; calls.push(call); return responder(call, calls); };
  try { await body(calls); } finally { globalThis.fetch = oldFetch; globalThis.sessionStorage = oldStorage; }
}

test("local API connection retains root paths and same-origin credentials", async () => {
  const calls = []; const response = new Response("generated");
  const client = factory(undefined, async (input, init) => { calls.push({ input, init }); return response; });
  assert.equal(client.url("/api/tools"), "/api/tools");
  assert.equal(await client.fetch("/api/tools"), response);
  assert.equal(calls[0].input, "/api/tools"); assert.equal(calls[0].init.credentials, "same-origin");
});
test("remote connection preserves query bytes, headers, body, signal, cache and Response", async () => {
  const controller = new AbortController(); const headers = new Headers({ "X-File-Name": "generated.txt", "Content-Type": "text/plain" });
  const response = new Response("generated"); const calls = [];
  const client = factory(origin + "/", async (input, init) => { calls.push({ input, init }); return response; });
  const path = "/api/library/documents/generated/preview?version=2&locator=heading%3AEvidence";
  const init = { method: "POST", body: "generated", headers, signal: controller.signal, cache: "no-store", redirect: "manual", credentials: "omit" };
  assert.equal(client.url(path), origin + path); assert.equal(await client.fetch(path, init), response);
  assert.equal(calls[0].input, origin + path);
  assert.deepEqual(calls[0].init, { ...init, credentials: "include" });
  assert.equal(calls[0].init.headers, headers); assert.equal(calls[0].init.signal, controller.signal);
});
test("configured SDK Request is forwarded without consuming or replacing its body", async () => {
  const request = new Request(origin + "/api/agent/runs", { method: "POST", body: "generated", signal: new AbortController().signal });
  let seen; const client = factory(origin, async (input, init) => { seen = { input, init }; return new Response(null, { status: 204 }); });
  await client.fetch(request); assert.equal(seen.input, request); assert.equal(seen.input.signal, request.signal);
  assert.equal(seen.init.credentials, "include"); assert.equal(request.bodyUsed, false); assert.equal(await request.text(), "generated");
});
test("API connection never buffers or waits for the stream to finish", async () => {
  let push; let cancelled = false;
  const stream = new ReadableStream({ start(controller) { push = controller; }, cancel() { cancelled = true; } });
  const response = new Response(stream); const client = factory(origin, async () => response);
  const actual = await client.fetch("/api/agent/runs"); assert.equal(actual, response); assert.equal(response.body.locked, false);
  const reader = actual.body.getReader(); push.enqueue(new TextEncoder().encode("first"));
  assert.equal(new TextDecoder().decode((await reader.read()).value), "first"); await reader.cancel(); assert.equal(cancelled, true);
});
test("invalid configured origins are refused without echoing supplied values", () => {
  factory(origin);
  for (const value of ["http://api.generated.example", "https://GENERATED_USER:GENERATED_PASSWORD" + "@" + "api.generated.example", origin + "/path", origin + "?GENERATED_SECRET", origin + "#GENERATED_SECRET", "https://*.generated.example", "not-an-origin"]) {
    assert.throws(() => factory(value), error => !error.message.includes("GENERATED_") && /HTTPS origin/.test(error.message));
  }
});
test("foreign and malformed API inputs fail before network", async () => {
  let calls = 0; const client = factory(origin, async () => { calls++; return new Response(null, { status: 204 }); });
  for (const input of ["//foreign.generated.example/api/tools", "https://foreign.generated.example/api/tools", "/api/../outside", "/api/%2e%2e/outside", "/api/tools#fragment", "/api/\\outside", "/api/tools\n", "/mock-files/generated.docx", new URL("https://foreign.generated.example/api/tools"), new Request("https://foreign.generated.example/api/tools")]) {
    await assert.rejects(client.fetch(input), TypeError);
  }
  assert.equal(calls, 0);
});
test("API resource links map to the backend while opaque media stays unchanged", async () => {
  const calls = []; const client = factory(origin, async (input, init) => { calls.push({ input, init }); return new Response("generated"); });
  const path = `/api/conversations/${A}/files/generated%2Fplot.svg/content`;
  assert.equal(client.resourceURL(path), origin + path);
  for (const value of ["https://media.generated.example/image.png", "data:image/png;base64,AA==", "blob:https://frontend.generated.example/generated"]) assert.equal(client.resourceURL(value), value);
  await client.resourceFetch(path); await client.resourceFetch("https://media.generated.example/image.png", { credentials: "include" });
  assert.equal(calls[0].input, origin + path); assert.equal(calls[0].init.credentials, "include");
  assert.equal(calls[1].input, "https://media.generated.example/image.png"); assert.equal(calls[1].init.credentials, "omit");
});

test("actual login, identity and logout use the configured HTTPS API", async () => {
  const me = { id: owner, username: "generated-user", displayName: "Generated user", roles: ["member"] };
  await network(call => call.init?.method === "DELETE" ? new Response(null, { status: 204 }) : call.url.endsWith("/me") ? json(null, 401) : json(me), async calls => {
    const auth = new AuthSession();
    try {
      await auth.start(); await auth.signIn({ username: "generated-user", password: "GENERATED_TEST_PASSWORD" }); await auth.signOut();
      remote(calls, ["/api/me", "/api/session", "/api/session"]);
      assert.equal(calls[1].init.method, "POST"); assert.equal(calls[2].init.method, "DELETE");
      assert.deepEqual(JSON.parse(calls[1].init.body), { username: "generated-user", password: "GENERATED_TEST_PASSWORD" });
    } finally { auth.dispose(); }
  });
});
test("actual conversation creation and history refresh use the configured API", async () => {
  await network(call => json(call.init?.method === "POST" ? summary(A) : [summary(A)]), async calls => {
    const session = new ConversationSession(owner);
    try { await session.start(); await session.refreshHistory(); remote(calls, ["/api/conversations", "/api/conversations"]); assert.equal(session.getSnapshot().sessionID, A); }
    finally { session.dispose(); }
  });
});
test("actual persisted thread history uses the configured API and its abort signal", async () => {
  await network(() => json({ ...summary(A), messages: [] }), async calls => {
    const signal = new AbortController().signal; await createSessionHistory(A, signal).load();
    remote(calls, [`/api/conversations/${A}`]); assert.equal(calls[0].init.signal, signal);
  });
});
test("actual SDK run reaches remote API with exact selected revision and SSE headers", async () => {
  await network(call => {
    const input = JSON.parse(call.init.body);
    const events = [{ type: "RUN_STARTED", threadId: input.threadId, runId: input.runId }, { type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId }];
    return new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } });
  }, async calls => {
    const selection = { id: C, revision: 2 }; const session = createThreadSession(A, callbacks, selection);
    try {
      selection.revision = 3; await session.agent.runAgent(); remote(calls, ["/api/agent/runs"]);
      const input = JSON.parse(calls[0].init.body); assert.deepEqual(input.skill, { id: C, revision: 2 });
      assert.equal(input.threadId, A); assert.equal(calls[0].init.method, "POST");
      assert.match(new Headers(calls[0].init.headers).get("Accept"), /text\/event-stream/);
      assert.equal(calls[0].init.signal instanceof AbortSignal, true);
    } finally { session.dispose(); }
  });
});
test("actual SDK approval resume keeps its narrow body on the remote connection", async () => {
  await network(call => {
    const input = JSON.parse(call.init.body);
    return new Response([{ type: "RUN_STARTED", threadId: A, runId: input.runId }, { type: "RUN_FINISHED", threadId: A, runId: input.runId }].map(event => `data: ${JSON.stringify(event)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } });
  }, async calls => {
    const session = createThreadSession(A, callbacks, { id: D, revision: 7 });
    try {
      await session.resumeApproval(B, C); remote(calls, ["/api/agent/runs"]);
      const input = JSON.parse(calls[0].init.body); assert.equal(input.runId, B); assert.equal(input.skill, undefined);
      assert.deepEqual(input.resume, { approvalId: C }); assert.deepEqual(input.state, {});
    } finally { session.dispose(); }
  });
});
test("actual skills list and save use remote API without changing mutation wire", async () => {
  const saved = { id: C, revision: 1, name: "Generated skill", description: "Generated", instructions: "List tools", exampleInputs: [], toolNames: ["list_skills"], unavailableTools: [], createdAt: stamp, updatedAt: stamp };
  const input = { id: C, expectedRevision: 0, mutationId: D, name: saved.name, description: saved.description, instructions: saved.instructions, exampleInputs: [], toolNames: saved.toolNames };
  await network(call => json(call.init?.method === "PUT" ? saved : { items: [], nextCursor: null }), async calls => {
    const signal = new AbortController().signal; await fetchSkills(signal); assert.deepEqual(await saveSkill(input, signal), saved);
    remote(calls, ["/api/skills", `/api/skills/${C}`]); const { id: unused, ...body } = input;
    assert.deepEqual(JSON.parse(calls[1].init.body), body); assert.equal(calls[1].init.signal, signal);
  });
});
test("actual tool inventory and workspace file list both use remote API", async () => {
  await network(() => json([]), async calls => {
    await fetchToolReference(new AbortController().signal); await listWorkspaceFiles(A);
    remote(calls, ["/api/tools", `/api/conversations/${A}/files`]);
    assert.equal(workspaceFileURL(A, "generated%2Fplot.svg"), `/api/conversations/${A}/files/generated%2Fplot.svg/content`);
  });
});
test("actual recovery default fetch uses remote API and owned abort signal", async () => {
  await network(() => json(idle()), async calls => {
    const recovery = new RunRecoveryController(A, callbacks); recovery.activate();
    try { await recovery.refresh(); remote(calls, [`/api/conversations/${A}/run`]); assert.equal(calls[0].init.signal instanceof AbortSignal, true); assert.equal(recovery.getSnapshot().phase, "ready"); }
    finally { recovery.release(); }
  });
});
test("actual approval read and decision use remote API with unchanged approval body", async () => {
  await network(call => json(call.init?.method === "POST" ? approvalSummary("approved") : approvalDetail()), async calls => {
    const approval = new RunApprovalController(A, { onAuthInvalid() {}, onRecoveryRefresh() {}, onResume() {} }); approval.activate();
    try {
      approval.setRecovery({ phase: "ready", data: paused() }); await tick(); await tick();
      await approval.decide(true); remote(calls, [`/api/approvals/${C}`, `/api/approvals/${C}/decision`]);
      assert.deepEqual(JSON.parse(calls[1].init.body), { approve: true });
    } finally { approval.release(); }
  });
});
const quote = "Generated source paragraph.";
const handle = { item: "generated-source", version: 2, block: 1, locator: "heading:Evidence", quoteSha256: createHash("sha256").update(quote).digest("hex"), simulated: true };
const citation = { handle, title: "Generated source", headingPath: "Evidence", classification: "unclassified", quote };
const metadata = { item: handle.item, version: 2, title: citation.title, path: "generated/source.md", tags: { mission: null, silo: null, stage: null }, summary: "", summaryKind: "extract", outline: [{ block: 1, headingPath: "Evidence" }], versions: [{ version: 2, sha256: "a".repeat(64), indexedAt: stamp }], simulated: true, classification: "unclassified", deleted: false };
const previewPath = "/api/library/documents/generated-source/preview?version=2&locator=heading%3AEvidence";
test("actual citation checking maps only network URLs and keeps canonical protected references", async () => {
  await network(call => json(call.url.endsWith("/check") ? { status: "current", latestVersion: 2 } : metadata), async calls => {
    const source = await checkSource(citation, new AbortController().signal); assert.ok(source);
    remote(calls, ["/api/library/citations/check", "/api/library/documents/generated-source?version=2"]);
    assert.equal(source.previewURL, previewPath); assert.equal(source.downloadURL, previewPath.replace("/preview?", "/download?"));
  });
});
test("actual protected preview validates before mapping and preserves response bytes", async () => {
  await network(() => new Response("Generated preview", { headers: { "Content-Type": "text/plain", "Content-Length": "17" } }), async calls => {
    const value = await loadDocumentPreview({ previewURL: previewPath }, new AbortController().signal);
    assert.equal(value.type, "ready"); assert.equal(await value.blob.text(), "Generated preview"); remote(calls, [previewPath]); assert.equal(calls[0].init.cache, "no-store");
    for (const invalid of [origin + previewPath, "//foreign.generated.example" + previewPath, "/api/library/documents/../preview", "/mock-files/generated.docx"]) {
      const result = await loadDocumentPreview({ previewURL: invalid }, new AbortController().signal); assert.notEqual(result.type, "ready");
    }
    assert.equal(calls.length, 1);
  });
});
test("remote configuration does not loosen canonical protected-preview validation", async () => {
  await network(() => { throw new Error("Forbidden preview request"); }, async calls => {
    for (const invalid of [origin + previewPath, "//foreign.generated.example" + previewPath, "/api/library/documents/../preview", "/mock-files/generated.docx"]) {
      const result = await loadDocumentPreview({ previewURL: invalid }, new AbortController().signal); assert.notEqual(result.type, "ready");
    }
    assert.equal(calls.length, 0);
  });
});
for (const [name, suffix] of [
  ["dot segments", "/api/a/../tools"], ["encoded dot segments", "/api/%2e%2e/api/tools"],
  ["backslashes", "/api/a\\..\\tools"], ["embedded newline", "/api/to\nols"],
  ["embedded tab", "/api/to\tols"], ["empty fragment", "/api/tools#"],
  ["trailing space", "/api/tools "], ["query with empty fragment", "/api/tools?after=generated#"],
  ["single dot segment", "/api/./tools"], ["encoded single dot", "/api/%2E/tools"],
]) test(`matching-origin absolute API string rejects ${name} before network`, async () => {
  let calls = 0; const client = factory(origin, async () => { calls++; return new Response(null, { status: 204 }); });
  await assert.rejects(client.fetch(origin + suffix), TypeError); assert.equal(calls, 0);
});
for (const [name, value] of [
  ["normalized parent path", origin + "/path/.."], ["normalized dot path", origin + "/./"],
  ["empty query", origin + "?"], ["empty fragment", origin + "#"],
  ["embedded newline", "https://api.gene\nrated.example"], ["embedded tab", "https://api.gene\trated.example"],
]) test(`configured origin rejects raw ${name} before URL normalization`, () => {
  assert.throws(() => factory(value), TypeError);
});
