import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "./typescript-loader.mjs";

const moduleAt = async path => existsSync(new URL(path, import.meta.url)) ? import(path) : {};
const configuration = await moduleAt("../src/components/workspace-url.ts");
const workspace = await moduleAt("../src/components/jupyter-workspace.tsx");
const shell = await moduleAt("../src/components/workspace-shell.tsx");
const resolveURL = (value, origin = "http://127.0.0.1:53520") => {
  assert.equal(typeof configuration.resolveWorkspaceURL, "function", "Workspace URL validation is required");
  return configuration.resolveWorkspaceURL(value, origin);
};
const ownerA = { id: "11111111-1111-4111-8111-111111111111", username: "generated-a", displayName: "Generated A", roles: ["member"] };
const ownerB = { id: "22222222-2222-4222-8222-222222222222", username: "generated-b", displayName: "Generated B", roles: ["member"] };
const ownerC = { id: "33333333-3333-4333-8333-333333333333", username: "generated-c", displayName: "Generated C", roles: ["member"] };
const userWorkspaces = JSON.stringify({ [ownerA.id]: "http://127.0.0.1:58101/", [ownerB.id]: "http://127.0.0.1:58102/" });
const resolveOwner = (owner, value = userWorkspaces) => {
  assert.equal(typeof configuration.resolveUserWorkspaceURL, "function", "Owner-specific workspace selection is required");
  return configuration.resolveUserWorkspaceURL(value, owner, "http://127.0.0.1:53520");
};

test("workspace selects only the exact authenticated owner entry", () => {
  assert.deepEqual(resolveOwner(ownerA.id), { status: "configured", url: "http://127.0.0.1:58101/lab" });
  assert.deepEqual(resolveOwner(ownerB.id), { status: "configured", url: "http://127.0.0.1:58102/lab" });
  for (const owner of [ownerC.id, "", ` ${ownerA.id}`, "toString"]) {
    assert.deepEqual(resolveOwner(owner), { status: "missing" });
  }
  assert.deepEqual(configuration.resolveUserWorkspaceURL(undefined, ownerA.id, "http://127.0.0.1:53520"), { status: "missing" });
  assert.deepEqual(resolveOwner(ownerA.id, ""), { status: "missing" });
});

test("workspace rejects malformed maps and selected entries without exposing them", () => {
  for (const value of ["{", "null", "[]", '"generated"', "42", JSON.stringify({ [ownerA.id]: null }), JSON.stringify({ [ownerA.id]: 58101 })]) {
    assert.deepEqual(resolveOwner(ownerA.id, value), { status: "invalid" });
  }
  for (const value of ["http://user:secret@localhost:58101", "http://localhost:58101/?token=secret", "http://localhost:58101/#secret", "http://example.test:58101", "http://127.0.0.1:53520", "https://localhost:58101", "http://localhost:58101/lab"]) {
    const entries = JSON.stringify({ [ownerA.id]: value, [ownerB.id]: "http://127.0.0.1:58102/" });
    const result = resolveOwner(ownerA.id, entries);
    assert.deepEqual(result, { status: "invalid" });
    assert.deepEqual(resolveOwner(ownerB.id, entries), { status: "configured", url: "http://127.0.0.1:58102/lab" });
    const html = renderToStaticMarkup(React.createElement(workspace.JupyterWorkspace, { configuration: result }));
    assert.doesNotMatch(html, /<iframe|secret|example\.test/);
  }
});

test("authenticated app routes two owners and leaves missing or malformed mappings unconnected", async () => {
  const appURL = new URL("../src/App.tsx", import.meta.url);
  const host = mountedHost();
  const originalFetch = globalThis.fetch;
  const { createRoot } = await import("react-dom/client");
  const root = createRoot(host.container);
  let currentOwner;
  globalThis.fetch = async (url, options) => {
    if (url === "/api/me") return new Response(JSON.stringify(currentOwner), { headers: { "content-type": "application/json" } });
    if (url.includes("/api/contents/?content=1")) return new Response(JSON.stringify({ type: "directory", path: "", content: [] }), { headers: { "content-type": "application/json" } });
    assert.equal(url, "/api/conversations");
    assert.equal(options.method, "POST");
    return new Response(null, { status: 503 });
  };
  try {
    for (const [index, entries] of [userWorkspaces, "{"].entries()) {
      const moduleURL = `${appURL.href}?workspace-owner-check=${index}`;
      const hooks = registerHooks({ load(url, context, next) {
        const loaded = next(url, context);
        return url === moduleURL ? { ...loaded, source: `import.meta.env = ${JSON.stringify({ VITE_USER_WORKSPACES: entries, VITE_WORKSPACE_URL: "http://127.0.0.1:58103/" })};\n${loaded.source}` } : loaded;
      } });
      let App;
      try { ({ default: App } = await import(moduleURL)); } finally { hooks.deregister(); }
      for (const [owner, expected] of [[ownerA, "http://127.0.0.1:58101/lab"], [ownerB, "http://127.0.0.1:58102/lab"], [ownerC, undefined]]) {
        currentOwner = owner;
        await React.act(async () => root.render(React.createElement(App, { key: `${index}:${owner.id}` })));
        assert.equal(host.elements("a").some(element => element.textContent.includes("Open JupyterLab")), false, "The initial Chat view does not mount an owner workspace");
        const open = host.elements("button").find(element => element.textContent === "Workspace");
        assert.ok(open);
        await React.act(async () => open.dispatchEvent({ type: "click", button: 0, bubbles: true }));
        const launches = host.elements("a").filter(element => element.textContent.includes("Open JupyterLab"));
        assert.equal(host.elements("iframe").length, 0, "JupyterLab is launch-only");
        if (index === 0 && expected) {
          assert.equal(launches.length, 1);
          assert.equal(launches[0].getAttribute("href"), expected);
          assert.equal(launches[0].getAttribute("target"), "_blank");
        } else {
          assert.equal(launches.length, 0);
          assert.match(host.container.textContent, /Workspace service is not connected/);
        }
      }
    }
  } finally {
    try { await React.act(async () => root.unmount()); } finally { globalThis.fetch = originalFetch; host.restore(); }
  }
});

test("workspace accepts a separate loopback HTTP server root and opens standard JupyterLab", () => {
  assert.deepEqual(resolveURL("http://127.0.0.1:53522/"), { status: "configured", url: "http://127.0.0.1:53522/lab" });
  assert.deepEqual(resolveURL("http://localhost:53522"), { status: "configured", url: "http://localhost:53522/lab" });
  assert.deepEqual(resolveURL("http://[::1]:53522"), { status: "configured", url: "http://[::1]:53522/lab" });
});

test("workspace refuses secrets, nonlocal services and the CRAFT origin without echoing values", () => {
  for (const value of ["http://127.0.0.1:53520", "https://localhost:53522", "http://example.test:53522", "http://localhost.example.test:53522", "http://user:secret@localhost:53522", "http://localhost:53522/?token=secret", "http://localhost:53522/#secret", "http://localhost:53522/lab", "not a url"]) {
    const result = resolveURL(value);
    assert.equal(result.status, "invalid");
    assert.equal("url" in result, false);
    assert.doesNotMatch(JSON.stringify(result), /secret|example\.test/);
  }
  assert.deepEqual(resolveURL(undefined), { status: "missing" });
  assert.deepEqual(resolveURL("  "), { status: "missing" });
});

test("missing workspace renders an honest service state with no frame or run substitute", () => {
  assert.equal(typeof workspace.JupyterWorkspace, "function");
  const html = renderToStaticMarkup(React.createElement(workspace.JupyterWorkspace, { configuration: { status: "missing" } }));
  assert.match(html, /Workspace service is not connected/);
  assert.match(html, /personal files and Markdown skills/);
  assert.doesNotMatch(html, /<iframe|>Run</);
});

test("configured workspace launches JupyterLab separately with honest sign-in guidance", () => {
  assert.equal(typeof workspace.JupyterWorkspace, "function");
  const html = renderToStaticMarkup(React.createElement(workspace.JupyterWorkspace, { configuration: { status: "configured", url: "http://127.0.0.1:53522/lab" } }));
  assert.doesNotMatch(html, /<iframe/);
  assert.match(html, /href="http:\/\/127\.0\.0\.1:53522\/lab"[^>]+target="_blank"[^>]+rel="noopener noreferrer"/);
  assert.match(html, /sign in/i);
  assert.doesNotMatch(html, /Workspace connected/);
});

test("navigation exposes only Chat and Workspace, with Files and Skills inside Workspace", async () => {
  const host = mountedHost(); const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container);
  const click = async label => { const button = host.elements("button").find(element => element.textContent === label); assert.ok(button); await React.act(async () => button.dispatchEvent({ type: "click", button: 0, bubbles: true })); };
  try {
    await React.act(async () => root.render(React.createElement(shell.WorkspaceShell, {
      chat: React.createElement("p", null, "Current conversation"), files: React.createElement("p", null, "Owned files"), skills: React.createElement("p", null, "Personal skills"),
    })));
    const primary = host.elements("nav").find(element => element.getAttribute("aria-label") === "Primary navigation");
    assert.deepEqual(host.elements("button").filter(element => primary.contains(element)).map(element => element.textContent), ["Chat", "Workspace"]);
    assert.equal(host.elements("nav").some(element => element.getAttribute("aria-label") === "Workspace layout"), false);
    assert.doesNotMatch(primary.textContent, /Skills|Tools|Notes|Drafts|Side by side/);
    assert.equal(host.elements("p").some(element => element.textContent === "Owned files"), false);
    await click("Workspace");
    const sections = host.elements("nav").find(element => element.getAttribute("aria-label") === "Workspace sections");
    assert.equal(visible(sections), true);
    assert.deepEqual(host.elements("button").filter(element => sections.contains(element)).map(element => element.textContent), ["Files", "Skills"]);
    assert.equal(visible(host.elements("p").find(element => element.textContent === "Owned files")), true);
    await click("Skills");
    const skill = host.elements("p").find(element => element.textContent === "Personal skills"); assert.equal(visible(skill), true);
    await click("Chat"); assert.equal(visible(skill), false);
    await click("Workspace"); assert.equal(visible(skill), true, "Workspace restores the selected subsection");
  } finally { try { await React.act(async () => root.unmount()); } finally { host.restore(); } }
});

test("first Workspace visit mounts the launch panel once and later navigation retains it", async () => {
  const host = mountedHost(); const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container);
  let mounts = 0, releases = 0, mountedVisible = false;
  function Files() { React.useLayoutEffect(() => { mounts++; mountedVisible = visible(host.elements("a")[0]); return () => { releases++; }; }, []); return React.createElement("a", { href: "http://127.0.0.1:53522/lab", target: "_blank", rel: "noopener noreferrer" }, "Open JupyterLab"); }
  const click = async label => { const button = host.elements("button").find(element => element.textContent === label); assert.ok(button); await React.act(async () => button.dispatchEvent({ type: "click", button: 0, bubbles: true })); };
  try {
    await React.act(async () => root.render(React.createElement(shell.WorkspaceShell, { chat: React.createElement("p", null, "Current conversation"), files: React.createElement(Files), skills: React.createElement("p", null, "Personal skills") })));
    assert.equal(host.elements("a").length, 0); assert.equal(mounts, 0);
    await click("Workspace"); const launch = host.elements("a")[0];
    assert.ok(launch); assert.equal(mounts, 1); assert.equal(mountedVisible, true); assert.equal(visible(launch), true);
    for (const label of ["Skills", "Chat", "Workspace", "Files", "Chat", "Workspace"]) { await click(label); assert.equal(host.elements("a")[0], launch); assert.equal(mounts, 1); assert.equal(releases, 0); }
  } finally { try { await React.act(async () => root.unmount()); } finally { host.restore(); } }
  assert.equal(releases, 1);
});

test("Chat and Workspace retain the active stream, composer text and unsaved skill document", async () => {
  const host = mountedHost(); const { createRoot } = await import("react-dom/client"); const root = createRoot(host.container);
  let mounts = 0, releases = 0, skillMounts = 0, skillReleases = 0, deliver;
  function LiveChat() { const [text, setText] = React.useState("Unsent message"); const [stream, setStream] = React.useState("Stream started"); React.useEffect(() => { mounts++; deliver = setStream; return () => { releases++; }; }, []); return React.createElement(React.Fragment, null, React.createElement("button", { onClick: () => setText("Retained message") }, text), React.createElement("output", null, stream)); }
  function SkillDocument() { const [text, setText] = React.useState("Unsaved instructions"); React.useEffect(() => { skillMounts++; return () => { skillReleases++; }; }, []); return React.createElement(React.Fragment, null, React.createElement("button", { onClick: () => setText("Changed instructions") }, "Edit generated skill"), React.createElement("textarea", { value: text, readOnly: true })); }
  const click = async label => { const button = host.elements("button").find(element => element.textContent === label); assert.ok(button); await React.act(async () => button.dispatchEvent({ type: "click", button: 0, bubbles: true })); };
  try {
    await React.act(async () => root.render(React.createElement(shell.WorkspaceShell, { chat: React.createElement(LiveChat), files: React.createElement("a", { href: "http://127.0.0.1:53522/lab", target: "_blank", rel: "noopener noreferrer" }, "Open JupyterLab"), skills: React.createElement(SkillDocument) })));
    await click("Unsent message"); const chat = host.elements("button").find(element => element.textContent === "Retained message"); const stream = host.elements("output")[0];
    await click("Workspace"); const launch = host.elements("a")[0];
    await click("Skills"); const skill = host.elements("textarea")[0]; await click("Edit generated skill");
    await React.act(async () => deliver("Stream continued while hidden"));
    for (const label of ["Chat", "Workspace", "Files", "Skills", "Chat", "Workspace"]) await click(label);
    assert.equal(host.elements("button").find(element => element.textContent === "Retained message"), chat); assert.equal(host.elements("output")[0], stream); assert.equal(stream.textContent, "Stream continued while hidden");
    assert.equal(host.elements("a")[0], launch); assert.equal(host.elements("textarea")[0], skill); assert.equal(skill.value, "Changed instructions");
    assert.equal(mounts, 1); assert.equal(skillMounts, 1); assert.equal(releases, 0); assert.equal(skillReleases, 0);
  } finally { try { await React.act(async () => root.unmount()); } finally { host.restore(); } }
  assert.equal(releases, 1); assert.equal(skillReleases, 1);
});

function visible(element) {
  if (!element) return false;
  for (let node = element; node; node = node.parentNode) if (node.hasAttribute?.("hidden")) return false;
  return true;
}

// Minimal test-only DOM, adapted from the existing Markdown lifecycle smoke check.
function mountedHost() {
  class HostNode {
    constructor(type, owner) { this.nodeType = type; this.ownerDocument = owner; this.parentNode = null; this.childNodes = []; this.listeners = new Map(); }
    appendChild(node) { if (node.parentNode) node.parentNode.removeChild(node); node.parentNode = this; this.childNodes.push(node); return node; }
    insertBefore(node, before) { if (!before) return this.appendChild(node); if (node.parentNode) node.parentNode.removeChild(node); const index = this.childNodes.indexOf(before); assert.ok(index >= 0); this.childNodes.splice(index, 0, node); node.parentNode = this; return node; }
    removeChild(node) { const index = this.childNodes.indexOf(node); assert.ok(index >= 0); this.childNodes.splice(index, 1); node.parentNode = null; return node; }
    get firstChild() { return this.childNodes[0] ?? null; }
    get lastChild() { return this.childNodes.at(-1) ?? null; }
    get nextSibling() { return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this) + 1] ?? null; }
    get previousSibling() { return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this) - 1] ?? null; }
    get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map(node => node.textContent).join(''); }
    set textContent(value) { this.childNodes.forEach(node => { node.parentNode = null; }); this.childNodes = []; if (value) this.appendChild(this.ownerDocument.createTextNode(String(value))); }
    contains(node) { return node === this || this.childNodes.some(child => child.contains(node)); }
    getRootNode() { let node = this; while (node.parentNode) node = node.parentNode; return node; }
    addEventListener(type, callback, options) { const listeners = this.listeners.get(type) ?? []; listeners.push({ callback, capture: options === true || options?.capture === true }); this.listeners.set(type, listeners); }
    removeEventListener(type, callback) { this.listeners.set(type, (this.listeners.get(type) ?? []).filter(value => value.callback !== callback)); }
    dispatchEvent(event) {
      event.target = this; event.preventDefault ??= () => { event.defaultPrevented = true; }; event.stopPropagation ??= () => { event.cancelBubble = true; };
      const path = []; for (let node = this; node; node = node.parentNode) path.push(node);
      for (const capture of [true, false]) for (const node of capture ? [...path].reverse() : path) {
        for (const entry of node.listeners.get(event.type) ?? []) if (entry.capture === capture) { event.currentTarget = node; entry.callback(event); }
        if (event.cancelBubble) break;
      }
      return !event.defaultPrevented;
    }
  }
  class HostElement extends HostNode {
    constructor(name, owner, namespace = 'http://www.w3.org/1999/xhtml') { super(1, owner); this.tagName = name.toUpperCase(); this.nodeName = this.tagName; this.namespaceURI = namespace; this.attributes = new Map(); this.style = { setProperty(name, value) { this[name] = value; }, removeProperty(name) { delete this[name]; } }; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); }
    setAttributeNS(_namespace, name, value) { this.setAttribute(name, value); }
    removeAttributeNS(_namespace, name) { this.removeAttribute(name); }
    hasAttribute(name) { return this.attributes.has(name); }
    get options() { return this.childNodes.filter(node => node.tagName === "OPTION"); }
  }
  class HostDocument extends HostNode {
    constructor() { super(9, null); this.ownerDocument = this; this.nodeName = '#document'; this.documentElement = new HostElement('html', this); this.head = new HostElement('head', this); this.body = new HostElement('body', this); this.appendChild(this.documentElement); this.documentElement.appendChild(this.head); this.documentElement.appendChild(this.body); this.activeElement = this.body; }
    createElement(name) { return new HostElement(name, this); }
    createElementNS(namespace, name) { return new HostElement(name, this, namespace); }
    createTextNode(value) { const node = new HostNode(3, this); node.nodeName = '#text'; node.nodeValue = String(value); return node; }
    createComment(value) { const node = new HostNode(8, this); node.nodeName = '#comment'; node.nodeValue = value; return node; }
  }
  const document = new HostDocument();
  const window = { document, HTMLElement: HostElement, HTMLIFrameElement: class {}, Node: HostNode, location: { href: 'http://127.0.0.1:53520/', origin: 'http://127.0.0.1:53520' } };
  document.defaultView = window;
  const old = new Map(['window', 'document', 'HTMLElement', 'Node', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries({ window, document, HTMLElement: HostElement, Node: HostNode, IS_REACT_ACT_ENVIRONMENT: true })) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  const container = document.createElement('div'); document.body.appendChild(container);
  return { container, restore() { for (const [key, descriptor] of old) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; } }, elements(name) { const result = []; const visit = node => { if (node.tagName === name.toUpperCase()) result.push(node); node.childNodes.forEach(visit); }; visit(container); return result; } };
}
