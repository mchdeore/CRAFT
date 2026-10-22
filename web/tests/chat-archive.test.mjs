import test from 'node:test';
import assert from 'node:assert/strict';
import './typescript-loader.mjs';
import { confirmChatArchive } from '../src/lib/chat-archive.ts';

for (const accepted of [false, true]) test(`archive ${accepted ? 'proceeds after confirmation' : 'cancellation preserves the chat and files'}`, t => {
  const previous = globalThis.window;
  t.after(() => { if (previous === undefined) delete globalThis.window; else globalThis.window = previous; });
  let calls = 0;
  globalThis.window = { confirm(message) {
    assert.match(message, /deletes its workspace files/);
    assert.match(message, /Restoring the chat will not restore those files/);
    return accepted;
  } };
  confirmChatArchive(() => { calls++; });
  assert.equal(calls, accepted ? 1 : 0);
});
