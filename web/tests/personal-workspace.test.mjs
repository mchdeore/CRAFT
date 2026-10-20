import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import "./typescript-loader.mjs";
import { mountedHost } from "./source-import-host.mjs";

const moduleURL = new URL("../src/lib/personal-workspace.ts", import.meta.url);
const client = existsSync(moduleURL) ? await import(moduleURL) : {};
const requireFunction = name => { assert.equal(typeof client[name], "function", `Missing personal workspace helper ${name}`); return client[name]; };
const configured = { status: "configured", url: "http://127.0.0.1:58101/lab" };
const origin = "http://127.0.0.1:58101";
const signal = () => new AbortController().signal;
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const file = (path, bytes = new TextEncoder().encode("Generated personal bytes"), extra = {}) => ({ name: path.split("/").at(-1), path, type: "file", writable: true, mimetype: "text/plain", format: "base64", content: Buffer.from(bytes).toString("base64"), size: bytes.length, ...extra });
const jupyterFile = (path, bytes, lineEnding = "\n", extra = {}) => {
  const model = file(path, bytes, extra);
  return { ...model, content: model.content.replace(/.{1,76}/g, line => line + lineEnding) };
};
const directory = (path, content = []) => ({ name: path.split("/").at(-1) ?? "", path, type: "directory", writable: true, format: "json", content });
let host, originalFetch;
beforeEach(() => { host = mountedHost(); originalFetch = globalThis.fetch; });
afterEach(() => { globalThis.fetch = originalFetch; host.restore(); });

function transport(initial = new Map()) {
  const models = new Map(initial); const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, ...options });
    const parsed = new URL(url); assert.equal(parsed.origin, origin);
    const path = parsed.pathname.slice("/api/contents/".length).split("/").map(decodeURIComponent).join("/");
    const method = options.method ?? "GET";
    const input = options.body ? JSON.parse(options.body) : null;
    if (method === "GET") {
      if (!models.has(path)) return json({}, 404);
      const model = models.get(path);
      if (model.type === "directory") return json(directory(path, [...models.values()].filter(row => row.path && row.path.split("/").slice(0, -1).join("/") === path).map(row => ({ ...row, content: null, format: null }))));
      return json(model);
    }
    if (method === "POST") {
      const fresh = `${path}/untitled${input.ext}`; assert.equal(models.has(fresh), false);
      const result = file(fresh, new Uint8Array()); models.set(fresh, result); return json({ ...result, content: null }, 201);
    }
    if (method === "PATCH") {
      if (models.has(input.path)) return json({ message: "Generated filename conflict" }, 409);
      const old = models.get(path); assert.ok(old, "Rename must address the fresh owned reservation");
      models.delete(path); const moved = { ...old, path: input.path, name: input.path.split("/").at(-1) }; models.set(input.path, moved); return json({ ...moved, content: null });
    }
    if (input.type === "directory") { const result = directory(path); models.set(path, result); return json(result, 201); }
    const saved = file(path, new TextEncoder().encode(input.content)); models.set(path, saved); return json({ ...saved, content: null });
  };
  return { models, requests };
}

test("personal Contents reads use only the configured owner origin, cookies and abort signal", async () => {
  const requests = []; const active = signal();
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return json(directory("imports", [file("imports/report.xlsx")])); };
  assert.equal((await requireFunction("listPersonalDirectory")(configured, "imports", active))[0].path, "imports/report.xlsx");
  assert.equal(requests[0].url, `${origin}/api/contents/imports?content=1`);
  assert.equal(requests[0].options.credentials, "include"); assert.equal(requests[0].options.redirect, "error");
  assert.equal(requests[0].options.cache, "no-store"); assert.equal(requests[0].options.signal, active);
  assert.equal(requests[0].options.headers, undefined);
});

test("invalid owner addresses and escaping paths fail before any request", async () => {
  let calls = 0; globalThis.fetch = async () => { calls++; return json(directory("")); };
  for (const configuration of [{ status: "missing" }, { status: "configured", url: "http://user:secret@127.0.0.1:58101/lab" }, { status: "configured", url: "http://127.0.0.1:58101/lab?token=secret" }, { status: "configured", url: "http://127.0.0.1:53520/lab" }, { status: "configured", url: "http://example.test/lab" }]) {
    await assert.rejects(() => requireFunction("listPersonalDirectory")(configuration, "", signal()), error => error.code === "invalid_workspace" && !error.message.includes("secret"));
  }
  for (const path of ["../source", "/workspace/imports", "imports/../secret", "imports//file", "imports\\file", "imports/%2e%2e/file", "imports/\u0000file", ".hidden", "imports/.hidden"]) {
    await assert.rejects(() => requireFunction("listPersonalDirectory")(configured, path, signal()), error => error.code === "invalid_path");
  }
  assert.equal(calls, 0);
});

test("directory replies cannot introduce outside paths, duplicate files or malformed models", async () => {
  for (const body of [directory("other"), directory("imports", [file("outside/report.xlsx")]), directory("imports", [file("imports/a.md"), file("imports/a.md")]), directory("imports", [file("imports/a.md", undefined, { name: "other.md" })]), { ...directory("imports"), content: null }]) {
    globalThis.fetch = async () => json(body);
    await assert.rejects(() => requireFunction("listPersonalDirectory")(configured, "imports", signal()), error => error.code === "invalid_response");
  }
  globalThis.fetch = async () => json(directory("imports", [file("imports/.hidden"), file("imports/report.xlsx")]));
  assert.deepEqual((await requireFunction("listPersonalDirectory")(configured, "imports", signal())).map(row => row.path), ["imports/report.xlsx"]);
});

test("slash skills are only direct personal skills/*.md files and never app catalog records", async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return json(directory("skills", [file("skills/check.md"), file("skills/readme.txt"), file("skills/notebook.ipynb", undefined, { type: "notebook" }), directory("skills/folder")])); };
  assert.deepEqual((await requireFunction("listPersonalSkills")(configured, signal())).map(row => row.path), ["skills/check.md"]);
  assert.deepEqual(requests.map(row => row.url), [`${origin}/api/contents/skills?content=1`]);
  globalThis.fetch = async () => json({}, 404);
  assert.deepEqual(await requireFunction("listPersonalSkills")(configured, signal()), []);
});

test("at-file discovery keeps notebooks and scripts inside the personal root with a bounded tree", async () => {
  const entries = new Map([["", directory("")], ["scripts", directory("scripts")], ["notebooks", directory("notebooks")], ["skills", directory("skills")], ["imports", directory("imports")], ["scripts/analysis.py", file("scripts/analysis.py")], ["notebooks/analysis.ipynb", file("notebooks/analysis.ipynb", undefined, { type: "notebook" })], ["imports/report.xlsx", file("imports/report.xlsx")], ["skills/check.md", file("skills/check.md")]]);
  const network = transport(entries);
  const result = await requireFunction("listPersonalFiles")(configured, signal());
  assert.equal(result.limited, false);
  assert.deepEqual(result.files.map(row => row.path).sort(), ["imports/report.xlsx", "notebooks/analysis.ipynb", "scripts/analysis.py", "skills/check.md"]);
  assert.ok(network.requests.every(row => row.method === "GET"));
  let calls = 0;
  globalThis.fetch = async url => { calls++; const path = new URL(url).pathname.slice("/api/contents/".length); return json(directory(path, Array.from({ length: 80 }, (_, index) => directory(`${path ? path + "/" : ""}folder-${index}`)))); };
  const bounded = await requireFunction("listPersonalFiles")(configured, signal());
  assert.equal(bounded.limited, true); assert.ok(calls <= 64, "Tree scans must have a hard request bound");
});

test("personal Office bytes are read as bounded authenticated Contents base64 without a public URL", async () => {
  const bytes = Uint8Array.of(80, 75, 3, 4, 0, 255); const active = signal(); let request;
  globalThis.fetch = async (url, options) => { request = { url, options }; return json(file("imports/report.xlsx", bytes, { mimetype: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" })); };
  const result = await requireFunction("readPersonalFile")(configured, "imports/report.xlsx", active);
  assert.deepEqual(result.bytes, bytes); assert.match(result.mimeType, /spreadsheetml/);
  assert.equal(request.url, `${origin}/api/contents/imports/report.xlsx?content=1&format=base64`);
  assert.equal(request.options.credentials, "include"); assert.equal(request.options.signal, active);
  assert.equal(requireFunction("personalAbsolutePath")("imports/report.xlsx"), "/workspace/imports/report.xlsx");
});

test("notebook raw-byte previews force the Contents file model without changing notebook bytes", async () => {
  const bytes = new TextEncoder().encode('{\n  "nbformat": 4, "nbformat_minor": 5, "cells": []\n}\n'); let request;
  globalThis.fetch = async (url, options) => { request = { url, options }; return json(jupyterFile("notebooks/My result.ipynb", bytes)); };
  const result = await requireFunction("readPersonalFile")(configured, "notebooks/My result.ipynb", signal());
  assert.equal(request.url, `${origin}/api/contents/notebooks/My%20result.ipynb?content=1&format=base64&type=file`);
  assert.deepEqual(result.bytes, bytes); assert.equal(request.options.credentials, "include"); assert.equal(request.options.redirect, "error");
});

for (const [endingName, lineEnding] of [["LF", "\n"], ["CRLF", "\r\n"]]) {
  for (const [path, size, mimeType] of [
    ["imports/payload-rationale-2.docx", 37412, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
    ["imports/engineering.xlsx", 5210, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
  ]) {
    test(`Jupyter ${endingName} wrapping preserves every byte of ${path}`, async () => {
      const bytes = Uint8Array.from({ length: size }, (_, index) => index % 256);
      bytes.set([80, 75, 3, 4]);
      globalThis.fetch = async () => json(jupyterFile(path, bytes, lineEnding, { mimetype: mimeType }));
      const result = await requireFunction("readPersonalFile")(configured, path, signal());
      assert.deepEqual(result.bytes, bytes); assert.equal(result.mimeType, mimeType);
    });
  }

  test(`Jupyter ${endingName} wrapping accepts a 10 MB file including escaped JSON overhead`, async () => {
    const limit = 10 * 1024 * 1024; const bytes = new Uint8Array(limit);
    bytes.set([80, 75, 3, 4, 255]); bytes[limit - 1] = 1;
    const wire = JSON.stringify(jupyterFile("imports/limit.xlsx", bytes, lineEnding));
    const headers = { "content-type": "application/json", ...(endingName === "CRLF" ? { "content-length": String(Buffer.byteLength(wire)) } : {}) };
    globalThis.fetch = async () => new Response(wire, { headers });
    const result = await requireFunction("readPersonalFile")(configured, "imports/limit.xlsx", signal(), limit);
    assert.deepEqual(result.bytes, bytes);
  });

  test(`Jupyter ${endingName} wrapping accepts an exact skill-document byte limit`, async () => {
    const bytes = new Uint8Array(200000).fill(65);
    globalThis.fetch = async () => json(jupyterFile("skills/check.md", bytes, lineEnding));
    assert.equal(await requireFunction("loadPersonalSkill")(configured, "skills/check.md", signal()), "A".repeat(200000));
  });
}

test("compact base64 still accepts a file at the 10 MB byte limit", async () => {
  const bytes = new Uint8Array(10 * 1024 * 1024).fill(255);
  globalThis.fetch = async () => json(file("imports/limit.xlsx", bytes));
  assert.deepEqual((await requireFunction("readPersonalFile")(configured, "imports/limit.xlsx", signal())).bytes, bytes);
});

test("wrapped base64 retains the decoded byte bound even when padding masks an oversized file", async () => {
  const limit = 58;
  globalThis.fetch = async () => json(jupyterFile("imports/report.xlsx", new Uint8Array(limit), "\r\n"));
  assert.equal((await requireFunction("readPersonalFile")(configured, "imports/report.xlsx", signal(), limit)).bytes.length, limit);
  for (const size of [limit, limit + 1]) {
    globalThis.fetch = async () => json(jupyterFile("imports/report.xlsx", new Uint8Array(limit + 1), "\r\n", { size }));
    await assert.rejects(() => requireFunction("readPersonalFile")(configured, "imports/report.xlsx", signal(), limit), error => error.code === "too_large");
  }
});

test("line wrapping never admits other whitespace or malformed base64", async () => {
  for (const content of ["YW Jj", "YWJj\t", "YWJj\r", "YWJj\v", "YWJj\f", "YWJj\u00a0", "YWJj\u2028", "YW\nJj", "\nYWJj", "YWJj\n\n", "A\n", "YW=Jj\n", "YWJj===\n", "_-AA\n", "A".repeat(76) + "\n%%%", "YQ==\nYQ==\n"]) {
    globalThis.fetch = async () => json(file("imports/report.xlsx", undefined, { content }));
    await assert.rejects(() => requireFunction("readPersonalFile")(configured, "imports/report.xlsx", signal()), error => error.code === "invalid_response", JSON.stringify(content));
  }
});

test("line wrapping has a finite wire-content allowance before normalization", async () => {
  globalThis.fetch = async () => json(file("imports/report.xlsx", new Uint8Array(57), { content: "A".repeat(76) + "\n".repeat(10) }));
  await assert.rejects(() => requireFunction("readPersonalFile")(configured, "imports/report.xlsx", signal(), 57), error => error.code === "too_large");
});

for (const declaredLength of [false, true]) {
  test(`wrapped file JSON remains bounded with ${declaredLength ? "declared" : "streamed"} length and cancels oversized bodies`, async () => {
    let cancelled = false;
    globalThis.fetch = async () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(new Uint8Array(declaredLength ? 2 : 16465)); },
      cancel() { cancelled = true; },
    }), { headers: { "content-type": "application/json", ...(declaredLength ? { "content-length": "16465" } : {}) } });
    await assert.rejects(() => requireFunction("readPersonalFile")(configured, "imports/report.xlsx", signal(), 57), error => error.code === "too_large");
    assert.equal(cancelled, true);
  });
}

test("malformed and oversized file bodies fail before any renderer or mutation", async () => {
  for (const body of [file("outside/report.xlsx"), file("imports/report.xlsx", undefined, { format: "text" }), file("imports/report.xlsx", undefined, { content: "%%%" }), file("imports/report.xlsx", undefined, { size: 10 * 1024 * 1024 + 1 })]) {
    globalThis.fetch = async () => json(body);
    await assert.rejects(() => requireFunction("readPersonalFile")(configured, "imports/report.xlsx", signal()), error => ["invalid_response", "too_large"].includes(error.code));
  }
  let cancelled = false;
  globalThis.fetch = async () => new Response(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(512 * 1024 + 1)); }, cancel() { cancelled = true; } }), { headers: { "content-type": "application/json" } });
  await assert.rejects(() => requireFunction("listPersonalDirectory")(configured, "imports", signal()), error => error.code === "too_large");
  assert.equal(cancelled, true);
});

test("authentication and service failures do not inspect or expose response bodies", async () => {
  for (const [status, code] of [[401, "workspace_auth"], [403, "workspace_auth"], [503, "unavailable"]]) {
    let reads = 0; globalThis.fetch = async () => ({ status, ok: false, json() { reads++; throw Error("private body"); }, text() { reads++; throw Error("private body"); } });
    await assert.rejects(() => requireFunction("listPersonalDirectory")(configured, "imports", signal()), error => error.code === code && !error.message.includes("private"));
    assert.equal(reads, 0);
  }
});

test("new Markdown save reserves then renames one fresh personal file before writing", async () => {
  const network = transport(new Map([["skills", directory("skills")]])); const active = signal();
  assert.equal(await requireFunction("savePersonalSkill")(configured, { filename: "review.md", content: "# Goal\nCompare inputs.\n" }, active), "skills/review.md");
  assert.deepEqual(network.requests.map(row => row.method), ["GET", "POST", "PATCH", "PUT"]);
  assert.equal(network.requests[1].url, `${origin}/api/contents/skills`);
  assert.deepEqual(JSON.parse(network.requests[1].body), { type: "file", ext: ".md" });
  assert.equal(network.requests[2].url, `${origin}/api/contents/skills/untitled.md`);
  assert.deepEqual(JSON.parse(network.requests[2].body), { path: "skills/review.md" });
  assert.deepEqual(JSON.parse(network.requests[3].body), { type: "file", format: "text", content: "# Goal\nCompare inputs.\n" });
  for (const row of network.requests.slice(1)) { assert.equal(row.credentials, "include"); assert.equal(row.headers["X-XSRFToken"], "generated-xsrf-cookie"); assert.equal(row.signal, active); }
  assert.equal(network.requests.some(row => row.url.startsWith("/api/skills")), false);
});

test("skill filename conflicts never overwrite an existing file or blindly retry writes", async () => {
  const original = file("skills/review.md"); const network = transport(new Map([["skills", directory("skills")], [original.path, original]]));
  await assert.rejects(() => requireFunction("savePersonalSkill")(configured, { filename: "review.md", content: "New instructions" }, signal()), error => error.code === "conflict");
  assert.equal(network.requests.filter(row => row.method === "PATCH").length, 1); assert.equal(network.requests.filter(row => row.method === "PUT").length, 0);
  assert.deepEqual(network.models.get(original.path), original);
});

test("intentional editing writes only the loaded Markdown path and is not an immutable revision save", async () => {
  const network = transport(new Map([["skills/review.md", file("skills/review.md")]]));
  assert.equal(await requireFunction("savePersonalSkill")(configured, { filename: "review.md", path: "skills/review.md", content: "Edited current file" }, signal()), "skills/review.md");
  assert.deepEqual(network.requests.map(row => row.method), ["PUT"]);
  assert.deepEqual(JSON.parse(network.requests[0].body), { type: "file", format: "text", content: "Edited current file" });
  const before = network.requests.length;
  for (const request of [{ filename: "other.md", path: "skills/review.md", content: "x" }, { filename: "review.md", path: "imports/review.md", content: "x" }, { filename: "../review.md", content: "x" }, { filename: "review.py", content: "x" }, { filename: "review.md", content: "😀".repeat(50001) }]) {
    await assert.rejects(() => requireFunction("savePersonalSkill")(configured, request, signal()));
  }
  assert.equal(network.requests.length, before);
});

test("new skill directory creation and failed save retain an honest unconfirmed status", async () => {
  const network = transport();
  await requireFunction("savePersonalSkill")(configured, { filename: "new.md", content: "# Goal" }, signal());
  assert.deepEqual(network.requests.map(row => row.method), ["GET", "PUT", "POST", "PATCH", "PUT"]);
  let calls = 0; globalThis.fetch = async () => { calls++; throw Error("private failure"); };
  await assert.rejects(() => requireFunction("savePersonalSkill")(configured, { filename: "new.md", path: "skills/new.md", content: "Changed" }, signal()), error => error.code === "save_unconfirmed" && /inspect/i.test(error.message) && !error.message.includes("private"));
  assert.equal(calls, 1);
});

test("missing or ambiguous XSRF cookie blocks mutations before fetching", async () => {
  for (const cookie of ["", "_xsrf=a; _xsrf=b", "_xsrf=%0A", "_xsrf=bad value"]) {
    host.container.ownerDocument.cookie = cookie;
    let calls = 0; globalThis.fetch = async () => { calls++; return json({}); };
    await assert.rejects(() => requireFunction("savePersonalSkill")(configured, { filename: "check.md", path: "skills/check.md", content: "# Goal" }, signal()), error => error.code === "workspace_auth");
    assert.equal(calls, 0);
  }
});

test("Markdown loading enforces direct skill path, byte bound and valid UTF-8", async () => {
  globalThis.fetch = async () => json(file("skills/check.md", new TextEncoder().encode("# Goal\nGenerated")));
  assert.equal(await requireFunction("loadPersonalSkill")(configured, "skills/check.md", signal()), "# Goal\nGenerated");
  globalThis.fetch = async () => json(file("skills/check.md", Uint8Array.of(255)));
  await assert.rejects(() => requireFunction("loadPersonalSkill")(configured, "skills/check.md", signal()), error => error.code === "invalid_response");
  await assert.rejects(() => requireFunction("loadPersonalSkill")(configured, "skills/sub/check.md", signal()), error => error.code === "invalid_path");
});

test("aborted and late owner replies never expose bytes or dispatch a mutation", async () => {
  const early = new AbortController(); early.abort(); let calls = 0;
  globalThis.fetch = async () => { calls++; return json(directory("")); };
  await assert.rejects(() => requireFunction("listPersonalDirectory")(configured, "", early.signal), error => error.name === "AbortError"); assert.equal(calls, 0);
  const late = new AbortController();
  globalThis.fetch = async () => { late.abort(); return json(file("skills/check.md")); };
  await assert.rejects(() => requireFunction("readPersonalFile")(configured, "skills/check.md", late.signal), error => error.name === "AbortError");
});
