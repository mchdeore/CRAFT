import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import ExcelJS from "exceljs";
import { strToU8, zipSync } from "fflate";
import "./typescript-loader.mjs";
import { mountedHost } from "./source-import-host.mjs";
const { JupyterWorkspace } = await import("../src/components/jupyter-workspace.tsx");

const configured = { status: "configured", url: "http://127.0.0.1:58101/lab" };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const entry = (path, type = "file") => ({ path, name: path.split("/").at(-1), type, writable: true, size: 100 });
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(predicate) { for (let count = 0; count < 80; count++) { await React.act(async () => { await tick(); }); if (predicate()) return; } assert.fail("Workspace behavior did not settle"); }
const visible = element => !!element && !element.closest("[hidden]");
const buttonAt = (host, label) => host.elements("button").find(row => row.getAttribute("aria-label") === label || row.textContent === label);
async function click(host, label) { const button = buttonAt(host, label); assert.ok(button, label); await React.act(async () => { button.dispatchEvent({ type: "click", bubbles: true, button: 0 }); await tick(); }); }

function interactiveHost() {
  const host = mountedHost(), prototype = Object.getPrototypeOf(host.container);
  host.container.ownerDocument.oninput = null;
  const matches = (element, selector) => {
    const match = selector.trim().match(/^([a-z]+)?(?:\[([a-z-]+)(?:="([^"]*)")?\])?$/i);
    return !!match && (!match[1] || element.tagName === match[1].toUpperCase()) && (!match[2] || element.hasAttribute(match[2]) && (match[3] === undefined || element.getAttribute(match[2]) === match[3]));
  };
  prototype.focus = function () { this.ownerDocument.activeElement = this; };
  prototype.replaceChildren = function (...nodes) { while (this.firstChild) this.removeChild(this.firstChild); for (const node of nodes) this.appendChild(node); };
  prototype.closest = function (selector) { for (let row = this; row?.tagName; row = row.parentNode) if (selector.split(",").some(part => matches(row, part))) return row; return null; };
  prototype.querySelectorAll = function (selector) { const rows = []; const visit = row => { for (const child of row.childNodes) { if (child.tagName && selector.split(",").some(part => matches(child, part))) rows.push(child); visit(child); } }; visit(this); return rows; };
  prototype.querySelector = function (selector) { return this.querySelectorAll(selector)[0] ?? null; };
  Object.defineProperty(prototype, "isConnected", { get() { return this.ownerDocument.contains(this); } });
  return host;
}

test("the configured workspace launches its exact Jupyter URL in a separate tab without an embed", async () => {
  const host = interactiveHost(), oldFetch = globalThis.fetch;
  globalThis.fetch = async url => { assert.equal(url, "http://127.0.0.1:58101/api/contents/?content=1"); return json({ type: "directory", path: "", content: [] }); };
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  try {
    await React.act(async () => { root.render(React.createElement(JupyterWorkspace, { configuration: configured })); await tick(); });
    assert.equal(host.elements("iframe").length, 0);
    const launch = host.elements("a").find(row => row.textContent.includes("Open JupyterLab")); assert.ok(launch);
    assert.equal(launch.getAttribute("href"), configured.url); assert.equal(launch.getAttribute("target"), "_blank"); assert.equal(launch.getAttribute("rel"), "noopener noreferrer");
    assert.match(host.container.textContent, /sign in/i); assert.match(host.container.textContent, /\/workspace/);
    assert.ok(buttonAt(host, "Search sources"));
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

test("source search Close and Escape preserve personal navigation and restore focus", async () => {
  const host = interactiveHost(), oldFetch = globalThis.fetch, requests = [];
  globalThis.fetch = async url => { requests.push(url); const path = new URL(url).pathname.slice("/api/contents/".length); return json({ type: "directory", path, content: path === "" ? [entry("imports", "directory")] : [entry("imports/report.txt")] }); };
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  try {
    await React.act(async () => { root.render(React.createElement(JupyterWorkspace, { configuration: configured })); await tick(); });
    await click(host, "Open folder imports");
    const browser = host.elements("aside")[0], filter = host.elements("input").find(row => row.getAttribute("aria-label") === "Filter personal files");
    await React.act(async () => { filter.value = "report"; filter.dispatchEvent({ type: "input", bubbles: true }); });
    const launch = host.elements("a").find(row => row.textContent.includes("Open JupyterLab")), searchInput = host.elements("input").find(row => row.getAttribute("name") === "source-query");
    await click(host, "Search sources");
    const panel = host.elements("div").find(row => row.getAttribute("role") === "dialog" && visible(row)); assert.ok(panel);
    assert.ok(host.container.ownerDocument.activeElement === searchInput, "Search must receive focus");
    assert.equal(buttonAt(host, "Search sources").getAttribute("aria-expanded"), "true");
    await click(host, "Close source search"); assert.equal(visible(panel), false);
    assert.ok(host.container.ownerDocument.activeElement === buttonAt(host, "Search sources"), "Close must return focus to the search action");
    await click(host, "Search sources");
    const escape = { type: "keydown", bubbles: true, key: "Escape" };
    await React.act(async () => searchInput.dispatchEvent(escape));
    assert.equal(escape.defaultPrevented, true); assert.equal(visible(panel), false);
    assert.ok(host.elements("aside")[0] === browser, "Search must retain the browser DOM"); assert.equal(filter.value, "report"); assert.ok(buttonAt(host, "Preview report.txt"));
    assert.ok(host.elements("a").find(row => row.textContent.includes("Open JupyterLab")) === launch, "Search must retain the launch panel"); assert.equal(requests.length, 2);
    await click(host, "Preview report.txt");
    const preview = host.elements("div").find(row => row.getAttribute("role") === "dialog" && visible(row)); assert.ok(preview);
    await click(host, "Close personal preview");
    assert.ok(host.elements("aside")[0] === browser, "Preview must retain personal navigation"); assert.equal(filter.value, "report"); assert.ok(buttonAt(host, "Preview report.txt"));
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

test("closing a source search keeps the pending request and completed results for reopening", async () => {
  const host = interactiveHost(), oldFetch = globalThis.fetch; let finish, searchSignal;
  const hit = { kind: "document", handle: { item: "generated-source", version: 1, block: 1, locator: "p1", quoteSha256: "0".repeat(64), simulated: true }, title: "Generated report", text: "Generated snippet", classification: "unclassified", headingPath: "", score: 0.9, tags: { mission: null, silo: null, stage: null }, before: null, after: null };
  globalThis.fetch = (url, options) => url === "/api/library/search" ? new Promise(resolve => { finish = resolve; searchSignal = options.signal; }) : Promise.resolve(json({ type: "directory", path: "", content: [] }));
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  try {
    await React.act(async () => { root.render(React.createElement(JupyterWorkspace, { configuration: configured })); await tick(); });
    await click(host, "Search sources");
    const input = host.elements("input").find(row => row.getAttribute("name") === "source-query"); input.value = "report";
    await React.act(async () => host.elements("form")[0].dispatchEvent({ type: "submit", bubbles: true }));
    await click(host, "Close source search"); assert.equal(searchSignal.aborted, false);
    finish(json({ hits: [hit], truncated: false })); await React.act(async () => { await tick(); });
    await click(host, "Search sources"); assert.match(host.container.textContent, /Generated snippet/); assert.equal(input.value, "report");
    const close = buttonAt(host, "Close source search"); close.focus();
    const tab = { type: "keydown", bubbles: true, key: "Tab", shiftKey: true };
    await React.act(async () => close.dispatchEvent(tab)); assert.equal(tab.defaultPrevented, true); assert.ok(host.container.ownerDocument.activeElement === buttonAt(host, "Import copy for Generated report, version 1"), "Shift-Tab wraps to the last search action");
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

test("a real Office import reveals the saved containing folder and automatically previews the saved bytes", async () => {
  const workbook = new ExcelJS.Workbook(); workbook.addWorksheet("Imported budget").addRows([["Item", "Cost"], ["Generated telescope", 27]]);
  const bytes = new Uint8Array(await workbook.xlsx.writeBuffer());
  const host = interactiveHost(), oldFetch = globalThis.fetch, files = new Map(), requests = [];
  const hit = { kind: "document", handle: { item: "generated-source", version: 1, block: 1, locator: "p1", quoteSha256: "0".repeat(64), simulated: true }, title: "Generated report", text: "Generated workbook", classification: "unclassified", headingPath: "", score: 0.9, tags: { mission: null, silo: null, stage: null }, before: null, after: null };
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    if (url === "/api/library/search") return json({ hits: [hit], truncated: false });
    if (url === "/api/library/documents/generated-source/download?version=1") return new Response(bytes, { headers: { "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "content-disposition": 'attachment; filename="report.xlsx"' } });
    const parsed = new URL(url); assert.equal(parsed.origin, "http://127.0.0.1:58101"); const path = decodeURIComponent(parsed.pathname.slice("/api/contents/".length));
    if (path === "" || path === "imports" || path === "imports/source-metadata") {
      if (options.method === "POST") { const { ext } = JSON.parse(options.body), fresh = `${path}/untitled${ext}`; files.set(fresh, { type: "file", format: "text", content: "" }); return json({ type: "file", path: fresh }, 201); }
      const content = path === "" ? [entry("imports", "directory")] : [...files.keys()].filter(file => file.split("/").slice(0, -1).join("/") === path).map(file => entry(file));
      return json({ type: "directory", path, content });
    }
    assert.ok(files.has(path), path);
    if (options.method === "PATCH") { const target = JSON.parse(options.body).path, file = files.get(path); files.delete(path); files.set(target, file); return json({ type: "file", path: target }); }
    if (options.method === "PUT") { files.set(path, JSON.parse(options.body)); return json({ type: "file", path }); }
    return json({ ...files.get(path), path, size: bytes.length, mimetype: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  };
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  try {
    await React.act(async () => { root.render(React.createElement(JupyterWorkspace, { configuration: configured })); await tick(); });
    await click(host, "Search sources"); host.elements("input").find(row => row.getAttribute("name") === "source-query").value = "budget";
    await React.act(async () => host.elements("form")[0].dispatchEvent({ type: "submit", bubbles: true }));
    await click(host, "Import copy for Generated report, version 1"); await settle(() => host.container.textContent.includes("Generated telescope"));
    assert.match(host.container.textContent, /Imported budget/);
    assert.ok(requests.some(row => row.url === "http://127.0.0.1:58101/api/contents/imports?content=1"));
    assert.equal(buttonAt(host, "Preview report.xlsx").getAttribute("aria-current"), "true");
    const download = host.elements("a").find(row => row.getAttribute("download") === "report.xlsx"); assert.ok(download); assert.match(download.getAttribute("href"), /^blob:/);
    assert.equal(host.elements("iframe").length, 0);
    await click(host, "Close personal preview"); assert.match(host.container.textContent, /\/workspace\/imports/); assert.ok(visible(buttonAt(host, "Preview report.xlsx")));
    assert.ok(host.container.ownerDocument.activeElement === buttonAt(host, "Search sources"), "Imported preview Close returns focus to the workspace action");
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});


for (const format of ["docx", "xlsx"]) test(`a partial Office ${format.toUpperCase()} import keeps its warning visible until manual preview`, async () => {
  const workbook = new ExcelJS.Workbook(); workbook.addWorksheet("Imported budget").addRows([["Item", "Cost"], ["Generated telescope", 27]]);
  const bytes = format === "xlsx" ? new Uint8Array(await workbook.xlsx.writeBuffer()) : zipSync({
    "[Content_Types].xml": strToU8('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'),
    "_rels/.rels": strToU8('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'),
    "word/document.xml": strToU8('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Generated Word report</w:t></w:r></w:p><w:sectPr/></w:body></w:document>'),
  });
  const originalBytes = Uint8Array.from(bytes), name = `report.${format}`, savedPath = `imports/${name}`;
  const mime = format === "docx" ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  const host = interactiveHost(), oldFetch = globalThis.fetch, createURL = URL.createObjectURL, blobs = [], files = new Map(), requests = [];
  URL.createObjectURL = blob => { blobs.push(blob); return createURL(blob); };
  const hit = { kind: "document", handle: { item: "generated-source", version: 1, block: 1, locator: "p1", quoteSha256: "0".repeat(64), simulated: true }, title: "Generated report", text: "Generated Office original", classification: "unclassified", headingPath: "", score: 0.9, tags: { mission: null, silo: null, stage: null }, before: null, after: null };
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    if (url === "/api/library/search") return json({ hits: [hit], truncated: false });
    if (url === "/api/library/documents/generated-source/download?version=1") {
      assert.equal(options.method, "GET", "The external original is read-only");
      return new Response(bytes, { headers: { "content-type": mime, "content-disposition": `attachment; filename="${name}"` } });
    }
    const parsed = new URL(url); assert.equal(parsed.origin, "http://127.0.0.1:58101");
    const path = decodeURIComponent(parsed.pathname.slice("/api/contents/".length));
    if (path.startsWith("imports/source-metadata")) return new Response("Generated private metadata failure", { status: 503 });
    if (path === "" || path === "imports") {
      if (options.method === "POST") {
        const { ext } = JSON.parse(options.body), fresh = `imports/untitled${ext}`;
        files.set(fresh, { type: "file", format: "text", content: "" }); return json({ type: "file", path: fresh }, 201);
      }
      const content = path === "" ? [entry("imports", "directory")] : [...files.keys()].map(file => entry(file));
      return json({ type: "directory", path, content });
    }
    assert.ok(files.has(path), path);
    if (options.method === "PATCH") { const target = JSON.parse(options.body).path, file = files.get(path); files.delete(path); files.set(target, file); return json({ type: "file", path: target }); }
    if (options.method === "PUT") { files.set(path, JSON.parse(options.body)); return json({ type: "file", path }); }
    assert.equal(options.method, "GET");
    return json({ ...files.get(path), path, size: bytes.length, mimetype: mime });
  };
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  try {
    await React.act(async () => { root.render(React.createElement(JupyterWorkspace, { configuration: configured })); await tick(); });
    await click(host, "Search sources"); host.elements("input").find(row => row.getAttribute("name") === "source-query").value = "report";
    await React.act(async () => host.elements("form")[0].dispatchEvent({ type: "submit", bubbles: true }));
    await click(host, "Import copy for Generated report, version 1");
    await settle(() => host.elements("p").some(row => row.getAttribute("role") === "alert" && row.textContent.includes("Source metadata could not be confirmed")) && buttonAt(host, `Preview ${name}`));
    const search = host.elements("div").find(row => row.getAttribute("aria-label") === "Search and import sources");
    const warning = host.elements("p").find(row => row.getAttribute("role") === "alert" && row.textContent.includes(savedPath));
    assert.ok(visible(search), "A partial Office import keeps source search open");
    assert.ok(visible(warning), "The saved-path and provenance warning must be visible, not text in a hidden dialog");
    assert.doesNotMatch(warning.textContent, /Imported to|Generated private metadata failure/);
    assert.equal(host.elements("div").some(row => row.getAttribute("aria-label") === `Personal preview ${name}`), false, "Partial success must not open a preview automatically");
    assert.equal(buttonAt(host, "Copy saved for Generated report, version 1").hasAttribute("disabled"), true);
    assert.deepEqual(new Uint8Array(Buffer.from(files.get(savedPath).content, "base64")), originalBytes, "The confirmed personal copy contains the exact original bytes");
    await click(host, "Close source search");
    assert.ok(visible(buttonAt(host, `Preview ${name}`)), "Closing search exposes the saved file in Personal files");
    assert.equal(buttonAt(host, `Preview ${name}`).getAttribute("aria-current"), "true");
    assert.equal(host.elements("p").find(row => row.getAttribute("aria-label") === "Current folder").textContent, "/workspace/imports");
    assert.ok(requests.some(row => row.url === "http://127.0.0.1:58101/api/contents/imports?content=1"));
    await click(host, "Search sources"); assert.ok(visible(warning));
    assert.ok(visible(buttonAt(host, "Open copy")), "The warned copy keeps its manual preview action");
    await click(host, "Open copy");
    await settle(() => host.elements("a").some(row => row.getAttribute("download") === name));
    const preview = host.elements("div").find(row => row.getAttribute("aria-label") === `Personal preview ${name}`);
    assert.ok(visible(preview)); assert.equal(visible(search), false);
    assert.deepEqual(new Uint8Array(await blobs[0].arrayBuffer()), originalBytes, "Manual preview reads the exact confirmed personal bytes");
    if (format === "xlsx") { await settle(() => preview.textContent.includes("Generated telescope")); assert.match(preview.textContent, /Imported budget/); }
    else assert.match(preview.textContent, /Word/);
    const reads = requests.filter(row => row.url === `http://127.0.0.1:58101/api/contents/${savedPath}?content=1&format=base64`);
    assert.equal(reads.length, 1); assert.equal(reads[0].options.credentials, "include"); assert.equal(reads[0].options.redirect, "error");
    assert.match(host.elements("a").find(row => row.getAttribute("download") === name).getAttribute("href"), /^blob:/);
    await click(host, "Close personal preview"); await click(host, "Search sources"); assert.ok(visible(warning));
    assert.equal(buttonAt(host, "Copy saved for Generated report, version 1").hasAttribute("disabled"), true);
    assert.equal(requests.filter(row => row.url.includes("/api/library/documents/")).length, 1, "Manual preview never downloads or writes the original again");
    assert.equal(requests.filter(row => row.options.method === "POST" && row.url === "http://127.0.0.1:58101/api/contents/imports").length, 1, "The partial result cannot create a duplicate import");
    assert.deepEqual(bytes, originalBytes, "The external original remains unchanged");
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; URL.createObjectURL = createURL; host.restore(); } }
});
