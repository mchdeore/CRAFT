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
async function settle(predicate) { for (let count = 0; count < 80; count++) { await React.act(async () => { await tick(); }); if (predicate()) return; } assert.fail("Personal file preview did not settle"); }

test("personal file preview selects existing Office renderers and rejects HTML/XML rendering", () => {
  assert.equal(typeof previewHelpers.personalFileFormat, "function");
  assert.equal(previewHelpers.personalFileFormat("report.docx"), "docx"); assert.equal(previewHelpers.personalFileFormat("report.xlsx"), "xlsx");
  assert.equal(previewHelpers.personalFileFormat("report.html"), undefined); assert.equal(previewHelpers.personalFileFormat("report.xml"), undefined);
});

test("authenticated personal XLSX bytes render the actual workbook and provide an exact-byte download", async () => {
  assert.equal(typeof view.PersonalFileBrowser, "function"); assert.equal(typeof view.PersonalFilePreview, "function");
  const workbook = new ExcelJS.Workbook(); const sheet = workbook.addWorksheet("Generated budget"); sheet.addRow(["Item", "Cost"]); sheet.addRow(["Generated sensor", 42]);
  const bytes = new Uint8Array(await workbook.xlsx.writeBuffer());
  const host = mountedHost(); const oldFetch = globalThis.fetch, createURL = URL.createObjectURL, revokeURL = URL.revokeObjectURL;
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
  const host = mountedHost(); const oldFetch = globalThis.fetch; const pending = [];
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
