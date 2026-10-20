import assert from "node:assert/strict";

export function mountedHost() {
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
    constructor(name, owner, namespace = 'http://www.w3.org/1999/xhtml') { super(1, owner); this.tagName = name.toUpperCase(); this.nodeName = this.tagName; this.namespaceURI = namespace; this.attributes = new Map(); this.value = ''; this.style = { setProperty(name, value) { this[name] = value; }, removeProperty(name) { delete this[name]; } }; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); }
    setAttributeNS(_namespace, name, value) { this.setAttribute(name, value); }
    removeAttributeNS(_namespace, name) { this.removeAttribute(name); }
    hasAttribute(name) { return this.attributes.has(name); }
    get name() { return this.getAttribute('name') ?? ''; }
    set name(value) { this.setAttribute('name', value); }
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
  document.cookie = '_xsrf=generated-xsrf-cookie';
  const window = { document, HTMLElement: HostElement, HTMLIFrameElement: class {}, Node: HostNode, location: { href: 'http://127.0.0.1:53520/', origin: 'http://127.0.0.1:53520' } };
  document.defaultView = window;
  const old = new Map(['window', 'document', 'location', 'HTMLElement', 'Node', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries({ window, document, location: window.location, HTMLElement: HostElement, Node: HostNode, IS_REACT_ACT_ENVIRONMENT: true })) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  const container = document.createElement('div'); document.body.appendChild(container);
  return { container, restore() { for (const [key, descriptor] of old) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; } }, elements(name) { const result = []; const visit = node => { if (node.tagName === name.toUpperCase()) result.push(node); node.childNodes.forEach(visit); }; visit(container); return result; } };
}
