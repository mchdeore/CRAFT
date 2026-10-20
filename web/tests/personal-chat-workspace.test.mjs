import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import React from "react";
import ExcelJS from "exceljs";
import { AssistantRuntimeProvider, MessagePrimitive, ThreadPrimitive, useExternalStoreRuntime } from "@assistant-ui/react";
import "./typescript-loader.mjs";
import { mountedHost } from "./source-import-host.mjs";
import { CitationProvider } from "../src/components/citations/citation-context.tsx";
import { MarkdownText } from "../src/components/assistant-ui/elements/markdown-text.tsx";

const helperURL = new URL("../src/lib/chat-workspace-preview.ts", import.meta.url);
const helpers = existsSync(helperURL) ? await import(helperURL) : {};
const viewURL = new URL("../src/components/personal-chat-workspace.tsx", import.meta.url);
const view = existsSync(viewURL) ? await import(viewURL) : {};
const configured = { status: "configured", url: "http://127.0.0.1:58101/lab" };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const bytesOf = text => new TextEncoder().encode(text);
const file = (path, bytes, mimeType = "text/plain") => ({ name: path.split("/").at(-1), path, type: "file", writable: true, format: "base64", content: Buffer.from(bytes).toString("base64").replace(/.{1,76}/g, line => line + "\n"), size: bytes.length, mimetype: mimeType });
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(predicate) { for (let count = 0; count < 100; count++) { await React.act(async () => { await tick(); }); if (predicate()) return; } assert.fail("Chat workspace preview did not settle"); }
function need(name) { assert.equal(typeof helpers[name], "function", `Missing preview helper ${name}`); return helpers[name]; }

test("workspace Markdown paths decode spaces once and reject foreign, escaping and query paths", () => {
  const parse = need("personalWorkspaceLinkPath");
  for (const [href, path] of [["/workspace/outputs/result.py", "outputs/result.py"], ["/workspace/imports/My%20report.xlsx", "imports/My report.xlsx"], ["/workspace/notebooks/caf%C3%A9.ipynb", "notebooks/café.ipynb"]]) assert.equal(parse(href), path);
  for (const href of [undefined, "", "/workspace", "/workspace/", "https://foreign.example/workspace/a.py", "http://127.0.0.1:58101/workspace/a.py", "//foreign.example/workspace/a.py", "workspace/a.py", " /workspace/a.py", "/workspace/../a.py", "/workspace/imports/./a.py", "/workspace/imports/%2e%2e/a.py", "/workspace/imports/%252e%252e/a.py", "/workspace/imports%2fa.py", "/workspace/imports%5ca.py", "/workspace/imports//a.py", "/workspace/a.py?download=1", "/workspace/a.py#fragment", "/workspace/a%3fsecret.py", "/workspace/a%00.py", "/workspace/a%0A.py", "/workspace/%ZZ.py", "/workspace/.hidden", "/workspace%2fa.py"]) assert.equal(parse(href), null, String(href));
});

test("Python preview preserves indentation, bounds text and treats HTML as code", () => {
  const preview = need("personalChatPreview")("outputs/result.py", bytesOf("def run():\n    return '<script>unsafe()</script>'\n"));
  assert.equal(preview.kind, "code"); assert.equal(preview.language, "Python");
  assert.equal(preview.text, "def run():\n    return '<script>unsafe()</script>'\n"); assert.equal(preview.truncated, false);
  const large = need("personalChatPreview")("outputs/large.py", bytesOf("x".repeat(200001)));
  assert.equal(large.truncated, true); assert.ok(large.text.length <= 100000);
  assert.throws(() => need("personalChatPreview")("outputs/broken.py", Uint8Array.of(255)), /UTF-8/);
});

test("notebook preview exposes cell sources and text outputs while reporting rich outputs", () => {
  const notebook = { nbformat: 4, nbformat_minor: 5, metadata: { kernelspec: { language: "python" } }, cells: [
    { cell_type: "markdown", metadata: {}, source: ["# Notes\n", "<script>literal()</script>"] },
    { cell_type: "code", metadata: {}, execution_count: 2, source: ["print('result')\n", "2 + 2"], outputs: [
      { output_type: "stream", name: "stdout", text: ["result", "\n"] },
      { output_type: "execute_result", execution_count: 2, metadata: {}, data: { "text/plain": "4", "text/html": "<b>4</b>" } },
      { output_type: "display_data", metadata: {}, data: { "image/png": "generated-image" } },
      { output_type: "error", ename: "ValueError", evalue: "generated failure", traceback: ["Traceback", "ValueError: generated failure"] },
    ] },
  ] };
  const preview = need("personalChatPreview")("notebooks/result.ipynb", bytesOf(JSON.stringify(notebook)));
  assert.equal(preview.kind, "notebook"); assert.equal(preview.cells.length, 2);
  assert.equal(preview.cells[0].source, "# Notes\n<script>literal()</script>");
  assert.equal(preview.cells[1].source, "print('result')\n2 + 2");
  assert.deepEqual(preview.cells[1].outputs, ["result\n", "4", "Traceback\nValueError: generated failure"]);
  assert.equal(preview.cells[1].unsupportedOutputs, 2); assert.equal(preview.truncated, false);
  assert.throws(() => need("personalChatPreview")("notebooks/invalid.ipynb", bytesOf(JSON.stringify({ nbformat: 4, cells: [{ cell_type: "code", source: [4], outputs: [] }] }))), /notebook/i);
});

test("notebook preview limits cell count, output count and total visible text", () => {
  const cells = Array.from({ length: 260 }, (_, index) => ({ cell_type: "code", source: "x".repeat(index === 0 ? 25000 : 1000), outputs: Array.from({ length: index === 0 ? 40 : 2 }, () => ({ output_type: "stream", name: "stdout", text: "y".repeat(index === 0 ? 10000 : 1000) })) }));
  const preview = need("personalChatPreview")("notebooks/large.ipynb", bytesOf(JSON.stringify({ nbformat: 4, nbformat_minor: 5, cells })));
  assert.equal(preview.kind, "notebook"); assert.equal(preview.truncated, true); assert.ok(preview.cells.length <= 200);
  assert.ok(preview.cells.every(cell => cell.outputs.length <= 20));
  assert.ok(preview.cells.reduce((size, cell) => size + cell.source.length + cell.outputs.join("").length, 0) <= 200000);
});

test("notebook errors with an empty traceback still expose their name and message", () => {
  const preview = need("personalChatPreview")("notebooks/error.ipynb", bytesOf(JSON.stringify({ nbformat: 4, cells: [{ cell_type: "code", source: "raise ValueError('invalid input')", outputs: [{ output_type: "error", ename: "ValueError", evalue: "invalid input", traceback: [] }] }] })));
  assert.deepEqual(preview.cells[0].outputs, ["ValueError: invalid input"]);
});

test("chat preview routes Office, PDF, PNG and ordinary text through existing document formats", () => {
  for (const [path, format] of [["imports/report.docx", "docx"], ["imports/report.xlsx", "xlsx"], ["outputs/report.pdf", "pdf"], ["outputs/plot.png", "png"], ["outputs/readme.md", "text"]]) assert.deepEqual(need("personalChatPreview")(path, Uint8Array.of(1)), { kind: "document", format });
  for (const path of ["outputs/app.html", "outputs/data.xml", "outputs/payload.bin"]) assert.equal(need("personalChatPreview")(path, Uint8Array.of(1)).kind, "unsupported");
});

async function mountPreview(markdown = "[result.py](/workspace/outputs/result.py)", configuration = configured, ownerKey = "owner-one") {
  assert.equal(typeof view.PersonalChatWorkspaceProvider, "function"); assert.equal(typeof view.PersonalChatPreviewPanel, "function");
  const host = mountedHost(); const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container);
  const Text = () => React.createElement(MarkdownText);
  const AssistantMessage = () => React.createElement(MessagePrimitive.Parts, { components: { Text } });
  let runtime;
  function Harness({ configuration, ownerKey, markdown }) {
    runtime = useExternalStoreRuntime({ messages: [{ id: "generated-answer", role: "assistant", createdAt: new Date("2026-10-20T00:00:00Z"), status: { type: "complete", reason: "stop" }, metadata: { custom: {} }, content: [{ type: "text", text: markdown }] }], isRunning: false, onNew: async () => {} });
    return React.createElement(AssistantRuntimeProvider, { runtime }, React.createElement(CitationProvider, null, React.createElement(view.PersonalChatWorkspaceProvider, { configuration, ownerKey }, React.createElement("div", { "data-chat-usable": "true" }, React.createElement(ThreadPrimitive.Messages, { components: { AssistantMessage } }), React.createElement("textarea", { "aria-label": "Still usable chat", defaultValue: "keep chatting" })), React.createElement(view.PersonalChatPreviewPanel))));
  }
  async function render(next = {}) { configuration = next.configuration ?? configuration; ownerKey = next.ownerKey ?? ownerKey; markdown = next.markdown ?? markdown; await React.act(async () => { root.render(React.createElement(Harness, { configuration, ownerKey, markdown })); await tick(); }); }
  await render();
  return { host, root, render, runtime: () => runtime, async click(label) { const element = [...host.elements("button"), ...host.elements("a")].find(row => row.getAttribute("aria-label") === label || row.textContent === label); assert.ok(element, label); const event = { type: "click", bubbles: true, button: 0 }; await React.act(async () => { element.dispatchEvent(event); await tick(); }); return event; }, async dispose() { try { await React.act(async () => root.unmount()); } finally { host.restore(); } } };
}

test("real assistant Markdown link opens current authenticated Python bytes without removing chat", async () => {
  const oldFetch = globalThis.fetch, createURL = URL.createObjectURL, revokeURL = URL.revokeObjectURL; const requests = [], blobs = [], revoked = [];
  let content = "def run():\n    return '<script>first()</script>'\n";
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return json(file("outputs/My result.py", bytesOf(content))); };
  URL.createObjectURL = blob => { blobs.push(blob); return createURL(blob); }; URL.revokeObjectURL = url => { revoked.push(url); revokeURL(url); };
  const mounted = await mountPreview("[My result.py](/workspace/outputs/My%20result.py) · [ordinary](https://example.test/help)");
  try {
    assert.equal(requests.length, 0);
    const event = await mounted.click("My result.py"); assert.equal(event.defaultPrevented, true);
    await settle(() => mounted.host.container.textContent.includes("first()"));
    assert.match(mounted.host.container.textContent, /Current workspace file/); assert.match(mounted.host.container.textContent, /Python/);
    assert.equal(mounted.host.elements("script").length, 0); assert.equal(mounted.host.elements("textarea").length, 1);
    assert.equal(mounted.host.elements("section").find(row => row.getAttribute("aria-label") === "Workspace file preview")?.getAttribute("aria-modal"), null);
    assert.ok(requests.every(row => row.url === "http://127.0.0.1:58101/api/contents/outputs/My%20result.py?content=1&format=base64" && row.options.credentials === "include" && row.options.redirect === "error" && row.options.cache === "no-store" && row.options.signal instanceof AbortSignal));
    const download = mounted.host.elements("a").find(row => row.getAttribute("download") === "My result.py"); assert.ok(download); const firstURL = download.getAttribute("href"); assert.match(firstURL, /^blob:/);
    assert.deepEqual(new Uint8Array(await blobs[0].arrayBuffer()), bytesOf(content));
    const ordinary = await mounted.click("ordinary"); assert.notEqual(ordinary.defaultPrevented, true); assert.equal(requests.length, 1);
    content = "def run():\n    return 'refreshed'\n"; await mounted.click("Refresh workspace preview");
    assert.equal(revoked.includes(firstURL), true); await settle(() => mounted.host.container.textContent.includes("refreshed")); assert.doesNotMatch(mounted.host.container.textContent, /first\(\)/);
    assert.equal(requests.length, 2); assert.deepEqual(new Uint8Array(await blobs[1].arrayBuffer()), bytesOf(content));
    await mounted.click("Close workspace preview"); assert.equal(mounted.host.elements("section").length, 0); assert.equal(revoked.length, 2); assert.equal(mounted.host.elements("textarea").length, 1);
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; URL.createObjectURL = createURL; URL.revokeObjectURL = revokeURL; }
});

test("chat Office preview renders the actual authenticated workbook", async () => {
  const workbook = new ExcelJS.Workbook(); workbook.addWorksheet("Generated result").addRow(["Workspace total", 42]);
  const bytes = new Uint8Array(await workbook.xlsx.writeBuffer()); const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => json(file("outputs/result.xlsx", bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"));
  const mounted = await mountPreview("[result.xlsx](/workspace/outputs/result.xlsx)");
  try { await mounted.click("result.xlsx"); await settle(() => mounted.host.container.textContent.includes("Workspace total")); assert.match(mounted.host.container.textContent, /Generated result/); assert.match(mounted.host.container.textContent, /42/); }
  finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("chat notebook preview reads raw Contents bytes and exposes safe cell text", async () => {
  const oldFetch = globalThis.fetch; const requests = [];
  const bytes = bytesOf(JSON.stringify({ nbformat: 4, nbformat_minor: 5, cells: [{ cell_type: "code", source: ["print('saved result')\n"], outputs: [{ output_type: "stream", name: "stdout", text: "saved result\n" }, { output_type: "display_data", data: { "text/html": "<script>must_not_run()</script>" }, metadata: {} }] }] }));
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return json(file("notebooks/My result.ipynb", bytes, "application/json")); };
  const mounted = await mountPreview("[notebook](/workspace/notebooks/My%20result.ipynb)");
  try { await mounted.click("notebook"); await settle(() => mounted.host.container.textContent.includes("saved result")); assert.match(mounted.host.container.textContent, /Cell 1/); assert.match(mounted.host.container.textContent, /Rich outputs/); assert.doesNotMatch(mounted.host.container.textContent, /must_not_run/); assert.equal(mounted.host.elements("script").length, 0); assert.equal(requests[0].url, "http://127.0.0.1:58101/api/contents/notebooks/My%20result.ipynb?content=1&format=base64&type=file"); }
  finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("owner changes and close abort pending preview reads and cannot display late bytes", async () => {
  const oldFetch = globalThis.fetch; const pending = [];
  globalThis.fetch = (url, options) => new Promise(resolve => pending.push({ url, options, resolve }));
  const mounted = await mountPreview();
  try {
    await mounted.click("result.py"); assert.equal(pending.length, 1);
    await mounted.render({ ownerKey: "owner-two", configuration: { status: "configured", url: "http://127.0.0.1:58102/lab" } }); assert.equal(pending[0].options.signal.aborted, true); assert.equal(mounted.host.elements("section").length, 0);
    pending[0].resolve(json(file("outputs/result.py", bytesOf("foreign_stale_content")))); await React.act(async () => { await tick(); }); assert.doesNotMatch(mounted.host.container.textContent, /foreign_stale_content/);
    await mounted.click("result.py"); assert.equal(pending.length, 2); await mounted.click("Close workspace preview"); assert.equal(pending[1].options.signal.aborted, true);
    pending[1].resolve(json(file("outputs/result.py", bytesOf("closed_late_content")))); await React.act(async () => { await tick(); }); assert.doesNotMatch(mounted.host.container.textContent, /closed_late_content/);
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("changing preview selection cancels the prior path before accepting the current result", async () => {
  const oldFetch = globalThis.fetch; const pending = [];
  globalThis.fetch = (url, options) => new Promise(resolve => pending.push({ url, options, resolve }));
  const mounted = await mountPreview("[first](/workspace/outputs/first.py) [second](/workspace/outputs/second.py)");
  try {
    await mounted.click("first"); await mounted.click("second"); assert.equal(pending[0].options.signal.aborted, true);
    pending[0].resolve(json(file("outputs/first.py", bytesOf("stale_first")))); pending[1].resolve(json(file("outputs/second.py", bytesOf("current_second"))));
    await settle(() => mounted.host.container.textContent.includes("current_second")); assert.doesNotMatch(mounted.host.container.textContent, /stale_first/);
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("a ready preview loses its old download and content immediately on an owner change", async () => {
  const oldFetch = globalThis.fetch, revokeURL = URL.revokeObjectURL; const revoked = [];
  URL.revokeObjectURL = url => { revoked.push(url); revokeURL(url); };
  globalThis.fetch = async () => json(file("outputs/result.py", bytesOf("old_owner_ready_content")));
  const mounted = await mountPreview();
  try {
    await mounted.click("result.py"); await settle(() => mounted.host.container.textContent.includes("old_owner_ready_content"));
    const url = mounted.host.elements("a").find(row => row.getAttribute("download"))?.getAttribute("href"); assert.match(url, /^blob:/);
    await mounted.render({ ownerKey: "replacement-owner" });
    assert.doesNotMatch(mounted.host.container.textContent, /old_owner_ready_content/); assert.equal(mounted.host.elements("a").some(row => row.getAttribute("download")), false); assert.equal(revoked.includes(url), true);
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; URL.revokeObjectURL = revokeURL; }
});

test("an auth failure during refresh clears prior bytes and recovers through explicit retry", async () => {
  const oldFetch = globalThis.fetch; let authenticated = true, calls = 0;
  globalThis.fetch = async () => { calls++; return authenticated ? json(file("outputs/result.py", bytesOf(calls === 1 ? "original_ready_content" : "retried_current_content"))) : new Response("private workspace response", { status: 403 }); };
  const mounted = await mountPreview();
  try {
    await mounted.click("result.py"); await settle(() => mounted.host.container.textContent.includes("original_ready_content"));
    authenticated = false; await mounted.click("Refresh workspace preview"); await settle(() => mounted.host.container.textContent.includes("sign in"));
    assert.doesNotMatch(mounted.host.container.textContent, /original_ready_content|private workspace response/); assert.equal(mounted.host.elements("a").some(row => row.getAttribute("download")), false);
    authenticated = true; await mounted.click("Retry workspace preview"); await settle(() => mounted.host.container.textContent.includes("retried_current_content")); assert.equal(calls, 3);
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("source citations retain CitationLink interception and do not request owned files", async () => {
  const oldFetch = globalThis.fetch; const requests = []; globalThis.fetch = async url => { requests.push(url); return json({}); };
  const mounted = await mountPreview("Citation [1](/api/citations/generated-reference). [external](https://foreign.example/workspace/result.py?query=1)");
  try {
    const event = await mounted.click("1"); assert.equal(event.defaultPrevented, true); assert.equal(mounted.host.elements("section").length, 0); assert.deepEqual(requests, []);
    const ordinary = await mounted.click("external"); assert.notEqual(ordinary.defaultPrevented, true); assert.deepEqual(requests, []);
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});

test("missing, unsupported, invalid and failed previews report their actual state and allow retry", async () => {
  const oldFetch = globalThis.fetch; let status = 404, bytes = bytesOf("plain source"), calls = 0;
  globalThis.fetch = async () => { calls++; return status === 200 ? json(file("outputs/result.bin", bytes)) : json({}, status); };
  const mounted = await mountPreview("[result](/workspace/outputs/result.bin) [unsafe](/workspace/outputs/%2e%2e/secret)");
  try {
    await mounted.click("result"); await settle(() => mounted.host.elements("p").some(row => row.getAttribute("role") === "alert")); assert.match(mounted.host.container.textContent, /no longer available/); assert.equal(mounted.host.elements("a").some(row => row.getAttribute("download")), false);
    status = 200; await mounted.click("Retry workspace preview"); await settle(() => mounted.host.container.textContent.includes("Preview is unavailable for this format")); assert.ok(mounted.host.elements("a").some(row => row.getAttribute("download") === "result.bin")); assert.equal(calls, 2);
    await mounted.render({ configuration: { status: "missing" } }); assert.equal(mounted.host.elements("section").length, 0);
    await mounted.click("result"); assert.match(mounted.host.container.textContent, /Connect your personal workspace/); assert.equal(calls, 2); assert.equal(mounted.host.elements("a").some(row => row.getAttribute("download")), false);
    assert.equal(mounted.host.elements("a").some(row => row.textContent === "unsafe"), false);
  } finally { await mounted.dispose(); globalThis.fetch = oldFetch; }
});
