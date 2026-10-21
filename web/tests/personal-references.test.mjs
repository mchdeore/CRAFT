import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import React from "react";
import { AssistantRuntimeProvider, ComposerPrimitive, useAuiState, useExternalStoreRuntime, unstable_useTriggerPopoverTriggers } from "@assistant-ui/react";
import "./typescript-loader.mjs";
import { mountedHost } from "./source-import-host.mjs";
const helperURL = new URL("../src/lib/personal-references.ts", import.meta.url);
const helpers = existsSync(helperURL) ? await import(helperURL) : {};
const pickerURL = new URL("../src/components/personal-reference-pickers.tsx", import.meta.url);
const view = existsSync(pickerURL) ? await import(pickerURL) : {};
const entry = (path, type = "file") => ({ path, name: path.split("/").at(-1), type, size: 12, writable: true });
const configured = { status: "configured", url: "http://127.0.0.1:58101/lab" };
const json = body => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(predicate) { for (let attempt = 0; attempt < 40; attempt++) { await React.act(async () => { await tick(); }); if (predicate()) return; } assert.fail("Personal reference picker did not settle"); }

test("personal reference adapters contain only file paths and slash admits only direct Markdown skills", () => {
  assert.equal(typeof helpers.personalReferenceAdapter, "function");
  const files = [entry("skills/check.md"), entry("imports/report.xlsx"), entry("notebooks/analysis.ipynb", "notebook"), entry("skills/example.txt"), entry("skills/nested/check.md"), entry("skills/folder", "directory")];
  assert.deepEqual(helpers.personalReferenceAdapter(files, "skills").search("").map(item => item.id), ["skills/check.md"]);
  assert.deepEqual(helpers.personalReferenceAdapter(files, "files").search("report").map(item => item.id), ["imports/report.xlsx"]);
  assert.equal(helpers.personalReferenceAdapter(files, "files").search("").some(item => item.type === "tool" || item.id === "skills/folder"), false);
  assert.equal(helpers.personalPathFormatter.serialize({ id: "imports/My report.xlsx" }), '"/workspace/imports/My report.xlsx"');
  assert.deepEqual(helpers.personalPathFormatter.parse("Read /workspace/skills/check.md"), [{ kind: "text", text: "Read /workspace/skills/check.md" }]);
});

test("reference search matches words in any order and validates each exact personal path", () => {
  const rows = [entry("imports/My report.xlsx"), entry("outputs/report.xlsx"), entry("../foreign.md"), entry("imports/%2e%2e/foreign.md")];
  assert.deepEqual(helpers.personalReferenceAdapter(rows, "files").search("report imports").map(item => item.id), ["imports/My report.xlsx"]);
  assert.deepEqual(helpers.personalReferenceAdapter(rows, "files").search("/workspace/imports/My report.xlsx").map(item => item.id), ["imports/My report.xlsx"]);
  assert.equal(helpers.personalReferenceAdapter(rows, "files").search("").length, 2);
});

test("reference trigger insertion retains surrounding chat text and the selected caret", () => {
  assert.equal(typeof helpers.insertPersonalReferenceTrigger, "function");
  assert.deepEqual(helpers.insertPersonalReferenceTrigger("Read this file later", "files", 5, 14), { content: "Read @ later", caret: 6 });
  assert.deepEqual(helpers.insertPersonalReferenceTrigger("Use", "skills", 3), { content: "Use /", caret: 5 });
  assert.throws(() => helpers.insertPersonalReferenceTrigger("Read", "files", -1), /selection/);
});

for (const [char, query, target] of [["/", "che", "skills/check.md"], ["@", "rep", "imports/report.xlsx"]]) test(`actual installed composer ${char} picker inserts only the exact personal path in message text`, async () => {
  assert.equal(typeof view.PersonalReferencePickers, "function");
  const host = mountedHost(); const oldFetch = globalThis.fetch; const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    const path = new URL(url).pathname.slice("/api/contents/".length);
    const rows = path === "" ? [entry("imports", "directory"), entry("skills", "directory")] : path === "skills" ? [entry("skills/check.md"), entry("skills/example.txt")] : [entry("imports/report.xlsx")];
    return json({ type: "directory", path, content: rows });
  };
  const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container); let runtime, triggers;
  function Probe() { triggers = unstable_useTriggerPopoverTriggers(); return null; }
  function Harness() { runtime = useExternalStoreRuntime({ messages: [], isRunning: false, onNew: async () => {} }); return React.createElement(AssistantRuntimeProvider, { runtime }, React.createElement(ComposerPrimitive.TriggerPopoverRoot, null, React.createElement(view.PersonalReferencePickers, { configuration: configured }), React.createElement(Probe))); }
  try {
    await React.act(async () => root.render(React.createElement(Harness)));
    assert.equal(requests.length, 0, "Closed pickers should not scan personal files");
    const text = `Before ${char}${query} after`;
    await React.act(async () => { runtime.thread.composer.setText(text); triggers.get(char).resource.setCursorPosition(8 + query.length); });
    await settle(() => host.elements("button").some(row => row.getAttribute("data-personal-path") === target));
    const choices = host.elements("button").filter(row => row.getAttribute("data-personal-path"));
    assert.deepEqual(choices.map(row => row.getAttribute("data-personal-path")), [target]);
    await React.act(async () => choices[0].dispatchEvent({ type: "click", bubbles: true, button: 0 }));
    const state = runtime.thread.composer.getState();
    assert.equal(state.text, `Before /workspace/${target} after`); assert.equal(state.attachments.length, 0); assert.deepEqual(state.runConfig, {});
    assert.ok(requests.every(row => row.url.startsWith("http://127.0.0.1:58101/api/contents/") && row.options.credentials === "include"));
    assert.equal(requests.some(row => row.url.startsWith("/api/skills") || row.url.startsWith("/api/library")), false);
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

async function mountReferenceControls(configuration = configured) {
  const host = mountedHost();
  const prototype = Object.getPrototypeOf(host.container);
  prototype.focus = function () { this.ownerDocument.activeElement = this; };
  prototype.setSelectionRange = function (start, end) { this.selectionStart = start; this.selectionEnd = end; };
  prototype.scrollIntoView = function () {};
  const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container); let runtime, triggers, input;
  function Probe() { triggers = unstable_useTriggerPopoverTriggers(); return null; }
  function ComposerContent() {
    const inputRef = React.useRef(null); const text = useAuiState(state => state.composer.text);
    React.useEffect(() => { input = inputRef.current; });
    return React.createElement(React.Fragment, null, React.createElement(view.PersonalReferencePickers, { configuration }), view.PersonalReferenceControl && React.createElement(view.PersonalReferenceControl, { configuration, inputRef }), React.createElement("textarea", { value: text, onChange() {}, ref: inputRef }), React.createElement(Probe));
  }
  function Harness() { runtime = useExternalStoreRuntime({ messages: [], isRunning: false, onNew: async () => {} }); return React.createElement(AssistantRuntimeProvider, { runtime }, React.createElement(ComposerPrimitive.TriggerPopoverRoot, null, React.createElement(ComposerContent))); }
  async function render(next = configuration) { configuration = next; await React.act(async () => root.render(React.createElement(Harness))); }
  await render();
  return { host, render, runtime: () => runtime, triggers: () => triggers, input: () => input, async click(label) { const button = host.elements("button").find(row => row.textContent === label || row.getAttribute("aria-label") === label); assert.ok(button, label); await React.act(async () => { button.dispatchEvent({ type: "click", bubbles: true, button: 0 }); await tick(); }); }, async type(text, char, caret = text.length) { await React.act(async () => { runtime.thread.composer.setText(text); triggers.get(char).resource.setCursorPosition(caret); }); }, async key(char, key, shiftKey = false) { const event = { key, shiftKey, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }; let consumed; await React.act(async () => { consumed = triggers.get(char).resource.handleKeyDown(event); await tick(); }); return { ...event, consumed }; }, async dispose() { try { await React.act(async () => root.unmount()); } finally { host.restore(); } } };
}
const pickerOpen = (mounted, char) => mounted.host.elements("div").some(row => row.getAttribute("role") === "listbox" && row.getAttribute("aria-label") === (char === "/" ? "Personal Markdown skills" : "Personal workspace files"));
const highlightedPath = mounted => mounted.host.elements("button").find(row => row.getAttribute("data-personal-path") && row.getAttribute("data-highlighted") === "")?.getAttribute("data-personal-path");

for (const [char, query, target] of [["@", "report imports", "imports/My report.xlsx"], ["/", "My check", "skills/My check.md"]]) test(`mounted ${char} picker keeps multi-word name/path queries open and replaces only through the caret`, async () => {
  const oldFetch = globalThis.fetch; const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options }); const path = new URL(url).pathname.slice("/api/contents/".length);
    return json({ type: "directory", path, content: path === "" ? [entry("imports", "directory")] : path === "imports" ? [entry("imports/My report.xlsx"), entry("imports/notes.txt")] : [entry("skills/My check.md"), entry("skills/other.md")] });
  };
  const mounted = await mountReferenceControls();
  try {
    const firstWord = query.split(" ")[0];
    await mounted.type(`Before ${char}${firstWord} after`, char, 8 + firstWord.length);
    await settle(() => mounted.host.elements("button").some(row => row.getAttribute("data-personal-path") === target));
    const text = `Before ${char}${query} after`, caret = 8 + query.length;
    await mounted.type(text, char, caret);
    assert.equal(pickerOpen(mounted, char), true, "A space in the query must keep the installed picker open");
    assert.deepEqual(mounted.host.elements("button").filter(row => row.getAttribute("data-personal-path")).map(row => row.getAttribute("data-personal-path")), [target]);
    assert.deepEqual(mounted.triggers().get(char).matcher(text, char, caret), { query, offset: 7, endOffset: caret });
    const event = await mounted.key(char, "Enter"); assert.equal(event.consumed, true); assert.equal(event.defaultPrevented, true);
    const state = mounted.runtime().thread.composer.getState();
    const inserted = `Before "/workspace/${target}" after`;
    assert.equal(state.text, inserted); assert.equal(state.attachments.length, 0); assert.deepEqual(state.runConfig, {});
    for (const trigger of ["@", "/"]) {
      await mounted.type(inserted, trigger); assert.equal(pickerOpen(mounted, trigger), false);
      await mounted.type(inserted + " more", trigger); assert.equal(pickerOpen(mounted, trigger), false);
    }
    assert.ok(requests.every(row => row.options.credentials === "include"));
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("mounted multi-word skill query retains arrow, Shift+Enter and Escape behavior without reopening exact paths", async () => {
  const oldFetch = globalThis.fetch; globalThis.fetch = async () => json({ type: "directory", path: "skills", content: [entry("skills/My check Alpha.md"), entry("skills/My check Beta.md"), entry("skills/check.md")] });
  const mounted = await mountReferenceControls();
  try {
    const text = "Before /My check after";
    await mounted.type(text, "/", 16);
    await settle(() => mounted.host.elements("button").filter(row => row.getAttribute("data-personal-path")).length === 2);
    const newline = await mounted.key("/", "Enter", true); assert.equal(newline.consumed, false); assert.equal(newline.defaultPrevented, false);
    await mounted.key("/", "ArrowDown"); assert.equal(highlightedPath(mounted), "skills/My check Beta.md");
    await mounted.key("/", "ArrowUp"); assert.equal(highlightedPath(mounted), "skills/My check Alpha.md");
    const escape = await mounted.key("/", "Escape"); assert.equal(escape.consumed, true); assert.equal(escape.defaultPrevented, true);
    assert.equal(mounted.runtime().thread.composer.getState().text, text); assert.equal(pickerOpen(mounted, "/"), false);
    await mounted.type("Before /check after", "/", 13);
    await settle(() => mounted.host.elements("button").some(row => row.getAttribute("data-personal-path") === "skills/check.md"));
    await mounted.key("/", "Enter"); assert.equal(mounted.runtime().thread.composer.getState().text, "Before /workspace/skills/check.md after");
    await mounted.type("Before /workspace/skills/check.md after more", "/"); assert.equal(pickerOpen(mounted, "/"), false);
    await mounted.type("Before /My check\nafter", "/"); assert.equal(pickerOpen(mounted, "/"), false);
    const latest = "Before @report /My check after", caret = latest.indexOf(" after");
    await mounted.type(latest, "@", caret); await mounted.type(latest, "/", caret);
    assert.equal(pickerOpen(mounted, "@"), false); assert.equal(pickerOpen(mounted, "/"), true);
    await mounted.key("/", "Escape"); assert.equal(pickerOpen(mounted, "/"), false); assert.equal(pickerOpen(mounted, "@"), false);
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("visible Add references control opens file choices at the caret and inserts a quoted exact path", async () => {
  assert.equal(typeof view.PersonalReferenceControl, "function");
  const oldFetch = globalThis.fetch; const requests = [];
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); const path = new URL(url).pathname.slice("/api/contents/".length); return json({ type: "directory", path, content: path === "" ? [entry("imports", "directory")] : [entry("imports/My report.xlsx")] }); };
  const mounted = await mountReferenceControls();
  try {
    assert.equal(requests.length, 0);
    await React.act(async () => { mounted.runtime().thread.composer.setText("Review tail"); }); mounted.input().setSelectionRange(7, 7);
    await mounted.click("Add references"); await mounted.click("Add workspace file");
    assert.equal(mounted.runtime().thread.composer.getState().text, "Review @ tail"); assert.equal(mounted.input().selectionStart, 8); assert.equal(mounted.host.container.ownerDocument.activeElement, mounted.input());
    await settle(() => mounted.host.elements("button").some(row => row.getAttribute("data-personal-path") === "imports/My report.xlsx"));
    const event = await mounted.key("@", "Tab"); assert.equal(event.consumed, true); assert.equal(event.defaultPrevented, true);
    const state = mounted.runtime().thread.composer.getState(); assert.equal(state.text, 'Review "/workspace/imports/My report.xlsx" tail'); assert.equal(state.attachments.length, 0); assert.deepEqual(state.runConfig, {});
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("Add Markdown skill offers only owned Markdown files and quotes a skill name with spaces", async () => {
  const oldFetch = globalThis.fetch; const paths = [];
  globalThis.fetch = async url => { paths.push(new URL(url).pathname); return json({ type: "directory", path: "skills", content: [entry("skills/My check.md"), entry("skills/readme.txt"), entry("skills/nested", "directory")] }); };
  const mounted = await mountReferenceControls();
  try {
    await React.act(async () => mounted.runtime().thread.composer.setText("Use")); mounted.input().setSelectionRange(3, 3);
    await mounted.click("Add references"); await mounted.click("Add Markdown skill");
    await settle(() => mounted.host.elements("button").some(row => row.getAttribute("data-personal-path") === "skills/My check.md"));
    assert.deepEqual(mounted.host.elements("button").filter(row => row.getAttribute("data-personal-path")).map(row => row.getAttribute("data-personal-path")), ["skills/My check.md"]);
    await mounted.key("/", "Enter"); assert.equal(mounted.runtime().thread.composer.getState().text, 'Use "/workspace/skills/My check.md" '); assert.deepEqual(paths, ["/api/contents/skills"]);
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("slash keyboard arrows, Enter and Escape retain the original composer semantics", async () => {
  const oldFetch = globalThis.fetch; globalThis.fetch = async () => json({ type: "directory", path: "skills", content: [entry("skills/Alpha.md"), entry("skills/Beta.md"), entry("skills/plain.txt"), entry("skills/sub", "directory")] });
  const mounted = await mountReferenceControls();
  try {
    await mounted.type("Before / after", "/", 8);
    await settle(() => mounted.host.elements("button").filter(row => row.getAttribute("data-personal-path")).length === 2);
    await mounted.key("/", "ArrowDown"); await mounted.key("/", "Enter"); assert.equal(mounted.runtime().thread.composer.getState().text, "Before /workspace/skills/Beta.md after");
    await mounted.type("Before /alp after", "/", 11);
    await settle(() => mounted.host.elements("button").some(row => row.getAttribute("data-personal-path") === "skills/Alpha.md"));
    const newline = await mounted.key("/", "Enter", true); assert.equal(newline.consumed, false); assert.equal(newline.defaultPrevented, false);
    await mounted.key("/", "Escape"); assert.equal(mounted.runtime().thread.composer.getState().text, "Before /alp after"); assert.equal(mounted.host.elements("button").some(row => row.getAttribute("data-personal-path")), false);
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("picker auth errors expose a retry that recovers without inserting unavailable choices", async () => {
  const oldFetch = globalThis.fetch; let authorized = false, calls = 0;
  globalThis.fetch = async () => { calls++; return authorized ? json({ type: "directory", path: "skills", content: [entry("skills/check.md")] }) : new Response("private response body", { status: 403 }); };
  const mounted = await mountReferenceControls();
  try {
    await mounted.type("/", "/"); await settle(() => /sign in/i.test(mounted.host.container.textContent));
    assert.equal(mounted.host.elements("button").some(row => row.getAttribute("data-personal-path")), false); assert.doesNotMatch(mounted.host.container.textContent, /private response body/);
    authorized = true; await mounted.click("Retry"); await settle(() => mounted.host.elements("button").some(row => row.getAttribute("data-personal-path") === "skills/check.md")); assert.equal(calls, 2);
    await mounted.key("/", "Enter"); assert.equal(mounted.runtime().thread.composer.getState().text, "/workspace/skills/check.md ");
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("picker owner switches and closure abort old listings before late choices can appear", async () => {
  const oldFetch = globalThis.fetch; const pending = [];
  globalThis.fetch = (url, options) => new Promise(resolve => pending.push({ url, options, resolve }));
  const mounted = await mountReferenceControls();
  try {
    await mounted.type("/", "/"); await settle(() => pending.length === 1);
    await mounted.render({ status: "configured", url: "http://127.0.0.1:58102/lab" }); await mounted.type("/", "/"); await settle(() => pending.length === 2); assert.equal(pending[0].options.signal.aborted, true);
    pending[0].resolve(json({ type: "directory", path: "skills", content: [entry("skills/stale.md")] })); await React.act(async () => { await tick(); }); assert.doesNotMatch(mounted.host.container.textContent, /stale.md/);
    await mounted.key("/", "Escape"); assert.equal(pending[1].options.signal.aborted, true);
    pending[1].resolve(json({ type: "directory", path: "skills", content: [entry("skills/closed.md")] })); await React.act(async () => { await tick(); }); assert.doesNotMatch(mounted.host.container.textContent, /closed.md/);
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});
