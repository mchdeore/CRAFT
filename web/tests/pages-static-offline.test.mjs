import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
const require = createRequire(import.meta.url);
const React = require("react");
const ts = require("typescript");
const { renderToStaticMarkup } = require("react-dom/server");
const main = fileURLToPath(new URL("../src/main.tsx", import.meta.url));
async function boot({ hostname, apiOrigin, production = true }) {
  const source = (await readFile(main, "utf8"))
    .replaceAll("import.meta.env.PROD", String(production))
    .replaceAll("import.meta.env.VITE_API_ORIGIN", apiOrigin === undefined ? "undefined" : JSON.stringify(apiOrigin));
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  let mounted;
  const context = { __appMounts: 0, __requests: [], location: { hostname }, document: { getElementById: () => ({}) }, module: { exports: {} }, exports: {}, console };
  context.globalThis = context;
  const App = () => { context.__appMounts++; context.__requests.push("/api/me"); return React.createElement("form", null, React.createElement("input", { type: "password" })); };
  context.require = name => {
    if (name === "react-dom/client") return { createRoot: () => ({ render(element) { mounted = element; } }) };
    if (name === "@/App") return { __esModule: true, default: App };
    if (name === "@/components/ui/tooltip") return { TooltipProvider: ({ children }) => children };
    if (name === "./index.css") return {};
    return require(name);
  };
  vm.runInNewContext(code, context);
  await Promise.resolve();
  assert.ok(mounted, "actual main entry renders a root");
  return { markup: renderToStaticMarkup(mounted), mounts: context.__appMounts, requests: context.__requests };
}
for (const apiOrigin of [undefined, "   "]) test(`unconfigured public Pages never mounts auth (${JSON.stringify(apiOrigin)})`, async () => {
  const result = await boot({ hostname: "mchdeore.github.io", apiOrigin });
  assert.equal(result.mounts, 0);
  assert.deepEqual(result.requests, []);
  assert.match(result.markup, /Sign-in is unavailable while the demo backend is offline/);
  assert.doesNotMatch(result.markup, /<form|type="password"/);
  assert.match(result.markup, /href="\.\.\/preview\/"/);
});
for (const config of [{ hostname: "mchdeore.github.io", apiOrigin: "https://api.generated.example" }, { hostname: "localhost" }, { hostname: "127.0.0.1" }, { hostname: "demo.generated.example" }, { hostname: "mchdeore.github.io", production: false }]) test(`configured or same-origin App still boots (${config.hostname}/${config.production ?? true})`, async () => {
  const result = await boot(config);
  assert.equal(result.mounts, 1);
  assert.deepEqual(result.requests, ["/api/me"]);
  assert.match(result.markup, /type="password"/);
});
