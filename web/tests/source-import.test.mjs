import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { createHash } from "node:crypto";
import React from "react";
import ExcelJS from "exceljs";
import { renderToStaticMarkup } from "react-dom/server";
import "./typescript-loader.mjs";
import { mountedHost } from "./source-import-host.mjs";

const clientURL = new URL("../src/lib/source-import.ts", import.meta.url);
const client = existsSync(clientURL) ? await import(clientURL) : {};
const workspace = await import("../src/components/jupyter-workspace.tsx");
const panel = await import("../src/components/source-import.tsx");
const requireFunction = name => { assert.equal(typeof client[name], "function", `Missing source import helper ${name}`); return client[name]; };
const configured = { status: "configured", url: "http://127.0.0.1:58101/lab" };
const source = { item: "generated-source-1", version: 3, title: "Generated Report", snippet: "<b>Generated source snippet</b>", classification: "unclassified" };
const hit = { kind: "document", handle: { item: source.item, version: 3, block: 1, locator: "p1", quoteSha256: "0".repeat(64), simulated: true }, title: source.title, text: source.snippet, classification: "unclassified", headingPath: "", score: 0.9, tags: { mission: null, silo: null, stage: null }, before: null, after: null };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const freshSignal = () => new AbortController().signal;
const sourceInput = host => host.elements("input").find(element => element.getAttribute("name") === "source-query");
const sourceForm = host => host.elements("form").find(element => element.getAttribute("aria-label") === "Search sources");
const jupyterLaunch = host => host.elements("a").find(element => element.getAttribute("href") === configured.url && element.getAttribute("target") === "_blank");
function focusCapableHost() {
  const host = mountedHost();
  Object.getPrototypeOf(host.container).focus = function () { this.ownerDocument.activeElement = this; };
  return host;
}
const openOriginal = (bytes = Uint8Array.of(120), options = {}) => {
  let cancellations = 0;
  const response = new Response(new ReadableStream({
    start(controller) { controller.enqueue(bytes); },
    cancel() { cancellations++; },
  }), options);
  return { response, get cancellations() { return cancellations; } };
};
const transport = override => {
  const requests = [], files = new Map();
  const fetcher = async (url, options = {}) => {
    const request = { url, ...options }; requests.push(request);
    const replacement = await override?.(request);
    if (replacement) return replacement;
    if (url === "/api/library/search") return json({ hits: [hit], truncated: false });
    if (url === "/api/library/documents/generated-source-1/download?version=3") return new Response("Generated original bytes", { headers: { "content-type": "text/plain", "content-disposition": 'attachment; filename="report.txt"' } });
    if (url.startsWith("http://127.0.0.1:58101/api/contents/")) {
      const path = new URL(url).pathname.slice("/api/contents/".length);
      const directory = path === "" || path === "imports" || path === "imports/source-metadata";
      if (directory && (!options.method || options.method === "GET")) return json({ type: "directory", path, content: [] });
      if (directory && options.method === "PUT") return json({ type: "directory", path }, 201);
      if (directory && options.method === "POST") {
        const { ext } = JSON.parse(options.body); let index = 0, fresh;
        do { fresh = `${path}/untitled${index || ""}${ext}`; index++; } while (files.has(fresh));
        files.set(fresh, { type: "file", format: "text", content: "" });
        return json({ type: "file", path: fresh }, 201);
      }
      if (files.has(path) && options.method === "PATCH") {
        const target = JSON.parse(options.body).path;
        if (target !== path && files.has(target)) return new Response(null, { status: 409 });
        const file = files.get(path); files.delete(path); files.set(target, file);
        return json({ type: "file", path: target });
      }
      if (files.has(path) && options.method === "PUT") {
        files.set(path, JSON.parse(options.body)); return json({ type: "file", path });
      }
    }
    assert.fail(`Unexpected generated request ${options.method ?? "GET"} ${url}`);
  };
  return { requests, fetcher, files };
};
async function environment(run, cookie = "_xsrf=generated-xsrf-cookie") {
  const oldFetch = globalThis.fetch;
  const old = new Map(["location", "document"].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperty(globalThis, "location", { value: { origin: "http://127.0.0.1:53520" }, configurable: true, writable: true });
  Object.defineProperty(globalThis, "document", { value: { cookie }, configurable: true, writable: true });
  try { return await run(); } finally { globalThis.fetch = oldFetch; for (const [name, descriptor] of old) { if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; } }
}
async function settleImport(host) {
  const deadline = Date.now() + 1000;
  while (host.container.textContent.includes("Importing...") && Date.now() < deadline) {
    await React.act(async () => { await new Promise(resolve => setImmediate(resolve)); });
  }
}
const deferred = () => {
  let resolve;
  const promise = new Promise(finish => { resolve = finish; });
  return { promise, resolve };
};
async function withSourcePanel(run, props = {}) {
  const host = mountedHost(), oldFetch = globalThis.fetch;
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  try {
    await React.act(async () => root.render(React.createElement(panel.SourceImport, { configuration: configured, ...props })));
    return await run(host, root);
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
}
async function submitPanel(host, value) {
  sourceInput(host).value = value;
  await React.act(async () => sourceForm(host).dispatchEvent({ type: "submit", bubbles: true }));
}
async function changePanelQuery(host, value) {
  const input = sourceInput(host); input.value = value;
  await React.act(async () => input.dispatchEvent({ type: "input", bubbles: true }));
}
const resultArticle = (host, title, version) => host.elements("article").find(article => article.textContent.includes(title) && article.textContent.includes(`Version ${version}`));
const importButton = (host, article) => host.elements("button").find(button => article?.contains(button) && /^(?:Import(?: copy)?|Retry Import)$/.test(button.textContent));
async function clickPanel(button) {
  assert.ok(button, "Result has an import action");
  await React.act(async () => button.dispatchEvent({ type: "click", bubbles: true, button: 0 }));
}
async function waitFor(condition, message) {
  const deadline = Date.now() + 1000;
  while (!condition() && Date.now() < deadline) await React.act(async () => { await new Promise(resolve => setImmediate(resolve)); });
  assert.ok(condition(), message);
}

test("source results identify the exact reference and offer an explicit personal copy action", async () => withSourcePanel(async host => {
  const mock = transport(); globalThis.fetch = mock.fetcher;
  await submitPanel(host, "radiator margin");
  const article = resultArticle(host, "Generated Report", 3);
  assert.ok(article);
  assert.match(article.textContent, /generated-source-1/);
  assert.match(article.textContent, /Generated source snippet/);
  assert.equal(importButton(host, article).textContent, "Import copy");
  assert.equal(host.elements("b").length, 0);
}));

test("typing and submitting another search does not cancel a personal save or hide its outcome", async () => {
  const imported = [];
  await withSourcePanel(async host => {
    const saved = deferred(); let upload;
    const newer = { ...hit, title: "Another source", handle: { ...hit.handle, item: "generated-other-2", version: 1 } };
    const mock = transport(request => {
      if (request.url === "/api/library/search" && JSON.parse(request.body).query === "other") return json({ hits: [newer], truncated: false });
      if (request.url.endsWith("/imports/report.txt") && request.method === "PUT") { upload = request; return saved.promise; }
    }); globalThis.fetch = mock.fetcher;
    await submitPanel(host, "margin");
    await clickPanel(importButton(host, resultArticle(host, "Generated Report", 3)));
    await waitFor(() => upload, "Personal save is in flight");
    await changePanelQuery(host, "other");
    await submitPanel(host, "other");
    assert.equal(upload.signal.aborted, false, "A new query only cancels its search");
    assert.match(host.container.textContent, /Another source/);
    await React.act(async () => saved.resolve(json({ type: "file", path: "imports/report.txt" })));
    await settleImport(host);
    assert.deepEqual(imported, ["imports/report.txt"]);
    assert.match(host.container.textContent, /Imported to imports\/report\.txt/);
    assert.equal(host.elements("a").find(link => link.textContent === "Open copy").getAttribute("href"), "http://127.0.0.1:58101/lab/tree/imports/report.txt");
    await submitPanel(host, "margin");
    const article = resultArticle(host, "Generated Report", 3);
    assert.match(article.textContent, /Imported to imports\/report\.txt/);
    const button = host.elements("button").find(row => article.contains(row));
    assert.equal(button.hasAttribute("disabled"), true, "The saved result does not offer a duplicate import");
  }, { onImported: path => imported.push(path) });
});

test("copy state belongs to an item and version and survives imports of other results", async () => withSourcePanel(async host => {
  const newer = { ...hit, title: "Generated newer report", handle: { ...hit.handle, version: 4 } };
  const mock = transport(request => {
    if (request.url === "/api/library/search") return json({ hits: [hit, newer], truncated: false });
    if (request.url.endsWith("/download?version=4")) return new Response("New version bytes", { headers: { "content-type": "text/plain", "content-disposition": 'attachment; filename="newer-report.txt"' } });
  }); globalThis.fetch = mock.fetcher;
  await submitPanel(host, "margin");
  await clickPanel(importButton(host, resultArticle(host, "Generated Report", 3)));
  await settleImport(host);
  const versionFour = resultArticle(host, "Generated newer report", 4);
  assert.doesNotMatch(versionFour.textContent, /Imported to/, "Another version has not been imported");
  assert.equal(importButton(host, versionFour).hasAttribute("disabled"), false);
  await clickPanel(importButton(host, versionFour));
  await settleImport(host);
  assert.match(resultArticle(host, "Generated Report", 3).textContent, /Imported to imports\/report\.txt/);
  assert.match(resultArticle(host, "Generated newer report", 4).textContent, /Imported to imports\/newer-report\.txt/);
  await changePanelQuery(host, "repeated search");
  await submitPanel(host, "repeated search");
  assert.match(resultArticle(host, "Generated Report", 3).textContent, /Imported to imports\/report\.txt/);
  assert.equal(mock.requests.filter(request => request.url.includes("/download?")).length, 2);
}));

test("a saved copy with incomplete metadata refreshes the tree and cannot be imported twice", async () => {
  const imported = [];
  await withSourcePanel(async host => {
    const mock = transport(request => request.url.includes("source-metadata") ? new Response("Generated private body", { status: 503 }) : undefined);
    globalThis.fetch = mock.fetcher;
    await submitPanel(host, "margin");
    await clickPanel(importButton(host, resultArticle(host, "Generated Report", 3)));
    await settleImport(host);
    assert.deepEqual(imported, ["imports/report.txt"], "Confirmed personal bytes refresh the tree even without confirmed metadata");
    assert.match(host.container.textContent, /copy saved at imports\/report\.txt/i);
    assert.doesNotMatch(host.container.textContent, /Imported to|Generated private body/);
    const article = resultArticle(host, "Generated Report", 3);
    const button = host.elements("button").find(row => article.contains(row));
    assert.equal(button.hasAttribute("disabled"), true);
    await clickPanel(button);
    await changePanelQuery(host, "margin again");
    await submitPanel(host, "margin again");
    assert.match(resultArticle(host, "Generated Report", 3).textContent, /copy saved at imports\/report\.txt/i);
    assert.equal(mock.requests.filter(request => request.url.includes("/download?")).length, 1);
  }, { onImported: path => imported.push(path) });
});

test("late search responses cannot replace the newest submitted selection", async () => withSourcePanel(async host => {
  const old = deferred(); let oldSignal;
  const oldHit = { ...hit, title: "Older search result" };
  const newHit = { ...hit, title: "Newer search result", handle: { ...hit.handle, version: 4 } };
  const mock = transport(request => {
    if (request.url === "/api/library/search") {
      if (JSON.parse(request.body).query === "older") { oldSignal = request.signal; return old.promise; }
      return json({ hits: [newHit], truncated: false });
    }
  }); globalThis.fetch = mock.fetcher;
  await submitPanel(host, "older");
  await changePanelQuery(host, "newer");
  await submitPanel(host, "newer");
  assert.equal(oldSignal.aborted, true);
  await React.act(async () => old.resolve(json({ hits: [oldHit], truncated: false })));
  assert.match(host.container.textContent, /Newer search result/);
  assert.doesNotMatch(host.container.textContent, /Older search result/);
}));

test("changing the SourceImport owner cancels a delayed save and clears previous source state", async () => {
  const imported = [];
  await withSourcePanel(async (host, root) => {
    const saved = deferred(); let upload;
    const mock = transport(request => {
      if (request.url.endsWith("/imports/report.txt") && request.method === "PUT") { upload = request; return saved.promise; }
    }); globalThis.fetch = mock.fetcher;
    await submitPanel(host, "margin");
    await clickPanel(importButton(host, resultArticle(host, "Generated Report", 3)));
    await waitFor(() => upload, "Personal save is in flight");
    await React.act(async () => root.render(React.createElement(panel.SourceImport, {
      configuration: { status: "configured", url: "http://127.0.0.1:58102/lab" }, onImported: path => imported.push(path),
    })));
    assert.equal(upload.signal.aborted, true);
    await React.act(async () => saved.resolve(json({ type: "file", path: "imports/report.txt" })));
    assert.deepEqual(imported, []);
    assert.doesNotMatch(host.container.textContent, /Generated Report|Generated source snippet|Imported to/);
  }, { onImported: path => imported.push(path) });
});

test("lost personal mutation responses are uncertain outcomes rather than retryable imports", async t => {
  const cases = [
    { name: "create response lost", method: "POST", endpoint: "/imports", possiblePath: undefined },
    { name: "rename response lost", method: "PATCH", endpoint: "/imports/untitled.txt", possiblePath: "imports/report.txt" },
    { name: "save response lost", method: "PUT", endpoint: "/imports/report.txt", possiblePath: "imports/report.txt" },
  ];
  for (const fixture of cases) await t.test(fixture.name, async () => environment(async () => {
    let mock;
    mock = transport(request => {
      if (request.method !== fixture.method || !request.url.endsWith(fixture.endpoint)) return;
      if (fixture.method === "POST") mock.files.set("imports/untitled.txt", { type: "file", format: "text", content: "" });
      if (fixture.method === "PATCH") { mock.files.set("imports/report.txt", mock.files.get("imports/untitled.txt")); mock.files.delete("imports/untitled.txt"); }
      if (fixture.method === "PUT") mock.files.set("imports/report.txt", JSON.parse(request.body));
      throw new Error("Generated private transport failure");
    }); globalThis.fetch = mock.fetcher;
    await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => {
      assert.equal(error.code, "outcome_unknown");
      assert.equal(error.copiedPath, undefined, "No unconfirmed success claim");
      assert.equal(error.possiblePath, fixture.possiblePath);
      assert.doesNotMatch(error.message, /retry|private/i);
      return true;
    });
    assert.equal(mock.requests.filter(request => request.method === fixture.method && request.url.endsWith(fixture.endpoint)).length, 1);
    assert.equal(mock.requests.some(request => request.url.includes("source-metadata")), false);
    if (fixture.method === "PUT") assert.equal(Buffer.from(mock.files.get("imports/report.txt").content, "base64").toString(), "Generated original bytes");
  }));
});

test("an untrusted personal save model never confirms a copy or exposes an unsafe path", async () => environment(async () => {
  const mock = transport(request => request.method === "PUT" && request.url.endsWith("/imports/report.txt")
    ? json({ type: "file", path: "imports/../../another-owner.txt" }) : undefined);
  globalThis.fetch = mock.fetcher;
  await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => {
    assert.equal(error.code, "outcome_unknown");
    assert.equal(error.copiedPath, undefined);
    assert.equal(error.possiblePath, "imports/report.txt");
    assert.doesNotMatch(error.message, /another-owner/);
    return true;
  });
  assert.equal(mock.requests.some(request => request.url.includes("source-metadata")), false);
}));

test("a mismatched imports directory creation is rejected before any personal file mutation", async () => environment(async () => {
  const mock = transport(request => {
    if (request.url.endsWith("/imports?content=0")) return new Response(null, { status: 404 });
    if (request.url.endsWith("/imports") && request.method === "PUT") return json({ type: "directory", path: "other-owner" }, 201);
  }); globalThis.fetch = mock.fetcher;
  await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "invalid_response");
  assert.equal(mock.requests.some(request => request.method === "POST" || request.method === "PATCH"), false);
}));

test("an uncertain save offers a workspace check and preserves duplicate protection across searches", async () => {
  const imported = [];
  await withSourcePanel(async host => {
    const mock = transport(request => {
      if (request.method === "PUT" && request.url.endsWith("/imports/report.txt")) throw new Error("Generated private transport failure");
    }); globalThis.fetch = mock.fetcher;
    await submitPanel(host, "margin");
    await clickPanel(importButton(host, resultArticle(host, "Generated Report", 3)));
    await settleImport(host);
    assert.match(host.container.textContent, /not.*confirmed|may.*saved/i);
    assert.match(host.container.textContent, /imports\/report\.txt/);
    assert.doesNotMatch(host.container.textContent, /Imported to|Personal copy saved|Generated private/);
    assert.deepEqual(imported, [], "Unknown bytes are not reported as saved");
    const check = host.elements("a").find(link => link.getAttribute("href") === "http://127.0.0.1:58101/lab/tree/imports/report.txt");
    assert.ok(check, "The safe candidate can be checked in JupyterLab");
    await submitPanel(host, "margin again");
    const article = resultArticle(host, "Generated Report", 3);
    assert.equal(host.elements("button").find(row => article.contains(row)).hasAttribute("disabled"), true);
    assert.equal(mock.requests.filter(request => request.url.includes("/download?")).length, 1);
  }, { onImported: path => imported.push(path) });
});

test("source search sends the existing typed document-only query and returns bounded snippets", async () => environment(async () => {
  const mock = transport(); globalThis.fetch = mock.fetcher;
  const result = await requireFunction("searchSources")("  radiator margin  ", freshSignal());
  assert.deepEqual(result, { results: [source], truncated: false });
  const [request] = mock.requests;
  assert.equal(request.url, "/api/library/search"); assert.equal(request.method, "POST");
  assert.equal(request.credentials, "same-origin"); assert.equal(request.redirect, "error");
  assert.deepEqual(JSON.parse(request.body), { query: "radiator margin", kinds: ["document"], limit: 6 });
  assert.equal(request.headers["X-XSRFToken"], undefined);
  globalThis.fetch = async () => json({ hits: [{ ...hit, text: "x".repeat(2000) }], truncated: true });
  assert.equal((await requireFunction("searchSources")("margin", freshSignal())).results[0].snippet.length, 640);
}));

test("source search rejects invalid queries and unsafe references before download", async () => environment(async () => {
  let calls = 0; globalThis.fetch = async () => { calls++; return json({ hits: [], truncated: false }); };
  for (const query of ["", " ", "x".repeat(501), "report\nX-header: value"]) await assert.rejects(() => requireFunction("searchSources")(query, freshSignal()), error => error.code === "invalid_query");
  assert.equal(calls, 0);
  globalThis.fetch = async () => json({ hits: [{ ...hit, handle: { ...hit.handle, item: "../private" } }], truncated: false });
  await assert.rejects(() => requireFunction("searchSources")("margin", freshSignal()), error => error.code === "invalid_response");
}));

test("Import downloads the selected version and uploads only a newly created personal file", async () => environment(async () => {
  const mock = transport(); globalThis.fetch = mock.fetcher;
  const path = await requireFunction("importSource")(source, configured, freshSignal());
  assert.equal(path, "imports/report.txt");
  const writes = mock.requests.filter(request => ["POST", "PATCH", "PUT"].includes(request.method));
  assert.deepEqual(writes.slice(0, 3).map(request => request.method), ["POST", "PATCH", "PUT"]);
  assert.deepEqual(JSON.parse(writes[0].body), { type: "file", ext: ".txt" });
  assert.deepEqual(JSON.parse(writes[1].body), { path });
  assert.deepEqual(JSON.parse(writes[2].body), { type: "file", format: "base64", content: Buffer.from("Generated original bytes").toString("base64") });
  for (const request of mock.requests.filter(request => request.url.startsWith("http:"))) {
    assert.equal(new URL(request.url).origin, "http://127.0.0.1:58101"); assert.equal(request.credentials, "include"); assert.equal(request.redirect, "error");
    assert.doesNotMatch(request.url, /token|password|xsrf|58102/);
    if (request.method !== "GET") assert.equal(request.headers["X-XSRFToken"], "generated-xsrf-cookie");
    assert.equal(request.headers?.Authorization, undefined);
  }
  const download = mock.requests.find(request => request.url.startsWith("/api/"));
  assert.equal(download.url, "/api/library/documents/generated-source-1/download?version=3"); assert.equal(download.method, "GET");
  assert.equal(download.credentials, "same-origin"); assert.equal(download.headers?.["X-XSRFToken"], undefined);
  assert.doesNotMatch(writes.map(request => request.body).join(""), /generated-xsrf-cookie|password|token/);
  const bytes = Uint8Array.from({ length: 30011 }, (_, index) => index % 256);
  const binary = transport(request => request.url.includes("/download?") ? new Response(bytes, { headers: { "content-type": "application/octet-stream", "content-disposition": 'attachment; filename="report.txt"' } }) : undefined);
  globalThis.fetch = binary.fetcher;
  await requireFunction("importSource")(source, configured, freshSignal());
  const upload = binary.requests.find(request => request.method === "PUT");
  assert.deepEqual(Buffer.from(JSON.parse(upload.body).content, "base64"), Buffer.from(bytes), "Base64 preserves binary bytes across bounded conversion chunks");
}));

test("Import creates the imports directory only when missing and after bounded download", async () => environment(async () => {
  const mock = transport(request => request.url.endsWith("imports?content=0") ? new Response(null, { status: 404 }) : undefined); globalThis.fetch = mock.fetcher;
  await requireFunction("importSource")(source, configured, freshSignal());
  const directory = mock.requests.find(request => request.url.endsWith("/imports") && request.method === "PUT");
  assert.deepEqual(JSON.parse(directory.body), { type: "directory" });
  assert.ok(mock.requests.indexOf(directory) > mock.requests.findIndex(request => request.url.includes("/download?")));
}));

test("Import refuses a rename conflict without overwriting an existing personal file", async () => environment(async () => {
  const mock = transport(request => request.method === "PATCH" ? new Response("Generated private failure", { status: 409 }) : undefined); globalThis.fetch = mock.fetcher;
  await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "conflict" && !error.message.includes("private"));
  assert.equal(mock.requests.some(request => request.method === "PUT"), false);
  const renames = mock.requests.filter(request => request.method === "PATCH");
  assert.equal(renames.length, 20, "Name conflicts have a finite limit");
  assert.equal(JSON.parse(renames[0].body).path, "imports/report.txt");
  assert.equal(JSON.parse(renames[19].body).path, "imports/report-20.txt");
  assert.equal(mock.requests.filter(request => request.method === "POST").length, 1);
}));

test("Import selects a readable available name without replacing previous copies", async () => environment(async () => {
  const mock = transport(); globalThis.fetch = mock.fetcher;
  const first = { type: "file", format: "text", content: "Keep first personal edit" };
  const second = { type: "file", format: "text", content: "Keep second personal edit" };
  mock.files.set("imports/report.txt", first); mock.files.set("imports/report-2.txt", second);
  const path = await requireFunction("importSource")(source, configured, freshSignal());
  assert.equal(path, "imports/report-3.txt");
  assert.deepEqual(mock.files.get("imports/report.txt"), first);
  assert.deepEqual(mock.files.get("imports/report-2.txt"), second);
  assert.equal(Buffer.from(mock.files.get(path).content, "base64").toString(), "Generated original bytes");
  assert.deepEqual(mock.requests.filter(request => request.method === "PATCH" && request.url.endsWith("/imports/untitled.txt")).map(request => JSON.parse(request.body).path), ["imports/report.txt", "imports/report-2.txt", "imports/report-3.txt"]);
  assert.equal(mock.requests.filter(request => request.url.includes("/download?")).length, 1);
  assert.equal(mock.requests.some(request => request.url.includes("/contents/imports/report") && request.method === "GET"), false);
}));

test("Import does not retry a failed rename that is not a name conflict", async () => environment(async () => {
  const mock = transport(request => request.method === "PATCH" ? new Response("Generated private failure", { status: 500 }) : undefined); globalThis.fetch = mock.fetcher;
  await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "outcome_unknown" && error.copiedPath === undefined && error.possiblePath === "imports/report.txt");
  assert.equal(mock.requests.filter(request => request.method === "PATCH").length, 1);
  assert.equal(mock.requests.some(request => request.method === "PUT"), false);
}));

test("Import saves separate bounded source provenance for each readable personal copy", async () => environment(async () => {
  const mock = transport(); globalThis.fetch = mock.fetcher;
  const paths = [await requireFunction("importSource")(source, configured, freshSignal()), await requireFunction("importSource")(source, configured, freshSignal())];
  assert.deepEqual(paths, ["imports/report.txt", "imports/report-2.txt"]);
  const entries = [...mock.files].filter(([path]) => path.startsWith("imports/source-metadata/"));
  assert.equal(entries.length, 2);
  const records = entries.map(([path, file]) => {
    assert.equal(file.format, "text"); assert.ok(Buffer.byteLength(file.content) <= 4096);
    const value = JSON.parse(file.content);
    assert.match(path, /^imports\/source-metadata\/[0-9a-f-]{36}\.json$/);
    assert.equal(path, `imports/source-metadata/${value.importId}.json`);
    assert.equal(value.format, "craft-source-import/1");
    assert.deepEqual(value.source, { item: "generated-source-1", version: 3, title: "Generated Report", classification: "unclassified" });
    assert.equal(value.copy.sha256, createHash("sha256").update("Generated original bytes").digest("hex"));
    assert.equal(value.copy.sizeBytes, Buffer.byteLength("Generated original bytes"));
    assert.match(value.importedAt, /^\d{4}-\d{2}-\d{2}T/);
    assert.doesNotMatch(file.content, /xsrf|password|token|Generated source snippet/);
    return value;
  });
  assert.deepEqual(records.map(record => record.copy.path).sort(), [...paths].sort());
  assert.notEqual(records[0].importId, records[1].importId);
  assert.equal(mock.requests.some(request => /\.json$/.test(request.url) && request.method === "GET"), false);
}));

test("Import creates the missing metadata directory after the personal bytes are saved", async () => environment(async () => {
  const mock = transport(request => request.url.endsWith("/source-metadata?content=0") ? new Response(null, { status: 404 }) : undefined); globalThis.fetch = mock.fetcher;
  await requireFunction("importSource")(source, configured, freshSignal());
  const directory = mock.requests.find(request => request.url.endsWith("/source-metadata") && request.method === "PUT");
  assert.ok(directory, "A first import can create its nonhidden provenance directory");
  assert.deepEqual(JSON.parse(directory.body), { type: "directory" });
  const sourceSave = mock.requests.find(request => request.method === "PUT" && !request.url.includes("source-metadata"));
  assert.ok(mock.requests.indexOf(directory) > mock.requests.indexOf(sourceSave));
}));

test("Import retains and identifies a saved copy when source metadata is not confirmed", async t => {
  for (const [name, override] of [
    ["metadata sign-in expired", request => request.url.includes("source-metadata") ? new Response("Generated private body", { status: 403 }) : undefined],
    ["unsafe metadata file response", request => request.url.endsWith("/source-metadata") && request.method === "POST" ? json({ type: "file", path: "other-owner/metadata.json" }, 201) : undefined],
    ["metadata destination already exists", request => request.url.includes("source-metadata") && request.method === "PATCH" ? new Response(null, { status: 409 }) : undefined],
    ["lost metadata save response", request => request.url.includes("source-metadata") && request.method === "PUT" ? Promise.reject(new Error("Generated private failure")) : undefined],
  ]) await t.test(name, async () => environment(async () => {
    const mock = transport(override); globalThis.fetch = mock.fetcher;
    await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "metadata_unavailable" && error.copiedPath === "imports/report.txt" && !error.message.includes("private"));
    assert.equal(Buffer.from(mock.files.get("imports/report.txt").content, "base64").toString(), "Generated original bytes");
    assert.equal(mock.requests.filter(request => request.method === "POST" && request.url.endsWith("/imports")).length, 1);
    assert.equal(mock.requests.filter(request => request.method === "PUT" && request.url.endsWith("/imports/report.txt")).length, 1);
    assert.equal(mock.requests.some(request => request.method === "DELETE"), false);
  }));
});

test("Import does not continue provenance writes after cancellation of a confirmed copy", async () => environment(async () => {
  const controller = new AbortController();
  const mock = transport(request => { if (request.url.includes("source-metadata")) { controller.abort(); return json({ type: "directory", path: "imports/source-metadata" }); } }); globalThis.fetch = mock.fetcher;
  await assert.rejects(() => requireFunction("importSource")(source, configured, controller.signal), error => error.code === "metadata_unavailable" && error.copiedPath === "imports/report.txt");
  assert.equal(controller.signal.aborted, true);
  assert.equal(mock.requests.some(request => request.url.includes("source-metadata") && request.method !== "GET"), false);
  assert.equal(Buffer.from(mock.files.get("imports/report.txt").content, "base64").toString(), "Generated original bytes");
}));

test("Import validates the exact loopback target and source reference before any request", async () => environment(async () => {
  let calls = 0; globalThis.fetch = async () => { calls++; assert.fail("No request for an unsafe target"); };
  for (const url of ["http://example.test/lab", "http://user:secret@localhost:58101/lab", "http://localhost:58101/lab?token=secret", "http://localhost:58101/lab#secret", "http://localhost:58101/lab/tree/private", "http://localhost:58101/../lab", "http://127.0.0.1:53520/lab", "https://localhost:58101/lab", "/lab"]) await assert.rejects(() => requireFunction("importSource")(source, { status: "configured", url }, freshSignal()), error => error.code === "invalid_workspace" && !error.message.includes("secret"));
  for (const changes of [{ item: "../private" }, { item: "report%2Fprivate" }, { version: 0 }, { version: 1.5 }]) await assert.rejects(() => requireFunction("importSource")({ ...source, ...changes }, configured, freshSignal()), error => error.code === "invalid_source");
  assert.equal(calls, 0);
}));

test("Import rejects unreadable or unsafe XSRF cookies and 403 with a sign-in state", async () => {
  for (const cookie of ["", "other=cookie", "_xsrf=%0D%0AX-header", "_xsrf=first; _xsrf=second", `_xsrf=${"x".repeat(513)}`]) await environment(async () => {
    let calls = 0; globalThis.fetch = async () => { calls++; assert.fail("Missing XSRF must not send requests"); };
    await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "workspace_auth"); assert.equal(calls, 0);
  }, cookie);
  await environment(async () => {
    let bodyReads = 0; globalThis.fetch = async () => ({ status: 403, ok: false, json() { bodyReads++; throw Error("Generated secret server body"); } });
    await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "workspace_auth" && !error.message.includes("secret")); assert.equal(bodyReads, 0);
  });
});

test("Import cancels the original stream on early response rejection without personal writes", async t => environment(async () => {
  const cases = [
    { name: "declared oversize", options: { headers: { "content-length": String(16 * 1024 * 1024 + 1) } }, code: "too_large" },
    { name: "malformed length", options: { headers: { "content-length": "invalid" } }, code: "invalid_response" },
    { name: "unsafe filename", options: { headers: { "content-disposition": 'attachment; filename="../report.txt"' } }, code: "invalid_name" },
    { name: "expired CRAFT sign-in", options: { status: 401 }, code: "craft_auth" },
  ];
  for (const { name, options, code } of cases) await t.test(name, async () => {
    const original = openOriginal(undefined, options); const signal = freshSignal();
    const mock = transport(request => request.url.includes("/download?") ? original.response : undefined); globalThis.fetch = mock.fetcher;
    try {
      await assert.rejects(() => requireFunction("importSource")(source, configured, signal), error => error.code === code);
      assert.equal(original.cancellations, 1, "Rejected original transfer must be cancelled");
      assert.equal(signal.aborted, false, "Response cleanup preserves the caller signal");
      assert.equal(mock.requests.some(request => request.method !== "GET"), false);
      const reader = original.response.body.getReader();
      try { assert.deepEqual(await reader.read(), { value: undefined, done: true }); } finally { reader.releaseLock(); }
    } finally { await original.response.body.cancel(); }
  });
}));

test("Import cancels streamed overflow before creating personal files", async () => environment(async () => {
  const original = openOriginal(new Uint8Array(16 * 1024 * 1024 + 1));
  const mock = transport(request => request.url.includes("/download?") ? original.response : undefined); globalThis.fetch = mock.fetcher;
  try {
    await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "too_large");
    assert.equal(original.cancellations, 1, "Streamed overflow stops the original transfer");
    assert.equal(mock.requests.some(request => request.method !== "GET"), false);
  } finally { await original.response.body.cancel(); }
}));

test("Import rejects filename and returned path hazards and preserves safe provider extensions", async () => environment(async () => {
  for (const name of ["../report.pdf", "folder/report.pdf", "folder\\report.pdf", "report:stream.pdf", "..", ".hidden.pdf"]) {
    const mock = transport(request => request.url.includes("/download?") ? new Response("x", { headers: { "content-type": "application/pdf", "content-disposition": `attachment; filename="${name}"` } }) : undefined); globalThis.fetch = mock.fetcher;
    await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "invalid_name");
    assert.equal(mock.requests.some(request => request.method === "POST"), false);
  }
  const headers = new Headers({ "content-disposition": "attachment; filename*=UTF-8''A%20report.PDF", "content-type": "application/octet-stream" });
  const name = requireFunction("personalFilename")(headers, "Generated fallback"); assert.equal(name, "A-report.pdf"); assert.ok(name.length <= 120);
  const fallback = requireFunction("personalFilename")(new Headers({ "content-type": "application/pdf" }), "Generated / Report"); assert.equal(fallback, "Generated-Report.pdf");
  assert.equal(requireFunction("personalFilename")(new Headers({ "content-disposition": 'attachment; filename="report.XLSX"' }), "Generated fallback"), "report.xlsx");
  const mock = transport(request => request.method === "POST" ? json({ type: "file", path: "imports/../../private.txt" }, 201) : undefined); globalThis.fetch = mock.fetcher;
  await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "outcome_unknown" && error.possiblePath === undefined); assert.equal(mock.requests.some(request => request.method === "PUT"), false);
}));

test("aborted downloads and delayed search JSON cannot publish or upload stale results", async () => environment(async () => {
  const controller = new AbortController(); let finish;
  const waiting = new Promise(resolve => { finish = resolve; });
  const mock = transport(request => request.url.includes("/download?") ? waiting : undefined); globalThis.fetch = mock.fetcher;
  const pending = requireFunction("importSource")(source, configured, controller.signal);
  const original = openOriginal();
  try {
    await new Promise(resolve => setImmediate(resolve)); controller.abort(); finish(original.response);
    await assert.rejects(pending, error => error.name === "AbortError"); assert.equal(mock.requests.some(request => request.method !== "GET"), false);
    assert.equal(original.cancellations, 1, "A response arriving after owner cancellation is closed");
  } finally { await original.response.body.cancel(); }
  const searchController = new AbortController(); globalThis.fetch = async () => ({ ok: true, json: async () => { searchController.abort(); return { hits: [hit], truncated: false }; } });
  await assert.rejects(() => requireFunction("searchSources")("margin", searchController.signal), error => error.name === "AbortError");
}));

test("Files workspace wires source snippets and Import to real download and Contents requests", async () => {
  const host = mountedHost(); const oldFetch = globalThis.fetch; const mock = transport(); globalThis.fetch = mock.fetcher;
  const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container);
  try {
    await React.act(async () => root.render(React.createElement(workspace.JupyterWorkspace, { configuration: configured })));
    const input = host.elements("input").find(element => element.getAttribute("name") === "source-query"); assert.ok(input, "Files pane contains source search"); input.value = "radiator margin";
    const form = sourceForm(host); await React.act(async () => form.dispatchEvent({ type: "submit", bubbles: true }));
    assert.match(host.container.textContent, /Generated source snippet/);
    assert.equal(host.elements("b").length, 0, "Snippet text is escaped rather than rendered as markup");
    const button = host.elements("button").find(element => element.textContent === "Import copy"); assert.ok(button);
    await React.act(async () => button.dispatchEvent({ type: "click", bubbles: true, button: 0 }));
    await settleImport(host);
    assert.match(host.container.textContent, /Imported to imports\/report\.txt/);
    assert.ok(mock.requests.some(request => request.method === "PUT"));
    const link = host.elements("a").find(element => element.textContent === "Open copy"); assert.equal(link.getAttribute("href"), "http://127.0.0.1:58101/lab/tree/imports/report.txt");
    assert.equal(jupyterLaunch(host).getAttribute("href"), configured.url);
    assert.match(jupyterLaunch(host).getAttribute("rel"), /noopener/);
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

test("Import opens exact saved Office bytes in the personal preview and preserves the Jupyter launch", async () => {
  const workbook = new ExcelJS.Workbook();
  workbook.addWorksheet("Imported budget").addRows([["Item", "Cost"], ["Generated telescope", 27]]);
  const bytes = new Uint8Array(await workbook.xlsx.writeBuffer());
  const host = focusCapableHost(), oldFetch = globalThis.fetch, createURL = URL.createObjectURL;
  const blobs = []; URL.createObjectURL = blob => { blobs.push(blob); return createURL(blob); };
  let mock;
  mock = transport(request => {
    if (request.url.includes("/download?")) return new Response(bytes, { headers: { "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "content-disposition": 'attachment; filename="report.xlsx"' } });
    if (request.url === "http://127.0.0.1:58101/api/contents/?content=1") return json({ type: "directory", path: "", content: [] });
    if (request.url.endsWith("imports/report.xlsx?content=1&format=base64")) return json({ ...mock.files.get("imports/report.xlsx"), path: "imports/report.xlsx", size: bytes.length, mimetype: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  });
  globalThis.fetch = mock.fetcher;
  const { createRoot } = await import("react-dom/client"), root = createRoot(host.container);
  const click = async label => {
    const button = host.elements("button").find(row => row.textContent === label || row.getAttribute("aria-label") === label);
    assert.ok(button, label); await React.act(async () => button.dispatchEvent({ type: "click", bubbles: true, button: 0 }));
  };
  try {
    await React.act(async () => root.render(React.createElement(workspace.JupyterWorkspace, { configuration: configured })));
    const launch = jupyterLaunch(host); assert.ok(launch, "Configured workspace opens in its own tab");
    host.elements("input").find(row => row.getAttribute("name") === "source-query").value = "budget";
    await React.act(async () => sourceForm(host).dispatchEvent({ type: "submit", bubbles: true }));
    await click("Import copy"); await settleImport(host);
    for (let count = 0; count < 80 && !host.container.textContent.includes("Generated telescope"); count++) await React.act(async () => { await new Promise(resolve => setImmediate(resolve)); });
    assert.match(host.container.textContent, /Generated telescope/, "Import should open our Office renderer instead of a Jupyter binary editor");
    assert.match(host.container.textContent, /Imported budget/);
    assert.equal(jupyterLaunch(host), launch);
    assert.deepEqual(new Uint8Array(await blobs[0].arrayBuffer()), bytes);
    assert.equal(host.elements("a").find(row => row.getAttribute("download") === "report.xlsx").getAttribute("href").startsWith("blob:"), true);
    await click("Close personal preview"); await click("Open copy");
    assert.ok(host.elements("div").some(row => row.getAttribute("role") === "dialog"));
    assert.equal(jupyterLaunch(host), launch);
    const reads = mock.requests.filter(request => request.url.endsWith("imports/report.xlsx?content=1&format=base64"));
    assert.ok(reads.length >= 1); assert.ok(reads.every(request => request.credentials === "include" && request.redirect === "error"));
    assert.equal(mock.requests.filter(request => request.url.includes("/download?")).length, 1);
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; URL.createObjectURL = createURL; host.restore(); } }
});

test("Files workspace exposes the saved copy and incomplete provenance without a full-success claim", async () => {
  const host = mountedHost(); const oldFetch = globalThis.fetch;
  const mock = transport(request => request.url.includes("source-metadata") ? new Response("Generated private failure", { status: 503 }) : undefined); globalThis.fetch = mock.fetcher;
  const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container);
  try {
    await React.act(async () => root.render(React.createElement(workspace.JupyterWorkspace, { configuration: configured })));
    sourceInput(host).value = "margin";
    await React.act(async () => sourceForm(host).dispatchEvent({ type: "submit", bubbles: true }));
    await React.act(async () => host.elements("button").find(element => element.textContent === "Import copy").dispatchEvent({ type: "click", bubbles: true, button: 0 }));
    await settleImport(host);
    assert.match(host.container.textContent, /copy saved at imports\/report\.txt/i);
    assert.match(host.container.textContent, /source metadata.*not.*confirmed/i);
    assert.doesNotMatch(host.container.textContent, /Imported to|Generated private failure/);
    const link = host.elements("a").find(element => element.textContent === "Open copy");
    assert.equal(link.getAttribute("href"), "http://127.0.0.1:58101/lab/tree/imports/report.txt");
    assert.equal(mock.requests.filter(request => request.method === "POST" && request.url.endsWith("/imports")).length, 1);
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});

test("Files workspace shows sign-in and retry and aborts pending work on owner URL change", async () => {
  const host = mountedHost(); const oldFetch = globalThis.fetch; const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container);
  let waiting, finish;
  globalThis.fetch = async (url, options) => {
    if (url === "/api/library/search") return json({ hits: [hit], truncated: false });
    if (url.includes("/api/contents/?content=1")) return json({ type: "directory", path: "", content: [] });
    waiting = options.signal; return new Promise(resolve => { finish = resolve; });
  };
  try {
    await React.act(async () => root.render(React.createElement(workspace.JupyterWorkspace, { configuration: configured })));
    assert.ok(sourceInput(host), "Files pane contains source search");
    sourceInput(host).value = "margin"; await React.act(async () => sourceForm(host).dispatchEvent({ type: "submit", bubbles: true }));
    host.container.ownerDocument.cookie = "";
    await React.act(async () => host.elements("button").find(element => element.textContent === "Import copy").dispatchEvent({ type: "click", bubbles: true, button: 0 }));
    assert.match(host.container.textContent, /sign in/i); assert.ok(host.elements("button").some(element => element.textContent === "Retry Import"));
    const signIn = host.elements("a").find(element => element.textContent === "Sign in to JupyterLab"); assert.equal(signIn.getAttribute("href"), configured.url);
    host.container.ownerDocument.cookie = "_xsrf=generated-xsrf-cookie";
    await React.act(async () => host.elements("button").find(element => element.textContent === "Retry Import").dispatchEvent({ type: "click", bubbles: true, button: 0 }));
    assert.ok(waiting);
    await React.act(async () => root.render(React.createElement(workspace.JupyterWorkspace, { configuration: { status: "configured", url: "http://127.0.0.1:58102/lab" } })));
    assert.equal(waiting.aborted, true);
    await React.act(async () => finish(json({ type: "directory", path: "imports" })));
    assert.doesNotMatch(host.container.textContent, /Generated source snippet|Imported to/);
    const html = renderToStaticMarkup(React.createElement(workspace.JupyterWorkspace, { configuration: { status: "missing" } })); assert.doesNotMatch(html, /<form|Open JupyterLab/);
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});
