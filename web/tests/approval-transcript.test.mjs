import test from "node:test";
import assert from "node:assert/strict";
import "./typescript-loader.mjs";
import { AgUiThreadRuntimeCore } from "../node_modules/@assistant-ui/react-ag-ui/dist/runtime/AgUiThreadRuntimeCore.js";
const { createThreadSession } = await import("../src/lib/agent-history.ts");

const conversationId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const runId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const approvalId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const userId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const proposalId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
const answerId = "ffffffff-ffff-4fff-8fff-ffffffffffff";
const nativeId = "generated-native-interrupt";
const callId = "generated-write-call";
const finalText = "GENERATED_RECORDED_FINAL";
const nextText = "GENERATED_NEXT_TURN_FINAL";
const unsafe = "GENERATED_UNSAFE_RELOAD_BODY";
const proposal = [
  { id: userId, role: "user", content: "GENERATED_REQUEST" },
  { id: proposalId, role: "assistant", content: "", toolCalls: [
    { id: callId, type: "function", function: { name: "WriteClient", arguments: '{"value":12}' } },
  ] },
];
const recorded = [...proposal,
  { id: "generated-tool-result", role: "tool", toolCallId: callId, content: "GENERATED_APPLIED" },
  { id: answerId, role: "assistant", content: finalText },
];
const nextTurn = [...recorded,
  { id: "generated-next-user", role: "user", content: "GENERATED_NEXT_REQUEST" },
  { id: "generated-next-assistant", role: "assistant", content: nextText },
];
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json" },
});
const started = input => ({ type: "RUN_STARTED", threadId: input.threadId, runId: input.runId });
const finished = input => ({ type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId });
const sse = events => new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(""), {
  headers: { "content-type": "text/event-stream" },
});
const successful = (input, messages = recorded, text = finalText, messageId = answerId) => sse([
  started(input),
  { type: "TEXT_MESSAGE_START", messageId, role: "assistant" },
  { type: "TEXT_MESSAGE_CONTENT", messageId, delta: text },
  { type: "TEXT_MESSAGE_END", messageId },
  { type: "MESSAGES_SNAPSHOT", messages },
  finished(input),
]);
const paused = input => sse([
  started(input), { type: "MESSAGES_SNAPSHOT", messages: proposal },
  { ...finished(input), outcome: { type: "interrupt", interrupts: [{
    id: nativeId, reason: "function_approval", expiresAt: "2099-01-01T00:00:00Z",
    metadata: {
      agent_framework: { function_call: { name: "WriteClient", call_id: callId, arguments: { value: 12 } } },
      craft: { approvalId },
    },
  }] } },
]);
const textParts = core => core.getMessages().flatMap(message => message.content)
  .filter(part => part.type === "text").map(part => part.text);

// The real SDK core owns the repository rendered by useAgUiRuntime. Only fetch
// is replaced: all history conversion, owned wire adaptation and SSE handling
// execute production code and the installed SDK.
async function withThread(body, { sameClient = false, reloadResponse, resumeErrorCode } = {}) {
  const originalFetch = globalThis.fetch;
  const requests = [];
  const errors = [];
  let messages = sameClient ? [] : proposal;
  let durableRunId = runId;
  let reads = 0, resumes = 0, ordinaryRuns = 0, imports = 0, settled = 0;
  let auth = 0, invalidSession = 0;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    if (url === `/api/conversations/${conversationId}`) {
      reads++;
      if (reads > 1 && reloadResponse) return reloadResponse(options);
      return json({ id: conversationId, messages });
    }
    assert.equal(url, "/api/agent/runs", "generated transport refuses every unowned route");
    const input = JSON.parse(options.body);
    if (input.resume !== undefined) {
      resumes++;
      assert.deepEqual(input, {
        threadId: conversationId, runId: durableRunId, tools: [], context: [],
        forwardedProps: {}, state: {}, messages: [], resume: { approvalId },
      });
      assert.equal(options.credentials, "same-origin");
      assert.equal(options.signal instanceof AbortSignal, true);
      if (resumeErrorCode) return sse([
        started(input), { type: "RUN_ERROR", code: resumeErrorCode, message: unsafe },
      ]);
      messages = recorded;
      return successful(input);
    }
    ordinaryRuns++;
    if (sameClient && ordinaryRuns === 1) {
      durableRunId = input.runId;
      messages = proposal;
      return paused(input);
    }
    messages = nextTurn;
    return successful(input, nextTurn, nextText, "generated-next-assistant");
  };
  const resources = createThreadSession(conversationId, {
    onAuthInvalid() { auth++; }, onSessionInvalid() { invalidSession++; },
    onError(message) { errors.push(message); }, onRunSettled() { settled++; },
  });
  const core = new AgUiThreadRuntimeCore({
    agent: resources.agent, history: resources.history, showThinking: true,
    logger: { debug() {}, info() {}, warn() {}, error() {} },
  });
  const resume = () => resources.resumeApproval(durableRunId, approvalId, repository => {
    imports++;
    // This is the installed hook's onImport boundary, after thread.import.
    core.applyExternalMessages(repository.messages.map(entry => entry.message));
  });
  const next = () => core.append({
    id: "generated-next-user", parentId: core.getMessages().at(-1)?.id ?? null,
    role: "user", content: [{ type: "text", text: "GENERATED_NEXT_REQUEST" }],
    attachments: [],
  });
  try {
    resources.activate();
    await core.__internal_load();
    if (sameClient) await core.append({
      id: userId, parentId: null, role: "user",
      content: [{ type: "text", text: "GENERATED_REQUEST" }], attachments: [],
    });
    await body({ resources, core, resume, next, requests, errors,
      counts: () => ({ reads, resumes, ordinaryRuns, imports, settled, auth, invalidSession }),
    });
  } finally {
    await core.cancel();
    resources.dispose();
    globalThis.fetch = originalFetch;
  }
}

// Missing authoritative import is the bug: agent.messages alone receives the
// generated final, while the repository the UI renders stays on the proposal.
test("owned continuation after paused-history reload updates the displayed SDK repository once", async () => {
  await withThread(async ({ resources, core, resume, counts }) => {
    assert.equal(core.getPendingInterrupts(), null, "recorded history has no native pending interrupt");
    assert.equal(textParts(core).includes(finalText), false);
    await resume();
    assert.equal(resources.agent.messages.some(message => message.content === finalText), true);
    assert.equal(textParts(core).filter(text => text === finalText).length, 1,
      "the displayed repository must receive the server-recorded final after Continue");
    assert.equal(core.getMessages().filter(message => message.role === "user").length, 1);
    assert.equal(core.getMessages().flatMap(message => message.content).filter(part => part.type === "tool-call").length, 1);
    assert.deepEqual(counts(), { reads: 2, resumes: 1, ordinaryRuns: 0, imports: 1, settled: 1, auth: 0, invalidSession: 0 });
  });
});

test("same-runtime continuation replaces pending transcript and permits the next normal turn", async () => {
  await withThread(async ({ resources, core, resume, next, counts }) => {
    assert.equal(resources.agent.pendingInterrupts[0]?.id, nativeId);
    assert.equal(core.getMessages().at(-1)?.status?.type, "requires-action");
    await resume();
    assert.equal(textParts(core).filter(text => text === finalText).length, 1);
    assert.equal(core.getPendingInterrupts(), null);
    await next();
    assert.equal(textParts(core).includes(nextText), true);
    assert.equal(counts().resumes, 1);
    assert.equal(counts().ordinaryRuns, 2);
  }, { sameClient: true });
});

for (const [code, reloadResponse, expectedAuth, expectedErrors] of [
  ["internal_error", undefined, 0, 1],
  ["not_signed_in", async () => json({ message: unsafe }, 401), 1, 0],
  ["not_found", async () => json({ message: unsafe }, 404), 0, 1],
]) test(`resumed RUN_ERROR ${code} preserves displayed history without reload or duplicate callbacks`, async () => {
  await withThread(async ({ core, resume, errors, counts }) => {
    assert.equal(core.getMessages().at(-1)?.status?.type, "requires-action");
    const previous = JSON.stringify(core.getMessages());
    const result = await resume();
    assert.equal(typeof result, "object", "the installed SDK resolves RUN_ERROR rather than rejecting");
    assert.equal(JSON.stringify(core.getMessages()), previous, "a failed continuation must preserve the displayed pending transcript");
    assert.equal(counts().auth, expectedAuth, "RUN_ERROR must not trigger a second auth callback through history reload");
    assert.equal(counts().invalidSession, 0, "an approval run error must not invalidate the session through history reload");
    assert.equal(counts().reads, 1, "failed continuation must not request authoritative history");
    assert.equal(counts().imports, 0);
    assert.equal(counts().resumes, 1);
    assert.equal(counts().ordinaryRuns, 1);
    assert.equal(counts().settled, 2, "existing terminal notifications still cover the pause and run error");
    assert.equal(errors.length, expectedErrors);
    assert.equal(errors.some(message => message.includes(unsafe)), false);
  }, { sameClient: true, reloadResponse, resumeErrorCode: code });
});

test("late reload JSON from a released and reactivated lifetime cannot replace its transcript", async () => {
  const body = deferred();
  await withThread(async ({ resources, core, resume, counts }) => {
    const previous = JSON.stringify(core.getMessages());
    const continuation = resume();
    await tick();
    assert.equal(counts().reads, 2, "completed continuation must request authoritative history");
    resources.release();
    resources.activate();
    body.resolve({ id: conversationId, messages: recorded });
    await continuation;
    assert.equal(JSON.stringify(core.getMessages()), previous);
    assert.equal(counts().imports, 0);
    assert.equal(counts().resumes, 1);
  }, { reloadResponse: async () => ({ ok: true, status: 200, json: () => body.promise }) });
});

test("a newer normal run prevents an older continuation reload from overwriting the new turn", async () => {
  const body = deferred();
  await withThread(async ({ core, resume, next, counts }) => {
    const continuation = resume();
    await tick();
    assert.equal(counts().reads, 2);
    await next();
    const newer = JSON.stringify(core.getMessages());
    assert.equal(textParts(core).includes(nextText), true);
    body.resolve({ id: conversationId, messages: recorded });
    await continuation;
    assert.equal(JSON.stringify(core.getMessages()), newer);
    assert.equal(counts().imports, 0);
    assert.equal(counts().resumes, 1);
  }, { reloadResponse: async () => ({ ok: true, status: 200, json: () => body.promise }) });
});

for (const [name, reloadResponse] of [
  ["HTTP failure", async () => json({ message: unsafe }, 500)],
  ["transport failure", async () => { throw new Error(unsafe); }],
  ["invalid JSON", async () => new Response(unsafe, { headers: { "content-type": "application/json" } })],
  ["missing message array", async () => json({ id: conversationId })],
]) test(`continuation ${name} preserves displayed history and never repeats the resume`, async () => {
  await withThread(async ({ core, resume, errors, counts }) => {
    const previous = JSON.stringify(core.getMessages());
    await resume();
    assert.equal(JSON.stringify(core.getMessages()), previous);
    assert.equal(counts().imports, 0);
    assert.equal(counts().resumes, 1);
    assert.equal(errors.length, 1, "reload failure needs a safe recovery message");
    assert.match(errors[0], /conversation|refresh/i);
    assert.equal(errors[0].includes(unsafe), false);
  }, { reloadResponse });
});

test("successful empty recorded history is distinct from a failed reload", async () => {
  await withThread(async ({ core, resume, errors, counts }) => {
    await resume();
    assert.equal(core.getMessages().length, 0);
    assert.equal(counts().imports, 1);
    assert.deepEqual(errors, []);
    assert.equal(counts().resumes, 1);
  }, { reloadResponse: async () => json({ id: conversationId, messages: [] }) });
});
