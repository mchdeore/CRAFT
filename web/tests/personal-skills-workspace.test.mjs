import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "./typescript-loader.mjs";
import { mountedHost } from "./source-import-host.mjs";
const moduleURL = new URL("../src/components/personal-skills-workspace.tsx", import.meta.url);
const view = existsSync(moduleURL) ? await import(moduleURL) : {};
const configured = { status: "configured", url: "http://127.0.0.1:58101/lab" };
const tool = { name: "search", operation: "generated.search", description: "Search engineering sources", effects: "NONE", needsApproval: false, route: "POST /api/library/search", inputSchema: { type: "object", properties: { query: { type: "string", description: "Words to find" } }, required: ["query"] }, outputSchema: {}, availableIn: ["chat", "mcp", "api"] };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const directory = (files = []) => ({ path: "skills", type: "directory", content: files.map(name => ({ name, path: `skills/${name}`, type: "file", writable: true, size: 12 })) });
const bytes = content => ({ path: "skills/check.md", type: "file", format: "base64", content: Buffer.from(content).toString("base64"), size: Buffer.byteLength(content), mimetype: "text/markdown" });
const tick = () => new Promise(resolve => setImmediate(resolve));
const button = (host, label) => { const found = host.elements("button").find(row => row.textContent === label || row.getAttribute("aria-label") === label); assert.ok(found, `Expected ${label}`); return found; };
const click = async (host, label) => React.act(async () => { button(host, label).dispatchEvent({ type: "click", bubbles: true, button: 0 }); await tick(); });

test("personal skill authoring is one roomy Markdown file with real tool metadata", () => {
  assert.equal(typeof view.PersonalSkillsEditor, "function");
  const html = renderToStaticMarkup(React.createElement(view.PersonalSkillsEditor, { document: { filename: "check.md", content: "# Goal\nCheck inputs." }, tools: [tool], toolStatus: "ready", busy: false, onChange() {}, onSave() {} }));
  for (const text of ["Skill filename", "Skill document", "Save to workspace", "Insert template", "Words to find", "query", "required"]) assert.ok(html.includes(text), text);
  assert.match(html, /min-h-\[24rem\]/); assert.match(html, /placeholder=/);
  assert.doesNotMatch(html, /Save skill|Revision|mutationId|Register skill|toolNames/);
});

test("personal Markdown tool insertion preserves the selection, surrounding edits and filename", async () => {
  assert.equal(typeof view.PersonalSkillsEditor, "function");
  const host = mountedHost(); const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container); let current;
  function Harness() { const [document, setDocument] = React.useState({ filename: "check.md", content: "Before selected after" }); current = document; return React.createElement(view.PersonalSkillsEditor, { document, tools: [tool], toolStatus: "ready", busy: false, onChange: patch => setDocument(old => ({ ...old, ...patch })), onSave() {} }); }
  try {
    await React.act(async () => root.render(React.createElement(Harness)));
    const input = host.elements("textarea")[0]; input.focus = () => { input.ownerDocument.activeElement = input; }; input.setSelectionRange = (start, end) => { input.selectionStart = start; input.selectionEnd = end; };
    input.setSelectionRange(7, 15); input.focus();
    await click(host, "Insert /search");
    assert.equal(input.value, "Before /search after"); assert.equal(input.selectionStart, 14); assert.equal(input.selectionEnd, 14);
    assert.equal(input.ownerDocument.activeElement, input); assert.equal(current.filename, "check.md"); assert.equal("toolNames" in current, false);
    input.setSelectionRange(0, input.value.length);
    await click(host, "Insert template"); assert.ok(input.value.includes("Before /search after"), "Template insertion must retain selected unsaved work");
  } finally { try { await React.act(async () => root.unmount()); } finally { host.restore(); } }
});

async function mountWorkspace(fetcher) {
  assert.equal(typeof view.PersonalSkillsWorkspace, "function");
  const host = mountedHost(); const oldFetch = globalThis.fetch; globalThis.fetch = fetcher;
  const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container); let invalidations = 0;
  await React.act(async () => { root.render(React.createElement(view.PersonalSkillsWorkspace, { configuration: configured, onAuthInvalid: () => { invalidations++; } })); await tick(); });
  return { host, root, get invalidations() { return invalidations; }, async close() { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } } };
}

test("Load/New require an explicit decision before replacing unsaved personal instructions", async () => {
  const requests = [];
  const mounted = await mountWorkspace(async (url, options) => { requests.push({ url, options }); if (url === "/api/tools") return json([tool]); if (url.endsWith("skills?content=1")) return json(directory(["check.md"])); if (url.includes("skills/check.md?")) return json(bytes("Saved current instructions")); assert.fail(url); });
  try {
    const input = mounted.host.elements("textarea")[0]; input.value = "My unsaved instructions";
    await React.act(async () => input.dispatchEvent({ type: "input", bubbles: true }));
    await click(mounted.host, "Load check.md");
    assert.equal(input.value, "My unsaved instructions"); assert.equal(requests.some(row => row.url.includes("skills/check.md?")), false);
    await click(mounted.host, "Keep editing"); assert.equal(input.value, "My unsaved instructions");
    await click(mounted.host, "Load check.md"); await click(mounted.host, "Discard edits and continue");
    assert.equal(input.value, "Saved current instructions");
    assert.match(mounted.host.container.textContent, /\/workspace\/skills\/check\.md/);
    const filename = mounted.host.elements("input").find(row => row.getAttribute("aria-label") === "Skill filename");
    assert.equal(filename.getAttribute("readonly") ?? filename.getAttribute("readOnly"), "");
    assert.equal(requests.some(row => row.url.startsWith("/api/skills")), false);
  } finally { await mounted.close(); }
});

test("unconfirmed save retains edits, blocks blind re-save and never registers a catalog skill", async () => {
  const requests = [];
  const mounted = await mountWorkspace(async (url, options) => { requests.push({ url, options }); if (url === "/api/tools") return json([tool]); if (options.method === "PUT") throw Error("private transport message"); if (url.endsWith("skills?content=1")) return json(directory(["check.md"])); if (url.includes("skills/check.md?")) return json(bytes("Saved current instructions")); assert.fail(url); });
  try {
    await click(mounted.host, "Load check.md");
    const input = mounted.host.elements("textarea")[0]; input.value = "Retain these edited instructions";
    await React.act(async () => input.dispatchEvent({ type: "input", bubbles: true }));
    await React.act(async () => { mounted.host.elements("form")[0].dispatchEvent({ type: "submit", bubbles: true }); await tick(); });
    assert.equal(input.value, "Retain these edited instructions"); assert.match(mounted.host.container.textContent, /Save could not be confirmed/);
    assert.doesNotMatch(mounted.host.container.textContent, /private transport|Saved \/workspace/);
    assert.equal(button(mounted.host, "Save to workspace").hasAttribute("disabled"), true);
    assert.equal(requests.filter(row => row.options.method === "PUT").length, 1); assert.equal(requests.some(row => row.url.startsWith("/api/skills")), false);
  } finally { await mounted.close(); }
});

test("late file loads are aborted at owned teardown and tool auth invalidation uses the existing sign-out callback", async () => {
  let finish, requestSignal;
  const pending = new Promise(resolve => { finish = resolve; });
  const mounted = await mountWorkspace(async (url, options) => { if (url === "/api/tools") return json([tool]); if (url.endsWith("skills?content=1")) return json(directory(["check.md"])); requestSignal = options.signal; return pending; });
  await click(mounted.host, "Load check.md"); assert.equal(requestSignal.aborted, false);
  await mounted.close(); assert.equal(requestSignal.aborted, true); finish(json(bytes("Late owner bytes"))); await tick();
  const unauthenticated = await mountWorkspace(async url => url === "/api/tools" ? json({}, 401) : json(directory()));
  try { assert.equal(unauthenticated.invalidations, 1); } finally { await unauthenticated.close(); }
});
