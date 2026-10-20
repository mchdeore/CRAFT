import test from "node:test";
import assert from "node:assert/strict";
import "./typescript-loader.mjs";
const { RunRecoveryController } = await import("../src/lib/run-recovery.ts");
test("default browser fetch retains a valid WebIDL receiver", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async function () {
    if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation");
    calls++;
    return new Response(JSON.stringify({ status: "none", runId: null, startedAt: null, endedAt: null, approvalId: null, approvalStatus: null, canResume: false }), {status: 200});
  };
  const recovery = new RunRecoveryController("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", {onAuthInvalid() {}, onSessionInvalid() {}});
  try { recovery.activate(); await recovery.refresh(); assert.equal(calls, 1); assert.equal(recovery.getSnapshot().phase, "ready"); assert.equal(recovery.getSnapshot().data.status, "none"); }
  finally { recovery.release(); globalThis.fetch = original; }
});
