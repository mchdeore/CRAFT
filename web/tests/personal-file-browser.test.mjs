import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import React from "react";
import ExcelJS from "exceljs";
import { renderToStaticMarkup } from "react-dom/server";
import "./typescript-loader.mjs";
import { mountedHost } from "./source-import-host.mjs";
const moduleURL = new URL("../src/components/personal-file-browser.tsx", import.meta.url);
const view = existsSync(moduleURL) ? await import(moduleURL) : {};
const previewHelpers = await import("../src/lib/personal-preview.ts");
const configured = { status: "configured", url: "http://127.0.0.1:58101/lab" };
const json = body => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
const entry = (path, type = "file") => ({ path, name: path.split("/").at(-1), type, writable: true, size: 100 });
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(predicate) { for (let count = 0; count < 80; count++) { await React.act(async () => { await tick(); }); if (predicate()) return; } assert.fail("Personal file view did not settle"); }

// The existing lightweight DOM handles React mounting; add focus APIs for keyboard behavior.
function interactiveHost() {
  const host = mountedHost(), prototype = Object.getPrototypeOf(host.container);
  host.container.ownerDocument.oninput = null;
  const matches = (element, selector) => {
    const match = selector.trim().match(/^([a-z]+)?(?:\[([a-z-]+)(?:="([^"]*)")?\])?$/i);
    return !!match && (!match[1] || element.tagName === match[1].toUpperCase()) && (!match[2] || element.hasAttribute(match[2]) && (match[3] === undefined || element.getAttribute(match[2]) === match[3]));
  };
  prototype.focus = function () { this.ownerDocument.activeElement = this; };
  prototype.closest = function (selector) { for (let row = this; row?.tagName; row = row.parentNode) if (selector.split(",").some(part => matches(row, part))) return row; return null; };
  prototype.querySelectorAll = function (selector) { const rows = []; const visit = row => { for (const child of row.childNodes) { if (child.tagName && selector.split(",").some(part => matches(child, part))) rows.push(child); visit(child); } }; visit(this); return rows; };
  prototype.querySelector = function (selector) { return this.querySelectorAll(selector)[0] ?? null; };
  Object.defineProperty(prototype, "isConnected", { get() { return this.ownerDocument.contains(this); } });
  return host;
}

const buttonAt = (host, label) => host.elements("button").find(row => row.getAttribute("aria-label") === label || row.textContent === label);
const folderAt = host => host.elements("p").find(row => row.getAttribute("aria-label") === "Current folder")?.textContent;
async function click(host, label) { const button = buttonAt(host, label); assert.ok(button, label); await React.act(async () => { button.dispatchEvent({ type: "click", bubbles: true, button: 0 }); await tick(); }); }
async function filter(host, value) { const input = host.elements("input").find(row => row.getAttribute("aria-label") === "Filter personal files"); assert.ok(input); await React.act(async () => { input.value = value; input.dispatchEvent({ type: "input", bubbles: true }); }); return input; }

test("folder navigation and filtering retain exact file selection and distinguish no matches", async () => {
  const host = interactiveHost(), oldFetch = globalThis.fetch, selected = [];
  const folders = { "": [entry("imports", "directory"), entry("readme.txt")], imports: [entry("imports/nested", "directory"), entry("imports/report.xlsx")], "imports/nested": [entry("imports/nested/analysis.py")] };
  globalThis.fetch = async url => { const path = decodeURIComponent(new URL(url).pathname.slice("/api/contents/".length)); assert.ok(Object.hasOwn(folders, path), path); return json({ type: "directory", path, content: folders[path] }); };
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  try {
    await React.act(async () => { root.render(React.createElement(view.PersonalFileBrowser, { configuration: configured, onOpenFile: path => selected.push(path) })); await tick(); });
    assert.equal(folderAt(host), "Workspace root");
    await click(host, "Open folder imports");
    assert.equal(folderAt(host), "imports");
    await filter(host, " REPORT ");
    assert.equal(buttonAt(host, "Open folder nested"), undefined);
    await click(host, "Preview report.xlsx"); assert.deepEqual(selected, ["imports/report.xlsx"]);
    assert.equal(buttonAt(host, "Preview report.xlsx").getAttribute("aria-current"), "true");
    await filter(host, "missing");
    assert.match(host.container.textContent, /No .*match/i); assert.doesNotMatch(host.container.textContent, /No files yet/);
    await click(host, "Clear filter");
    assert.equal(host.elements("input")[0].value, "");
    await click(host, "Open folder nested");
    assert.equal(folderAt(host), "imports/nested");
    await click(host, "Back to parent folder");
    assert.ok(buttonAt(host, "Preview report.xlsx"));
    await click(host, "Go to workspace root");
    assert.ok(buttonAt(host, "Open folder imports")); assert.equal(folderAt(host), "Workspace root");
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

test("empty folders stay distinct from a filter miss and refresh reveals newly saved files", async () => {
  const host = interactiveHost(), oldFetch = globalThis.fetch; let files = [];
  globalThis.fetch = async () => json({ type: "directory", path: "", content: files });
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  try {
    await React.act(async () => { root.render(React.createElement(view.PersonalFileBrowser, { configuration: configured, onOpenFile() {} })); await tick(); });
    await filter(host, "missing");
    assert.match(host.container.textContent, /No files yet/); assert.doesNotMatch(host.container.textContent, /No .*match/i);
    files = [entry("new.txt")]; await click(host, "Refresh");
    assert.doesNotMatch(host.container.textContent, /No files yet/); assert.match(host.container.textContent, /No .*match/i);
    await click(host, "Clear filter"); assert.ok(buttonAt(host, "Preview new.txt"));
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

test("an authentication failure offers the configured sign-in link and retry reloads the current folder", async () => {
  const host = interactiveHost(), oldFetch = globalThis.fetch; let attempts = 0;
  globalThis.fetch = async () => ++attempts === 1 ? new Response(null, { status: 403 }) : json({ type: "directory", path: "", content: [entry("restored.txt")] });
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  try {
    await React.act(async () => { root.render(React.createElement(view.PersonalFileBrowser, { configuration: configured, onOpenFile() {} })); await tick(); });
    assert.match(host.container.textContent, /sign in/i);
    const signIn = host.elements("a").find(row => row.textContent.includes("Sign in")); assert.ok(signIn); assert.equal(signIn.getAttribute("href"), configured.url);
    await click(host, "Retry"); assert.equal(attempts, 2); assert.ok(buttonAt(host, "Preview restored.txt"));
    assert.equal(host.elements("p").some(row => row.getAttribute("role") === "alert"), false);
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

test("an import reveal aborts stale navigation and opens the actual containing folder again for repeated paths", async () => {
  const host = interactiveHost(), oldFetch = globalThis.fetch, requests = []; let finishRoot;
  globalThis.fetch = (url, options) => {
    const path = new URL(url).pathname.slice("/api/contents/".length); requests.push({ path, signal: options.signal });
    if (requests.length === 1) return new Promise(resolve => { finishRoot = resolve; });
    const content = path === "imports/deep" ? [entry("imports/deep/copy.csv")] : path === "imports" ? [entry("imports/deep", "directory")] : [entry("root.txt")];
    return Promise.resolve(json({ type: "directory", path, content }));
  };
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  const render = props => React.act(async () => { root.render(React.createElement(view.PersonalFileBrowser, { configuration: configured, onOpenFile() {}, ...props })); await tick(); });
  try {
    await render({}); await render({ revealFile: "imports/deep/copy.csv", refreshToken: 1 });
    assert.equal(requests[0].signal.aborted, true);
    finishRoot(json({ type: "directory", path: "", content: [entry("stale.txt")] })); await React.act(async () => { await tick(); });
    assert.doesNotMatch(host.container.textContent, /stale.txt/); assert.equal(folderAt(host), "imports/deep");
    assert.equal(buttonAt(host, "Preview copy.csv").getAttribute("aria-current"), "true");
    await click(host, "Back to parent folder");
    await render({ revealFile: "imports/deep/copy.csv", refreshToken: 2 });
    assert.equal(requests.filter(row => row.path === "imports/deep").length, 2);
    assert.ok(buttonAt(host, "Preview copy.csv"));
    await render({ revealFile: "root.txt", refreshToken: 3 });
    assert.ok(buttonAt(host, "Preview root.txt")); assert.equal(requests.at(-1).path, "");
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

test("personal preview focuses Close, contains keyboard focus, and Escape restores the opener", async () => {
  const host = interactiveHost(), oldFetch = globalThis.fetch;
  globalThis.fetch = async () => json({ path: "report.txt", type: "file", format: "base64", content: Buffer.from("Personal report").toString("base64"), mimetype: "text/plain" });
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  function Harness() { const [selection, setSelection] = React.useState(null); return React.createElement(React.Fragment, null, React.createElement("button", { onClick: () => setSelection({ path: "report.txt" }) }, "Open preview"), selection && React.createElement(view.PersonalFilePreview, { configuration: configured, selection, onClose: () => setSelection(null) })); }
  try {
    await React.act(async () => root.render(React.createElement(Harness)));
    const opener = buttonAt(host, "Open preview"); opener.focus(); await click(host, "Open preview");
    const close = buttonAt(host, "Close personal preview"); assert.ok(host.container.ownerDocument.activeElement === close, "Preview must focus Close");
    await settle(() => host.container.textContent.includes("Personal report"));
    const download = host.elements("a").find(row => row.getAttribute("download") === "report.txt");
    download.focus(); const tab = { type: "keydown", bubbles: true, key: "Tab", shiftKey: true };
    await React.act(async () => download.dispatchEvent(tab)); assert.equal(tab.defaultPrevented, true); assert.ok(host.container.ownerDocument.activeElement === close, "Shift-Tab must wrap to Close");
    await React.act(async () => close.dispatchEvent({ type: "keydown", bubbles: true, key: "Escape" }));
    assert.equal(host.elements("div").some(row => row.getAttribute("role") === "dialog"), false); assert.ok(host.container.ownerDocument.activeElement === opener, "Escape must restore opener focus");
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

test("personal file preview selects existing Office renderers and rejects HTML/XML rendering", () => {
  assert.equal(typeof previewHelpers.personalFileFormat, "function");
  assert.equal(previewHelpers.personalFileFormat("report.docx"), "docx"); assert.equal(previewHelpers.personalFileFormat("report.xlsx"), "xlsx");
  assert.equal(previewHelpers.personalFileFormat("report.html"), undefined); assert.equal(previewHelpers.personalFileFormat("report.xml"), undefined);
});

test("authenticated personal XLSX bytes render the actual workbook and provide an exact-byte download", async () => {
  assert.equal(typeof view.PersonalFileBrowser, "function"); assert.equal(typeof view.PersonalFilePreview, "function");
  const workbook = new ExcelJS.Workbook(); const sheet = workbook.addWorksheet("Generated budget"); sheet.addRow(["Item", "Cost"]); sheet.addRow(["Generated sensor", 42]);
  const bytes = new Uint8Array(await workbook.xlsx.writeBuffer());
  const host = interactiveHost(); const oldFetch = globalThis.fetch, createURL = URL.createObjectURL, revokeURL = URL.revokeObjectURL;
  const requests = [], blobs = [], revoked = [];
  URL.createObjectURL = blob => { blobs.push(blob); return createURL(blob); }; URL.revokeObjectURL = url => { revoked.push(url); revokeURL(url); };
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    const parsed = new URL(url), path = parsed.pathname.slice("/api/contents/".length);
    if (path === "") return json({ type: "directory", path, content: [entry("imports", "directory"), entry("analysis.ipynb", "notebook")] });
    if (path === "imports") return json({ type: "directory", path, content: [entry("imports/report.xlsx")] });
    assert.equal(path, "imports/report.xlsx"); return json({ path, type: "file", format: "base64", content: Buffer.from(bytes).toString("base64"), size: bytes.length, mimetype: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  };
  const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container);
  function Harness() { const [selection, setSelection] = React.useState(null); return React.createElement(React.Fragment, null, React.createElement(view.PersonalFileBrowser, { configuration: configured, onOpenFile: path => setSelection({ path }) }), selection && React.createElement(view.PersonalFilePreview, { configuration: configured, selection, onClose: () => setSelection(null) })); }
  const click = async label => { const button = host.elements("button").find(row => row.getAttribute("aria-label") === label || row.textContent === label); assert.ok(button, label); await React.act(async () => { button.dispatchEvent({ type: "click", bubbles: true, button: 0 }); await tick(); }); };
  try {
    await React.act(async () => { root.render(React.createElement(Harness)); await tick(); });
    const notebook = host.elements("a").find(row => row.textContent.includes("analysis.ipynb")); assert.ok(notebook); assert.equal(notebook.getAttribute("href"), `${configured.url}/tree/analysis.ipynb`);
    await click("Open folder imports"); await click("Preview report.xlsx");
    await settle(() => host.container.textContent.includes("Generated sensor"));
    assert.match(host.container.textContent, /Generated budget/); assert.match(host.container.textContent, /42/);
    const download = host.elements("a").find(row => row.getAttribute("download") === "report.xlsx"); assert.ok(download); assert.match(download.getAttribute("href"), /^blob:/);
    assert.deepEqual(new Uint8Array(await blobs[0].arrayBuffer()), bytes, "Downloaded bytes must be the exact personal workbook");
    assert.ok(requests.every(row => row.options.credentials === "include" && row.options.redirect === "error"));
    assert.ok(requests.some(row => row.url.endsWith("imports/report.xlsx?content=1&format=base64")));
    await click("Close personal preview"); assert.equal(revoked.includes(download.getAttribute("href")), true);
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; URL.createObjectURL = createURL; URL.revokeObjectURL = revokeURL; host.restore(); } }
});

test("changing or closing a personal preview aborts stale owned byte responses", async () => {
  assert.equal(typeof view.PersonalFilePreview, "function");
  const host = interactiveHost(); const oldFetch = globalThis.fetch; const pending = [];
  globalThis.fetch = (url, options) => new Promise(resolve => pending.push({ url, options, resolve }));
  const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container);
  try {
    await React.act(async () => root.render(React.createElement(view.PersonalFilePreview, { configuration: configured, selection: { path: "imports/first.txt" }, onClose() {} })));
    await React.act(async () => root.render(React.createElement(view.PersonalFilePreview, { configuration: configured, selection: { path: "imports/second.txt" }, onClose() {} })));
    assert.equal(pending[0].options.signal.aborted, true); assert.equal(pending[1].options.signal.aborted, false);
    pending[0].resolve(json({ path: "imports/first.txt", type: "file", format: "base64", content: Buffer.from("Stale personal bytes").toString("base64") })); await React.act(async () => { await tick(); });
    assert.doesNotMatch(host.container.textContent, /Stale personal bytes|first.txt/);
    await React.act(async () => root.unmount()); assert.equal(pending[1].options.signal.aborted, true);
    pending[1].resolve(json({})); await tick();
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

test("a missing workspace offers no personal download URL or Office content", () => {
  assert.equal(typeof view.PersonalFileBrowser, "function");
  const html = renderToStaticMarkup(React.createElement(view.PersonalFileBrowser, { configuration: { status: "missing" }, onOpenFile() {} }));
  assert.doesNotMatch(html, /href=|<iframe|Download/);
});
