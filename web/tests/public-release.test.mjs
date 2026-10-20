import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import path from "node:path";
import "./typescript-loader.mjs";

const root = path.resolve(import.meta.dirname, "..");
const configURL = new URL("../vite.config.ts", import.meta.url);
let sequence = 0;
async function config(command, target, mock = "true") {
  const saved = { target: process.env.CRAFT_BACKEND_URL, mock: process.env.VITE_MOCK };
  if (target === undefined) delete process.env.CRAFT_BACKEND_URL;
  else process.env.CRAFT_BACKEND_URL = target;
  process.env.VITE_MOCK = mock;
  try {
    const declared = (await import(configURL.href + "?case=" + sequence++)).default;
    return typeof declared === "function" ? await declared({ command, mode: command === "build" ? "production" : "development", isSsrBuild: false, isPreview: false }) : declared;
  } finally {
    if (saved.target === undefined) delete process.env.CRAFT_BACKEND_URL; else process.env.CRAFT_BACKEND_URL = saved.target;
    if (saved.mock === undefined) delete process.env.VITE_MOCK; else process.env.VITE_MOCK = saved.mock;
  }
}

test("public build works without a loopback backend configuration", async () => {
  const value = await config("build");
  assert.equal(value.base, "./");
  assert.equal(value.server?.proxy, undefined);
});
test("production build pins mock mode and excludes env files, maps and general public fixtures", async () => {
  const value = await config("build", "http://127.0.0.1:53516", "true");
  assert.equal(value.define?.["import.meta.env.VITE_MOCK"], JSON.stringify("false"));
  assert.equal(value.envDir, false);
  assert.equal(value.build?.sourcemap, false);
  assert.equal(path.basename(value.build?.outDir ?? ""), "dist");
  assert.equal(path.basename(value.publicDir ?? ""), "release-public");
});
test("public build is independent of a configured development-only remote target", async () => {
  const value = await config("build", "https://foreign.generated.example");
  assert.equal(value.base, "./");
  assert.equal(value.server?.proxy, undefined);
});
test("development still rejects a missing or foreign backend", async () => {
  await assert.rejects(config("serve"), /CRAFT_BACKEND_URL/);
  await assert.rejects(config("serve", "https://foreign.generated.example"), /CRAFT_BACKEND_URL/);
});
test("development preserves the existing explicit loopback proxies and origin", async () => {
  const value = await config("serve", "http://127.0.0.1:53516", "false");
  for (const route of ["/api", "/health", "/mcp"]) {
    assert.equal(value.server.proxy[route].target, "http://127.0.0.1:53516");
    assert.equal(value.server.proxy[route].changeOrigin, false);
  }
  assert.notEqual(value.define?.["import.meta.env.VITE_MOCK"], JSON.stringify("false"));
});

// A test-directory class must never influence the published application CSS.
const scannerCanary = "z-[987654]";
const output = process.env.CRAFT_RELEASE_TEST_OUTPUT;
assert.ok(output && path.isAbsolute(output), "Root must provide a fresh retained artifact output directory");
assert.equal(existsSync(output), false);
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => ["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"].includes(key)));
Object.assign(env, { NO_COLOR: "1", CRAFT_BACKEND_URL: "http://127.0.0.1:53516", VITE_API_ORIGIN: "https://api.generated.example", VITE_MOCK: "true" });
execFileSync(process.execPath, [path.join(root, "node_modules/vite/bin/vite.js"), "build", "--outDir", output], { cwd: root, env, timeout: 180000, stdio: "pipe" });
function files(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => entry.isDirectory() ? files(path.join(directory, entry.name)) : [path.join(directory, entry.name)]);
}
const artifacts = files(output);
const texts = artifacts.filter(file => /\.(html|js|css|svg)$/.test(file)).map(file => ({ file, text: readFileSync(file, "utf8") }));
const html = readFileSync(path.join(output, "index.html"), "utf8");
const page = "https://mchdeore.github.io/CRAFT/demo/";
function mountedAsset(reference, base = page) {
  const url = new URL(reference, base);
  assert.equal(url.origin, new URL(page).origin);
  assert.ok(url.pathname.startsWith("/CRAFT/demo/"), "Asset must stay under the application mount");
  const local = path.join(output, decodeURIComponent(url.pathname.slice("/CRAFT/demo/".length)));
  assert.ok(existsSync(local), "Every published asset edge must resolve");
  return local;
}
test("emitted entry assets load under the GitHub Pages application mount", () => {
  const links = [...html.matchAll(/(?:src|href)=["']([^"']+)["']/g)].map(match => match[1]);
  assert.ok(links.some(link => link.endsWith(".js")) && links.some(link => link.endsWith(".css")));
  for (const link of links) mountedAsset(link);
});
test("production artifact omits copied documents, mock workers, maps and raw private source", () => {
  assert.ok(artifacts.length > 3);
  for (const file of artifacts) {
    const relative = path.relative(output, file);
    assert.ok(/\.(html|js|css|svg|woff2?|ttf|otf)$/.test(relative), "Only actual runtime asset types may ship");
    assert.doesNotMatch(relative, /(?:mock|node_modules|\.env|\.map$|\.tsx?$|package|\.docx$)/i);
  }
  for (const { text } of texts) assert.doesNotMatch(text, /mockServiceWorker|mock-files\/|mocks\/browser|\/Users\/|\/\.local\/|sourcesContent|\/\/[#@]\s*sourceMappingURL=/);
});
test("curated SVG assets preserve the original audited bytes", () => {
  for (const name of ["favicon.svg", "icons.svg"]) assert.deepEqual(readFileSync(path.join(output, name)), readFileSync(path.join(root, "public", name)));
});
test("application CSS keeps core styles and resolvable font assets", () => {
  const styles = texts.filter(value => value.file.endsWith(".css"));
  assert.ok(styles.length > 0);
  assert.ok(styles.some(value => value.text.includes("@font-face") && value.text.includes("Geist")));
  assert.ok(styles.some(value => /\.flex\s*\{display:flex\}/.test(value.text)));
  for (const { file, text } of styles) {
    const base = new URL(path.relative(output, file).split(path.sep).join("/"), page).href;
    for (const [, reference] of text.matchAll(/url\(["']?([^\s"')]+)["']?\)/g)) if (!reference.startsWith("data:")) mountedAsset(reference, base);
  }
});
test("test and private directory class canaries do not enter the application CSS", () => {
  assert.equal(scannerCanary, "z-[987654]");
  for (const { text } of texts.filter(value => value.file.endsWith(".css"))) assert.doesNotMatch(text, /z-index:\s*987654/);
});
