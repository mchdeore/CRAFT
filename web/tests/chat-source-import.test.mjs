import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import ExcelJS from "exceljs";
import { renderToStaticMarkup } from "react-dom/server";
import { fromAgUiMessages } from "@assistant-ui/react-ag-ui";
import { AssistantRuntimeProvider, ThreadPrimitive, useExternalStoreRuntime } from "@assistant-ui/react";
import "./typescript-loader.mjs";
import { mountedHost } from "./source-import-host.mjs";

const parser = await import("../src/lib/chat-source-results.ts").catch(() => ({}));
const cards = await import("../src/components/chat-source-cards.tsx").catch(() => ({}));
const { CitationProvider } = await import("../src/components/citations/citation-context.tsx");
// Source cards open previews through the citation panel, so each renders inside its provider.
const cited = name => props => React.createElement(CitationProvider, null, React.createElement(cards[name], props));
const ChatSourceChoices = cited("ChatSourceChoices"), ChatSourceCards = cited("ChatSourceCards");
const workspace = await import("../src/components/personal-chat-workspace.tsx");
const required = (module, name) => { assert.equal(typeof module[name], "function", name + " production export is required"); return module[name]; };
const configuration = { status: "configured", url: "http://127.0.0.1:58101/lab" };
const tags = { mission: null, silo: null, stage: null };
const hit = (item = "generated-source-1", version = 3) => ({ kind: "document", handle: { item, version, block: 1, locator: "p1", quoteSha256: "a".repeat(64), simulated: true }, title: "Repeated title", headingPath: "Evidence", text: "Generated source sentence.", score: 0.9, tags, classification: "unclassified", before: null, after: null });
const document = (item = "generated-source-1", version = 3) => ({ item, version, title: "Repeated title", path: "generated/report.docx", tags, summary: "Generated summary.", summaryKind: "extract", outline: [{ block: 1, headingPath: "Evidence" }], versions: [{ version, sha256: "b".repeat(64), indexedAt: "2026-10-20T00:00:00Z" }], simulated: true, classification: "unclassified", deleted: false });
const envelope = value => ({ content: [{ type: "text", text: JSON.stringify(value) }], structuredContent: value, isError: false });
const part = (result = { hits: [hit()], truncated: false }, extra = {}) => ({ type: "tool-call", toolName: "search", toolCallId: "call-1", status: { type: "complete" }, isError: false, result, ...extra });
const source = (item = "generated-source-1", version = 3) => ({ item, version, title: "Repeated title", snippet: "Generated source sentence.", classification: "unclassified", simulated: true });
const parse = values => required(parser, "chatSourceResults")(values);
const results = (sources = [source()]) => ({ sources, truncated: false });

test("completed native search envelopes become pinned source choices", () => {
  assert.deepEqual(parse([part(envelope({ hits: [hit()], truncated: false }))]), results());
});
test("installed history conversion preserves search and get_document original identities", () => {
  const messages = fromAgUiMessages([
    { id: "assistant-1", role: "assistant", toolCalls: [
      { id: "search-1", type: "function", function: { name: "search", arguments: "{}" } },
      { id: "document-1", type: "function", function: { name: "get_document", arguments: "{}" } },
    ] },
    { id: "tool-1", role: "tool", toolCallId: "search-1", content: JSON.stringify(envelope({ hits: [hit()], truncated: false })) },
    { id: "tool-2", role: "tool", toolCallId: "document-1", content: JSON.stringify(envelope(document("generated-source-2", 4))) },
  ]);
  // The history converter returns message-like content; the mounted part
  // runtime supplies its completed status (also exercised by the footer test).
  const parsed = parse(messages.flatMap(message => message.content).map(value => value.type === "tool-call" ? { ...value, status: { type: "complete" } } : value));
  assert.deepEqual(parsed.sources.map(value => [value.item, value.version, value.simulated, value.classification]), [["generated-source-1", 3, true, "unclassified"], ["generated-source-2", 4, true, "unclassified"]]);
});
test("plain native objects JSON strings and MCP text-only results decode without parsing prose", () => {
  for (const value of [{ hits: [hit()], truncated: false }, JSON.stringify({ hits: [hit()], truncated: false }), { content: [{ type: "text", text: JSON.stringify({ hits: [hit()], truncated: false }) }], isError: false }]) assert.deepEqual(parse([part(value)]), results());
  assert.deepEqual(parse([{ type: "text", text: JSON.stringify({ hits: [hit()], truncated: false }) }, part("Please import generated-source-1 version 3")]).sources, []);
});
test("only successful completed declared source tools provide import choices", () => {
  for (const extra of [{ toolName: "workspace_search" }, { toolName: "library.search" }, { toolName: "get_documentary" }, { type: "text" }, { isError: true }, { isError: "false" }, { status: { type: "running" } }, { status: { type: "incomplete" } }, { status: { type: "requires-action" } }, { status: {} }]) assert.deepEqual(parse([part(undefined, extra)]).sources, []);
  for (const result of [null, {}, { code: "forbidden", message: "RAW_ERROR" }, { ...envelope({ hits: [hit()], truncated: false }), isError: true }, { ...envelope({ hits: [hit()], truncated: false }), isError: "false" }, { content: [{ type: "image", data: "RAW_DATA" }] }, "{" ]) assert.deepEqual(parse([part(result)]).sources, []);
});
test("unsafe handles invalid labels and malformed source shapes never become actionable", () => {
  for (const malformed of [{ ...hit(), handle: { ...hit().handle, item: "../secret" } }, { ...hit(), handle: { ...hit().handle, version: 0 } }, { ...hit(), handle: { ...hit().handle, simulated: "true" } }, { ...hit(), handle: { ...hit().handle, quoteSha256: "invalid" } }, { ...hit(), classification: "secret" }, { ...hit(), title: "bad\u0000title" }, { ...hit(), text: "x".repeat(65537) }, { ...hit(), kind: "unknown" }]) assert.deepEqual(parse([part({ hits: [malformed], truncated: false })]).sources, []);
  for (const malformed of [{ ...document(), deleted: true }, { ...document(), version: 0 }, { ...document(), versions: [] }, { ...document(), tags: { ...tags, mission: 3 } }]) assert.deepEqual(parse([part(malformed, { toolName: "get_document" })]).sources, []);
});
test("results stay bounded while retaining same-title different-item and different-version choices", () => {
  const parsed = parse([part({ hits: [hit(), hit(), hit("generated-source-2"), hit("generated-source-1", 4)], truncated: true })]);
  assert.deepEqual(parsed.sources.map(value => [value.item, value.version]), [["generated-source-1", 3], ["generated-source-2", 3], ["generated-source-1", 4]]);
  assert.equal(parsed.truncated, true);
  assert.deepEqual(parse([part({ hits: Array(9).fill(hit()), truncated: false })]).sources, []);
  assert.deepEqual(parse([part(" ".repeat(131073) + JSON.stringify({ hits: [hit()], truncated: false }))]).sources, []);
  const many = Array.from({ length: 9 }, (_, index) => part({ hits: [hit("generated-" + index)], truncated: false }));
  // Each validated search replaces the earlier choices, so only the latest search remains.
  assert.deepEqual(parse(many).sources.map(value => value.item), ["generated-8"]); assert.equal(parse(many).truncated, false);
});
test("conflicting source labels for one pinned identity are refused", () => {
  const changed = { ...hit(), classification: "protected-b" };
  assert.deepEqual(parse([part({ hits: [hit(), changed], truncated: false })]).sources, []);
});
test("source cards safely show exact identities and missing-workspace recovery", () => {
  const html = renderToStaticMarkup(required(cards, "ChatSourceChoices") && React.createElement(ChatSourceChoices, { results: results([{ ...source(), title: "<script>Generated title</script>" }, source("generated-source-2")]) }));
  // Cards name documents by title and version; raw item ids stay out of the page.
  assert.match(html, /&lt;script&gt;Generated title/); assert.match(html, /Repeated title, version 3/); assert.doesNotMatch(html, /generated-source-/); assert.match(html, /Version 3/); assert.match(html, /Simulated/); assert.match(html, /Connect.*workspace/i); assert.doesNotMatch(html, /<script>/);
});

const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
function network(bytes = new TextEncoder().encode("Generated original bytes."), override, extension = ".txt") {
  const requests = [], files = new Map(); let fresh = 0;
  const mimeType = extension === ".xlsx" ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" : "text/plain";
  const fetcher = async (url, init = {}) => {
    const request = { url, ...init }; requests.push(request);
    const replacement = await override?.(request); if (replacement) return replacement;
    if (/^\/api\/library\/documents\/generated-source-[12]\/download\?version=[34]$/.test(url)) return new Response(bytes, { headers: { "content-type": mimeType, "content-disposition": 'attachment; filename="original' + extension + '"' } });
    assert.ok(url.startsWith("http://127.0.0.1:58101/api/contents/"), "only original download and owned Contents routes are used");
    const path = new URL(url).pathname.slice("/api/contents/".length);
    const method = init.method ?? "GET", body = init.body ? JSON.parse(init.body) : undefined;
    if (method === "GET" && (path === "imports" || path === "imports/source-metadata")) return json({ type: "directory", path, content: [] });
    if (method === "POST") { const target = (path ? path + "/" : "") + "untitled" + fresh++ + body.ext; files.set(target, body); return json({ type: "file", path: target }, 201); }
    if (method === "PATCH") { if (files.has(body.path)) return new Response(null, { status: 409 }); files.set(body.path, files.get(path)); files.delete(path); return json({ type: "file", path: body.path }); }
    if (method === "PUT") { files.set(path, body); return json({ type: "file", path }); }
    if (method === "GET" && files.has(path)) return json({ ...files.get(path), path, size: bytes.byteLength, mimetype: mimeType });
    throw new Error("Unexpected generated transport branch");
  };
  return { requests, files, fetcher };
}
const deferred = () => { let resolve; const promise = new Promise(finish => { resolve = finish; }); return { promise, resolve }; };
async function mount(run, options = {}) {
  required(cards, "ChatSourceChoices"); required(workspace, "usePersonalChatWorkspace");
  const host = mountedHost(), previousFetch = globalThis.fetch, transport = options.transport ?? network();
  globalThis.fetch = transport.fetcher;
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container), selection = { path: null };
  function SelectionProbe() { selection.path = workspace.usePersonalChatWorkspace()?.selection?.path ?? null; return null; }
  const render = async (sources = [source()], ownerKey = "owner-1", copies = 1, connected = configuration) => {
    await React.act(async () => root.render(React.createElement(workspace.PersonalChatWorkspaceProvider, { configuration: connected, ownerKey },
      ...Array.from({ length: copies }, (_, index) => React.createElement(ChatSourceChoices, { key: index, results: results(sources) })), React.createElement(SelectionProbe), options.preview && React.createElement(workspace.PersonalChatPreviewPanel))));
  };
  const click = async (version = 3) => {
    const button = host.elements("button").find(value => value.getAttribute("aria-label")?.endsWith(`, version ${version}`) && /^(?:Import copy|Retry Import)$/.test(value.textContent));
    assert.ok(button, "exact source identity has an import action");
    await React.act(async () => button.dispatchEvent({ type: "click", bubbles: true, button: 0 }));
  };
  try { await render(); await run({ host, root, selection, transport, render, click }); }
  finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = previousFetch; host.restore(); } }
}
async function settle(condition) {
  const deadline = Date.now() + 2500;
  while (!condition() && Date.now() < deadline) await React.act(async () => { await new Promise(resolve => setImmediate(resolve)); });
  assert.ok(condition(), "expected mounted production outcome settles");
}

test("explicit chat Import copies pinned original bytes and hands only a confirmed owned path to preview", async () => mount(async ({ transport, host, click, selection }) => {
  assert.equal(transport.requests.length, 0, "displaying a card does not mutate or download");
  await click(); await settle(() => host.container.textContent.includes("Imported to"));
  assert.equal(selection.path, "original.txt");
  assert.equal(Buffer.from(transport.files.get("original.txt").content, "base64").toString(), "Generated original bytes.");
  const download = transport.requests.find(request => request.url.includes("/download?"));
  assert.equal(download.url, "/api/library/documents/generated-source-1/download?version=3");
  assert.equal(download.credentials, "same-origin");
  assert.ok(transport.requests.filter(request => request.method === "PUT").every(request => request.credentials === "include" && request.headers["X-XSRFToken"] === "generated-xsrf-cookie"));
}));
test("same-title cards choose the exact source and version", async () => mount(async ({ render, click, transport }) => {
  await render([source(), source("generated-source-2", 4)]); await click(4);
  assert.deepEqual(transport.requests.filter(request => request.url.includes("/download?")).map(request => request.url), ["/api/library/documents/generated-source-2/download?version=4"]);
}));
test("an unrelated message footer cannot display another message's personal import history", async () => mount(async ({ root, click, host }) => {
  await click(); await settle(() => host.container.textContent.includes("Imported to"));
  await React.act(async () => root.render(React.createElement(workspace.PersonalChatWorkspaceProvider, { configuration, ownerKey: "owner-1" }, React.createElement(ChatSourceChoices, { key: "unrelated-message", results: results([]) }))));
  assert.doesNotMatch(host.container.textContent, /Repeated title|original.txt|Imported to/);
}));
test("rapid and repeated cards cannot duplicate an import within the current owner scope", async () => {
  const waiting = deferred(); let upload;
  const transport = network(undefined, request => { if (request.method === "PUT" && request.url.endsWith("original.txt")) { upload = request; return waiting.promise; } });
  await mount(async ({ render, click, host }) => {
    await render([source()], "owner-1", 2);
    const buttons = host.elements("button").filter(value => value.textContent === "Import copy");
    await React.act(async () => { for (const button of [buttons[0], buttons[1], buttons[0]]) button.dispatchEvent({ type: "click", bubbles: true, button: 0 }); });
    await settle(() => !!upload);
    assert.equal(transport.requests.filter(request => request.url.includes("/download?")).length, 1);
    await React.act(async () => waiting.resolve(json({ type: "file", path: "original.txt" })));
    await settle(() => host.container.textContent.includes("Imported to"));
    assert.ok(host.elements("button").filter(value => value.textContent === "Copy imported").every(value => value.hasAttribute("disabled")));
  }, { transport });
});
test("partial provenance keeps the copied path and warning without retrying or claiming complete import", async () => {
  const transport = network(undefined, request => request.url.includes("source-metadata") ? new Response(null, { status: 500 }) : undefined);
  await mount(async ({ click, host, selection }) => {
    await click(); await settle(() => host.container.textContent.includes("Personal copy saved"));
    assert.equal(selection.path, null); assert.match(host.container.textContent, /original\.txt/); assert.match(host.container.textContent, /metadata/i);
    assert.ok(host.elements("button").some(value => value.textContent === "Open copy"));
    assert.equal(host.elements("button").some(value => value.textContent === "Retry Import"), false);
  }, { transport });
});
test("unknown save outcomes retain check links and cannot be blindly retried across message cards", async () => {
  const transport = network(undefined, request => { if (request.method === "PUT" && request.url.endsWith("original.txt")) throw new Error("RAW_FAILURE"); });
  await mount(async ({ click, host, selection, render }) => {
    await click(); await settle(() => host.container.textContent.includes("could not be confirmed"));
    assert.equal(selection.path, null); assert.match(host.container.textContent, /original\.txt/); assert.doesNotMatch(host.container.textContent, /RAW_FAILURE/);
    assert.ok(host.elements("a").some(value => value.getAttribute("href") === "http://127.0.0.1:58101/lab/tree/original.txt"));
    await render([source()], "owner-1", 2);
    assert.equal(host.elements("button").some(value => value.textContent === "Retry Import"), false);
    assert.equal(transport.requests.filter(request => request.url.includes("/download?")).length, 1);
  }, { transport });
});
test("owner change aborts delayed work and ignores late saved-path responses", async () => {
  const waiting = deferred(); let upload;
  const transport = network(undefined, request => { if (request.method === "PUT" && request.url.endsWith("original.txt")) { upload = request; return waiting.promise; } });
  await mount(async ({ click, render, host, selection }) => {
    await click(); await settle(() => !!upload); await render([], "owner-2"); assert.equal(upload.signal.aborted, true);
    await React.act(async () => waiting.resolve(json({ type: "file", path: "original.txt" })));
    assert.equal(selection.path, null); assert.doesNotMatch(host.container.textContent, /Imported to|Repeated title|original.txt/);
    assert.equal(transport.requests.some(request => request.url.includes("source-metadata")), false);
  }, { transport });
});
test("removing an importing card aborts it and preserves an uncertain warning for another owner-scoped card", async () => {
  const waiting = deferred(); let upload;
  const transport = network(undefined, request => { if (request.method === "PUT" && request.url.endsWith("original.txt")) { upload = request; return waiting.promise; } });
  await mount(async ({ render, click, host }) => {
    await render([source()], "owner-1", 2); await click(); await settle(() => !!upload);
    await render([source()], "owner-1", 0); assert.equal(upload.signal.aborted, true);
    await render(); assert.match(host.container.textContent, /Check.*JupyterLab/);
    await React.act(async () => waiting.resolve(json({ type: "file", path: "original.txt" })));
    assert.doesNotMatch(host.container.textContent, /Imported to/);
  }, { transport });
});
test("generated Office original reaches the existing inline renderer through exact saved bytes", async () => {
  const workbook = new ExcelJS.Workbook(); workbook.addWorksheet("Original sheet").addRows([["Input", "Value"], ["Generated original cell", 27]]);
  const bytes = new Uint8Array(await workbook.xlsx.writeBuffer()), transport = network(bytes, undefined, ".xlsx");
  await mount(async ({ click, host, selection }) => {
    await click(); await settle(() => host.container.textContent.includes("Generated original cell"));
    assert.equal(selection.path, "original.xlsx");
    assert.deepEqual(new Uint8Array(Buffer.from(transport.files.get("original.xlsx").content, "base64")), bytes);
    assert.match(host.container.textContent, /Original sheet/);
  }, { transport, preview: true });
});
test("large imported copies keep a Jupyter link when the existing inline preview limit rejects them", async () => {
  const bytes = new Uint8Array(10 * 1024 * 1024 + 1), transport = network(bytes);
  await mount(async ({ click, host, selection }) => {
    await click(); await settle(() => selection.path !== null && host.container.textContent.includes("This file exceeds"));
    assert.equal(selection.path, "original.txt");
    assert.ok(host.elements("a").some(value => value.textContent.includes("Open in JupyterLab") && value.getAttribute("href") === "http://127.0.0.1:58101/lab/tree/original.txt"));
    assert.ok(transport.files.has("original.txt"));
  }, { transport, preview: true });
});
test("reloaded assistant message footers show source choices outside collapsed tool groups", async () => {
  required(cards, "ChatSourceCards"); const host = mountedHost(), oldFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("source choices alone do not fetch"); };
  const messages = fromAgUiMessages([{ id: "assistant-1", role: "assistant", content: "Generated choices", toolCalls: [{ id: "call-1", type: "function", function: { name: "search", arguments: "{}" } }] }, { id: "tool-1", role: "tool", toolCallId: "call-1", content: JSON.stringify(envelope({ hits: [hit()], truncated: false })) }]);
  function Harness() {
    const runtime = useExternalStoreRuntime({ messages, convertMessage: message => message, isRunning: false, onNew: async () => {} });
    return React.createElement(AssistantRuntimeProvider, { runtime }, React.createElement(workspace.PersonalChatWorkspaceProvider, { ownerKey: "owner-1", configuration }, React.createElement(ThreadPrimitive.Messages, { components: { AssistantMessage: ChatSourceCards, UserMessage: () => null } })));
  }
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  try { await React.act(async () => root.render(React.createElement(Harness))); assert.match(host.container.textContent, /Repeated title/); assert.ok(host.elements("button").some(value => value.textContent === "Import copy")); }
  finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});
