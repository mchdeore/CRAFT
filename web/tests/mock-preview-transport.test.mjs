import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import "./typescript-loader.mjs";
registerHooks({ load(url, context, next) {
  const loaded = next(url, context);
  if (url.endsWith("/src/lib/api-client.ts") || url.endsWith("/src/components/citations/preview/use-source-preview.ts")) {
    return { ...loaded, source: String(loaded.source).replace(/import\.meta\.env\b/g, JSON.stringify({ VITE_API_ORIGIN: "https://api.generated.example", VITE_MOCK: "true" })) };
  }
  return loaded;
} });
const { loadDocumentPreview } = await import("../src/components/citations/preview/use-source-preview.ts");
const path = "/mock-files/generated.txt";
const response = text => new Response(text, { headers: { "Content-Type": "text/plain" } });
test("explicit mock-preview fetcher remains authoritative", async () => {
  const oldFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async () => { throw new Error("Global fetch must not replace the explicit fetcher"); };
  try {
    const signal = new AbortController().signal;
    const result = await loadDocumentPreview({ previewURL: path }, signal, async (input, init) => { calls.push({ input, init }); return response("Override preview"); });
    assert.equal(result.type, "ready"); assert.equal(await result.blob.text(), "Override preview");
    assert.equal(calls.length, 1); assert.equal(calls[0].input, path); assert.equal(calls[0].init.signal, signal); assert.equal(calls[0].init.credentials, "same-origin");
  } finally { globalThis.fetch = oldFetch; }
});
test("default mock-preview fetch uses the original local asset path", async () => {
  const oldFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (input, init) => { calls.push({ input, init }); return response("Default preview"); };
  try {
    const result = await loadDocumentPreview({ previewURL: path }, new AbortController().signal);
    assert.equal(result.type, "ready"); assert.equal(await result.blob.text(), "Default preview");
    assert.equal(calls.length, 1); assert.equal(calls[0].input, path); assert.equal(calls[0].init.credentials, "same-origin");
  } finally { globalThis.fetch = oldFetch; }
});
