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
const requireFunction = name => { assert.equal(typeof client[name], "function", `Missing source import helper ${name}`); return client[name]; };
const configured = { status: "configured", url: "http://127.0.0.1:58101/lab" };
const source = { item: "generated-source-1", version: 3, title: "Generated Report", snippet: "<b>Generated source snippet</b>", classification: "unclassified" };
const hit = { kind: "document", handle: { item: source.item, version: 3, block: 1, locator: "p1", quoteSha256: "0".repeat(64), simulated: true }, title: source.title, text: source.snippet, classification: "unclassified", headingPath: "", score: 0.9, tags: { mission: null, silo: null, stage: null }, before: null, after: null };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const freshSignal = () => new AbortController().signal;
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
        do { fresh = `${path ? path + "/" : ""}untitled${index || ""}${ext}`; index++; } while (files.has(fresh));
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
const deferred = () => {
  let resolve;
  const promise = new Promise(finish => { resolve = finish; });
  return { promise, resolve };
};

test("lost personal mutation responses are uncertain outcomes rather than retryable imports", async t => {
  const cases = [
    { name: "create response lost", method: "POST", endpoint: "/api/contents/", possiblePath: undefined },
    { name: "rename response lost", method: "PATCH", endpoint: "/untitled.txt", possiblePath: "report.txt" },
    { name: "save response lost", method: "PUT", endpoint: "/report.txt", possiblePath: "report.txt" },
  ];
  for (const fixture of cases) await t.test(fixture.name, async () => environment(async () => {
    let mock;
    mock = transport(request => {
      if (request.method !== fixture.method || !request.url.endsWith(fixture.endpoint)) return;
      if (fixture.method === "POST") mock.files.set("untitled.txt", { type: "file", format: "text", content: "" });
      if (fixture.method === "PATCH") { mock.files.set("report.txt", mock.files.get("untitled.txt")); mock.files.delete("untitled.txt"); }
      if (fixture.method === "PUT") mock.files.set("report.txt", JSON.parse(request.body));
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
    if (fixture.method === "PUT") assert.equal(Buffer.from(mock.files.get("report.txt").content, "base64").toString(), "Generated original bytes");
  }));
});

test("an untrusted personal save model never confirms a copy or exposes an unsafe path", async () => environment(async () => {
  const mock = transport(request => request.method === "PUT" && request.url.endsWith("/report.txt")
    ? json({ type: "file", path: "../../another-owner.txt" }) : undefined);
  globalThis.fetch = mock.fetcher;
  await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => {
    assert.equal(error.code, "outcome_unknown");
    assert.equal(error.copiedPath, undefined);
    assert.equal(error.possiblePath, "report.txt");
    assert.doesNotMatch(error.message, /another-owner/);
    return true;
  });
  assert.equal(mock.requests.some(request => request.url.includes("source-metadata")), false);
}));

test("a mismatched metadata directory keeps the saved copy and writes no source metadata", async () => environment(async () => {
  const mock = transport(request => {
    if (request.url.endsWith("/imports?content=0")) return new Response(null, { status: 404 });
    if (request.url.endsWith("/imports") && request.method === "PUT") return json({ type: "directory", path: "other-owner" }, 201);
  }); globalThis.fetch = mock.fetcher;
  // The copy is saved at the workspace root first; only its provenance record lives under imports/.
  await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "metadata_unavailable" && error.copiedPath === "report.txt");
  assert.equal(mock.requests.some(request => request.url.includes("source-metadata")), false);
}));

test("Import downloads the selected version and uploads only a newly created personal file", async () => environment(async () => {
  const mock = transport(); globalThis.fetch = mock.fetcher;
  const path = await requireFunction("importSource")(source, configured, freshSignal());
  assert.equal(path, "report.txt");
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
  assert.equal(JSON.parse(renames[0].body).path, "report.txt");
  assert.equal(JSON.parse(renames[19].body).path, "report-20.txt");
  assert.equal(mock.requests.filter(request => request.method === "POST").length, 1);
}));

test("Import selects a readable available name without replacing previous copies", async () => environment(async () => {
  const mock = transport(); globalThis.fetch = mock.fetcher;
  const first = { type: "file", format: "text", content: "Keep first personal edit" };
  const second = { type: "file", format: "text", content: "Keep second personal edit" };
  mock.files.set("report.txt", first); mock.files.set("report-2.txt", second);
  const path = await requireFunction("importSource")(source, configured, freshSignal());
  assert.equal(path, "report-3.txt");
  assert.deepEqual(mock.files.get("report.txt"), first);
  assert.deepEqual(mock.files.get("report-2.txt"), second);
  assert.equal(Buffer.from(mock.files.get(path).content, "base64").toString(), "Generated original bytes");
  assert.deepEqual(mock.requests.filter(request => request.method === "PATCH" && request.url.endsWith("/untitled.txt")).map(request => JSON.parse(request.body).path), ["report.txt", "report-2.txt", "report-3.txt"]);
  assert.equal(mock.requests.filter(request => request.url.includes("/download?")).length, 1);
  assert.equal(mock.requests.some(request => request.url.includes("/contents/report") && request.method === "GET"), false);
}));

test("Import does not retry a failed rename that is not a name conflict", async () => environment(async () => {
  const mock = transport(request => request.method === "PATCH" ? new Response("Generated private failure", { status: 500 }) : undefined); globalThis.fetch = mock.fetcher;
  await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "outcome_unknown" && error.copiedPath === undefined && error.possiblePath === "report.txt");
  assert.equal(mock.requests.filter(request => request.method === "PATCH").length, 1);
  assert.equal(mock.requests.some(request => request.method === "PUT"), false);
}));

test("Import saves separate bounded source provenance for each readable personal copy", async () => environment(async () => {
  const mock = transport(); globalThis.fetch = mock.fetcher;
  const paths = [await requireFunction("importSource")(source, configured, freshSignal()), await requireFunction("importSource")(source, configured, freshSignal())];
  assert.deepEqual(paths, ["report.txt", "report-2.txt"]);
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
    await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "metadata_unavailable" && error.copiedPath === "report.txt" && !error.message.includes("private"));
    assert.equal(Buffer.from(mock.files.get("report.txt").content, "base64").toString(), "Generated original bytes");
    assert.equal(mock.requests.filter(request => request.method === "POST" && request.url.endsWith("/api/contents/")).length, 1);
    assert.equal(mock.requests.filter(request => request.method === "PUT" && request.url.endsWith("/report.txt")).length, 1);
    assert.equal(mock.requests.some(request => request.method === "DELETE"), false);
  }));
});

test("Import does not continue provenance writes after cancellation of a confirmed copy", async () => environment(async () => {
  const controller = new AbortController();
  const mock = transport(request => { if (request.url.includes("source-metadata")) { controller.abort(); return json({ type: "directory", path: "imports/source-metadata" }); } }); globalThis.fetch = mock.fetcher;
  await assert.rejects(() => requireFunction("importSource")(source, configured, controller.signal), error => error.code === "metadata_unavailable" && error.copiedPath === "report.txt");
  assert.equal(controller.signal.aborted, true);
  assert.equal(mock.requests.some(request => request.url.includes("source-metadata") && request.method !== "GET"), false);
  assert.equal(Buffer.from(mock.files.get("report.txt").content, "base64").toString(), "Generated original bytes");
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
    let bodyReads = 0;
    globalThis.fetch = transport(request => request.url.startsWith("http://127.0.0.1:58101/") ? { status: 403, ok: false, json() { bodyReads++; throw Error("Generated secret server body"); } } : undefined).fetcher;
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
  const mock = transport(request => request.method === "POST" ? json({ type: "file", path: "../../private.txt" }, 201) : undefined); globalThis.fetch = mock.fetcher;
  await assert.rejects(() => requireFunction("importSource")(source, configured, freshSignal()), error => error.code === "outcome_unknown" && error.possiblePath === undefined); assert.equal(mock.requests.some(request => request.method === "PUT"), false);
}));

