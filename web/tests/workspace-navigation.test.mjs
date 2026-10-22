import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import "./typescript-loader.mjs";
import { mountedHost } from "./source-import-host.mjs";
const { JupyterWorkspace } = await import("../src/components/jupyter-workspace.tsx");

const configured = { status: "configured", url: "http://127.0.0.1:58101/lab" };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const tick = () => new Promise(resolve => setImmediate(resolve));
const buttonAt = (host, label) => host.elements("button").find(row => row.getAttribute("aria-label") === label || row.textContent === label);

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
    const launch = host.elements("a").find(row => row.textContent.includes("Open in JupyterLab")); assert.ok(launch);
    assert.equal(launch.getAttribute("href"), configured.url); assert.equal(launch.getAttribute("target"), "_blank"); assert.equal(launch.getAttribute("rel"), "noopener noreferrer");
    assert.match(host.container.textContent, /Use notebooks for code-driven analysis/);
    // Sources are imported from chat answers; Workspace has no separate source search.
    assert.equal(buttonAt(host, "Search sources"), undefined);
  } finally { try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = oldFetch; host.restore(); } }
});
