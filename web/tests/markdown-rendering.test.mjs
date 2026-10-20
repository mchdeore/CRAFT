import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import "./typescript-loader.mjs";
import {
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
const { MarkdownText } = await import("../src/components/assistant-ui/elements/markdown-text.tsx");

const markdown = "# Generated heading\n\nGenerated paragraph with **bold** and \u0060<script>\u0060.\n\n| left | right |\n| --- | --- |\n| one | two |\n\n<script>generated()</script>";
const messages = [{
  id: "generated-markdown",
  role: "assistant",
  createdAt: new Date("2026-10-19T00:00:00Z"),
  status: { type: "complete", reason: "stop" },
  metadata: { custom: {} },
  content: [{ type: "text", text: markdown }],
}];
const RendererContext = React.createContext(undefined);
function TextPart() {
  return React.createElement(MarkdownText, { components: React.useContext(RendererContext) });
}
function AssistantMessage() {
  return React.createElement(MessagePrimitive.Parts, { components: { Text: TextPart } });
}
function Harness({ components }) {
  const runtime = useExternalStoreRuntime({ messages, isRunning: false, onNew: async () => {} });
  return React.createElement(
    AssistantRuntimeProvider,
    { runtime },
    React.createElement(RendererContext.Provider, { value: components },
      React.createElement(ThreadPrimitive.Messages, { components: { AssistantMessage, UserMessage: () => null } })),
  );
}
const view = components => React.createElement(React.StrictMode, null,
  React.createElement(React.Suspense, { fallback: "Waiting for renderer" },
    React.createElement(Harness, { components })));
const tick = () => new Promise(resolve => setImmediate(resolve));
async function mount() {
  const host = mountedHost();
  host.container.ownerDocument.defaultView.matchMedia = () => ({
    matches: true, addEventListener() {}, removeEventListener() {},
  });
  const { createRoot } = await import("react-dom/client");
  const root = createRoot(host.container);
  return {
    host, root,
    async render(components) {
      await React.act(async () => { root.render(view(components)); await tick(); });
    },
    async dispose() {
      try { await React.act(async () => { root.unmount(); }); }
      finally { host.restore(); }
    },
  };
}

test("shallow-equivalent Markdown renderer maps retain the mounted paragraph", async () => {
  function Paragraph({ children }) {
    const [marker] = React.useState("retained");
    return React.createElement("p", { "data-renderer": marker }, children);
  }
  const mounted = await mount();
  try {
    await mounted.render({ p: Paragraph });
    const paragraph = mounted.host.elements("p")[0];
    assert.equal(paragraph.getAttribute("data-renderer"), "retained");
    await mounted.render({ p: Paragraph });
    assert.equal(mounted.host.elements("p")[0], paragraph, "equivalent overrides must preserve mounted renderer state");
    assert.equal(mounted.host.elements("table").length, 1, "GFM tables must continue to render");
    assert.equal(mounted.host.elements("strong")[0].textContent, "bold");
    assert.equal(mounted.host.elements("code")[0].textContent, "<script>");
    assert.equal(mounted.host.elements("script").length, 0, "raw Markdown HTML must remain inactive");
  } finally { await mounted.dispose(); }
});

test("Markdown renderer replacements and removals take effect on the next render", async () => {
  function First({ children }) { return React.createElement("p", { "data-renderer": "first" }, children); }
  function Second({ children }) { return React.createElement("p", { "data-renderer": "second" }, children); }
  const mounted = await mount();
  try {
    await mounted.render({ p: First });
    assert.equal(mounted.host.elements("p")[0].getAttribute("data-renderer"), "first");
    await mounted.render({ p: Second });
    assert.equal(mounted.host.elements("p")[0].getAttribute("data-renderer"), "second");
    await mounted.render({});
    assert.equal(mounted.host.elements("p")[0].getAttribute("data-renderer"), null);
    assert.match(mounted.host.elements("p")[0].getAttribute("class"), /aui-md-p/);
    await mounted.render(undefined);
    assert.match(mounted.host.elements("p")[0].getAttribute("class"), /aui-md-p/);
  } finally { await mounted.dispose(); }
});

test("a suspended Markdown renderer change cannot remount the retained paragraph", async () => {
  function Paragraph({ children }) {
    const [marker] = React.useState("retained");
    return React.createElement("p", { "data-renderer": marker }, children);
  }
  let attempted = 0;
  const pending = new Promise(() => {});
  function PendingParagraph() { attempted++; throw pending; }
  const mounted = await mount();
  try {
    await mounted.render({ p: Paragraph });
    const paragraph = mounted.host.elements("p")[0];
    await React.act(async () => {
      React.startTransition(() => { mounted.root.render(view({ p: PendingParagraph })); });
      await tick();
    });
    assert.ok(attempted > 0, "the alternate renderer must actually suspend");
    assert.equal(mounted.host.elements("p")[0], paragraph, "the transition must retain the visible paragraph");
    await mounted.render({ p: Paragraph });
    assert.equal(mounted.host.elements("p")[0], paragraph, "abandoned renderer work must not reset retained paragraph state");
  } finally { await mounted.dispose(); }
});

// Test-only DOM surface; the installed React and Markdown renderers remain real.
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
  }
  class HostDocument extends HostNode {
    constructor() { super(9, null); this.ownerDocument = this; this.nodeName = '#document'; this.documentElement = new HostElement('html', this); this.head = new HostElement('head', this); this.body = new HostElement('body', this); this.appendChild(this.documentElement); this.documentElement.appendChild(this.head); this.documentElement.appendChild(this.body); this.activeElement = this.body; }
    createElement(name) { return new HostElement(name, this); }
    createElementNS(namespace, name) { return new HostElement(name, this, namespace); }
    createTextNode(value) { const node = new HostNode(3, this); node.nodeName = '#text'; node.nodeValue = String(value); return node; }
    createComment(value) { const node = new HostNode(8, this); node.nodeName = '#comment'; node.nodeValue = value; return node; }
  }
  const document = new HostDocument();
  const window = { document, HTMLElement: HostElement, HTMLIFrameElement: class {}, Node: HostNode, location: { href: 'http://127.0.0.1/' } };
  document.defaultView = window;
  const old = new Map(['window', 'document', 'HTMLElement', 'Node', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries({ window, document, HTMLElement: HostElement, Node: HostNode, IS_REACT_ACT_ENVIRONMENT: true })) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  const container = document.createElement('div'); document.body.appendChild(container);
  return { container, restore() { for (const [key, descriptor] of old) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; } }, elements(name) { const result = []; const visit = node => { if (node.tagName === name.toUpperCase()) result.push(node); node.childNodes.forEach(visit); }; visit(container); return result; } };
}
