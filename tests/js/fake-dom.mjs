/* A minimal DOM for the view tests. No dependencies, no jsdom.
 *
 * `app/static/js/dom.js`'s `el()` is textContent-only and touches exactly five
 * globals — `document.createElement`, `document.createTextNode`, and the node
 * members `className` / `dataset` / `style` / `setAttribute` /
 * `addEventListener`. That is a small enough surface to stand up by hand, and
 * standing it up by hand is the point: the views under test are the real
 * modules, rendering through the real `el()`, so a test failure means the view
 * is wrong rather than that a stub drifted.
 *
 * WHAT THIS DELIBERATELY DOES NOT IMPLEMENT: `innerHTML` (nothing may use it —
 * see dom.js), `querySelector` on arbitrary selectors (only the two
 * attribute-scoped lookups the views actually perform), layout, and events'
 * bubbling. The tests therefore assert on the tree, not on pixels; pixels are
 * what the Chromium check in the ticket's verification step is for.
 */

/**
 * Depth-first walk in document order, ELEMENTS ONLY.
 *
 * Text nodes are excluded on purpose: a test that asserts "the first element
 * carrying role=x" must not have to remember that a text node has no `dataset`,
 * and every assertion in views.test.mjs reads `dataset` off what walk returns.
 */
export function walk(node, out = []) {
  if (node.nodeType !== 1) return out;
  out.push(node);
  for (const child of node.childNodes) walk(child, out);
  return out;
}

export function textOf(node) {
  if (node.nodeType === 3) return node.data;
  return node.childNodes.map(textOf).join('');
}

class FakeText {
  constructor(data) {
    this.nodeType = 3;
    this.data = String(data);
  }

  get textContent() {
    return this.data;
  }
}

class FakeNode {
  constructor(tagName, ownerDocument) {
    this.nodeType = 1;
    this.tagName = String(tagName).toUpperCase();
    this.ownerDocument = ownerDocument;
    this.childNodes = [];
    this.attributes = new Map();
    this.style = {};
    this.listeners = new Map();
    this.parentNode = null;
    this._className = '';
    /* `dataset` and `attributes` are ONE store in a browser: `node.dataset.role`
     * and `node.getAttribute('data-role')` are the same string, and
     * `[data-role="x"]` finds a node whose id was written through either. This
     * used to be a separate object, which is invisible until a test — or app
     * code — looks a node up by an attribute selector: `el()` writes `dataset`,
     * so every such lookup silently found nothing and the assertion passed for
     * the wrong reason. A proxy over the attribute map, with the browser's own
     * camelCase -> `kebab-case` rule, is both the faithful answer and the one
     * that makes `[data-role="x"]` work. */
    const attributeName = (key) => `data-${String(key).replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`;
    const self = this;
    this.dataset = new Proxy(
      {},
      {
        set(_target, key, value) {
          self.attributes.set(attributeName(key), String(value));
          return true;
        },
        get(_target, key) {
          if (typeof key !== 'string' || key.startsWith('_')) return undefined;
          const value = self.attributes.get(attributeName(key));
          return value === undefined ? undefined : value;
        },
        has(_target, key) {
          return typeof key === 'string' && self.attributes.has(attributeName(key));
        },
        deleteProperty(_target, key) {
          self.attributes.delete(attributeName(key));
          return true;
        },
        ownKeys() {
          return [...self.attributes.keys()]
            .filter((name) => name.startsWith('data-'))
            .map((name) => name.slice(5));
        },
        getOwnPropertyDescriptor(_target, key) {
          // Every own key this proxy reports is a real, enumerable, configurable
          // attribute — the invariants `Object.keys` and spread rely on, and the
          // VALUE has to be the real one or a test that enumerated a node's
          // dataset would read `undefined` for every key.
          if (typeof key !== 'string' || !self.attributes.has(attributeName(key))) return undefined;
          return {
            configurable: true,
            enumerable: true,
            writable: true,
            value: self.attributes.get(attributeName(key)),
          };
        },
      },
    );
  }

  get className() {
    return this._className;
  }

  set className(value) {
    this._className = String(value);
  }

  get classList() {
    const self = this;
    return {
      contains: (name) => self._className.split(/\s+/).includes(name),
      add: (name) => {
        if (!self.classList.contains(name)) self._className = `${self._className} ${name}`.trim();
      },
    };
  }

  get textContent() {
    return this.childNodes.map((child) => child.textContent).join('');
  }

  set textContent(value) {
    this.childNodes = [];
    if (value !== '' && value !== null && value !== undefined) {
      this.appendChild(this.ownerDocument.createTextNode(value));
    }
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    // A real document indexes ids, so `getElementById` finds a node whose id was
    // set at any point in its life. Without this, `getElementById` only ever
    // answered for the one id `installFakeDom` registered by hand — and a module
    // that looks its panel up that way (which is the normal way) would be
    // untestable rather than tested.
    if (name === 'id') this.ownerDocument.byId.set(String(value), this);
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  /** Present-valued and empty-valued attributes are both "present" here, which
   * is the distinction `hidden=""` turns on: a boolean attribute is written
   * without a value, and `getAttribute` cannot tell that from `hidden=""`
   * without the attribute table — which is what this is. */
  hasAttribute(name) {
    return this.attributes.has(name);
  }

  removeAttribute(name) {
    // Captured BEFORE the delete: the id is the value being removed, and asking
    // for it afterwards would answer null and leave the index pointing at a node
    // that is no longer in the tree.
    const previous = this.attributes.get(name);
    this.attributes.delete(name);
    // …and un-index it, or a removed id would still answer `getElementById`.
    if (name === 'id' && previous !== undefined) this.ownerDocument.byId.delete(previous);
  }

  appendChild(child) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  get firstChild() {
    return this.childNodes[0] || null;
  }

  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
    return this;
  }

  removeChild(child) {
    const at = this.childNodes.indexOf(child);
    if (at !== -1) this.childNodes.splice(at, 1);
    child.parentNode = null;
    return child;
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(handler);
  }

  removeEventListener(type, handler) {
    const set = this.listeners.get(type);
    if (set) set.delete(handler);
  }

  /** Fire a listener synchronously. No bubbling, no capture — see the header. */
  dispatchEvent(event) {
    const set = this.listeners.get(event.type);
    if (!set || set.size === 0) return true;
    event.target = this;
    for (const handler of [...set]) handler(event);
    return true;
  }

  /**
   * The five selector shapes the tests use, and no more: a tag name, a class, a
   * bare attribute, an attribute WITH its value, and `:not([attr])`. A stub that
   * silently answered an arbitrary selector would let a test pass against a shape
   * it never meant to assert, so an unknown one throws.
   *
   * The value form (`[data-tab="more"]`) is here because app code uses it:
   * `js/tabbar.js` looks its three controls up by `data-tab`, and the alternative
   * was a hand-written window stub for that one module — which is the drift this
   * file exists to prevent. It is a fixed shape, matched by one regex, and the
   * throw below still guards everything else.
   */
  querySelectorAll(selector) {
    const trimmed = selector.trim();
    if (trimmed.includes(',')) {
      return trimmed.split(',').flatMap((part) => this.querySelectorAll(part));
    }
    const valuedAttr = trimmed.match(/^(?:([a-z][a-z0-9-]*))?\[([a-z-]+)="([^"]*)"\]$/i);
    if (valuedAttr) {
      const [, tag, name, value] = valuedAttr;
      return walk(this).filter(
        (node) =>
          (tag === undefined || node.tagName === tag.toUpperCase()) &&
          node.getAttribute(name) === value,
      );
    }
    const tagAttr = trimmed.match(/^([a-z][a-z0-9-]*)\[([a-z-]+)\]$/i);

    if (tagAttr) {
      return walk(this).filter(
        (node) => node.tagName === tagAttr[1].toUpperCase() && node.getAttribute(tagAttr[2]) !== null,
      );
    }
    const tag = trimmed.match(/^([a-z][a-z0-9-]*)$/i);
    if (tag) return walk(this).filter((node) => node.tagName === tag[1].toUpperCase());
    const notAttr = trimmed.match(/^\.([a-z0-9-]+):not\(\[([a-z-]+)\]\)$/);
    if (notAttr) {
      return walk(this).filter(
        (node) => node.classList.contains(notAttr[1]) && node.getAttribute(notAttr[2]) === null,
      );
    }
    const attribute = trimmed.match(/^\[([a-z-]+)\]$/);
    if (attribute) return walk(this).filter((node) => node.getAttribute(attribute[1]) !== null);
    const prefixed = trimmed.match(/^\.([a-z0-9-]+)$/);
    if (prefixed) return walk(this).filter((node) => node.classList.contains(prefixed[1]));
    throw new Error(`fake-dom: unsupported selector "${selector}"`);
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

class FakeDocument {
  constructor() {
    this.nodeType = 9;
    this.byId = new Map();
    // A real document has a tree, and `main.js`'s `isModalOpen()` queries
    // `document` — not the app root — for an open modal. Without a root node to
    // walk, that guard would answer "nothing is open" for a picker that IS open
    // and the F18 §4e assertion would be vacuous.
    this.documentElement = new FakeNode('html', this);
    this.body = new FakeNode('body', this);
    this.documentElement.appendChild(this.body);
    // `document` is a real EventTarget in the browser, and
    // `initPullToRefresh` defaults `eventTarget` to it, so the fake has to be
    // one too or the gesture wiring cannot be exercised at all.
    this.listeners = new Map();
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(handler);
  }

  removeEventListener(type, handler) {
    const set = this.listeners.get(type);
    if (set) set.delete(handler);
  }

  dispatchEvent(event) {
    const set = this.listeners.get(event.type);
    if (set) for (const handler of [...set]) handler(event);
    return true;
  }

  createElement(tag) {
    return new FakeNode(tag, this);
  }

  createElementNS(_ns, tag) {
    return new FakeNode(tag, this);
  }

  createTextNode(data) {
    return new FakeText(data);
  }

  getElementById(id) {
    return this.byId.get(id) || null;
  }
}

/**
 * A `querySelector` that understands the three shapes main.js's guard uses,
 * including the comma-joined one it actually passes. A stub that only answered
 * the first selector would make the F18 §4e assertion vacuous.
 */
function documentQuerySelector(document, selector) {
  return document.body.querySelector(selector);
}

/**
 * Install the fake DOM on `globalThis` and return a `restore()`.
 *
 * `globalThis` plays the window: the views and `dom.js` read `window.scrollY`,
 * `window.location`, `globalThis.addEventListener` and `globalThis.localStorage`
 * without agreeing on a single object, and making them one object is what keeps
 * this harness under 200 lines.
 */
/**
 * `globalThis.navigator` is an accessor-only property in Node 22, so plain
 * assignment throws. Every global this harness swaps therefore goes through
 * `defineProperty`, and `restore` puts the original descriptor back rather than
 * a value — assigning `undefined` over a getter would leave the getter.
 */
function setGlobal(key, value) {
  Object.defineProperty(globalThis, key, {
    value,
    writable: true,
    configurable: true,
    enumerable: true,
  });
}

function restoreGlobal(key, descriptor) {
  if (descriptor === undefined) delete globalThis[key];
  else Object.defineProperty(globalThis, key, descriptor);
}

export function installFakeDom({ hash = '#/', search = '', online = true, historyLength = 2 } = {}) {
  const keys = [
    'document',
    'window',
    'navigator',
    'localStorage',
    'addEventListener',
    'removeEventListener',
    'dispatchEvent',
    'location',
    'history',
    'scrollY',
    'CustomEvent',
    'Event',
  ];
  const saved = Object.fromEntries(keys.map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));

  const document = new FakeDocument();
  document.querySelector = (selector) => documentQuerySelector(document, selector);
  document.querySelectorAll = (selector) => document.body.querySelectorAll(selector);

  const target = new FakeNode('div', document);
  target.setAttribute('id', 'app-root');
  document.body.appendChild(target);
  document.byId.set('app-root', target);

  const store = new Map();
  const localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
    clear: () => store.clear(),
  };

  // A freshly pushed entry has NULL state in a real browser — that is exactly
  // what distinguishes a forward navigation from a Back traversal, and a view
  // that reads its own restore target depends on the difference.
  const historyEntries = [{ state: null }];
  let historyIndex = 0;

  const location = {
    hash,
    search,
    href: `http://127.0.0.1:8007/${hash.replace(/^#/, '#')}${search}`,
    origin: 'http://127.0.0.1:8007',
  };

  const history = {
    get length() {
      return historyLength;
    },
    get state() {
      return historyEntries[historyIndex].state;
    },
    replaceState(state) {
      historyEntries[historyIndex].state = state;
    },
    pushState(state) {
      historyEntries.splice(historyIndex + 1);
      historyEntries.push({ state });
      historyIndex = historyEntries.length - 1;
    },
    back() {
      history.dispatchEvent({ type: 'popstate' });
    },
  };

  const nav = { onLine: online };
  const windowTarget = new FakeNode('window', document);
  windowTarget.document = document;
  windowTarget.history = history;
  windowTarget.location = location;
  windowTarget.navigator = nav;
  windowTarget.scrollY = 0;
  // The views reach for `window.setTimeout` / `window.clearTimeout` by property
  // rather than through the bare global, so they have to exist on the fake
  // window too. Node's own timers are the implementation.
  windowTarget.setTimeout = (fn, ms) => setTimeout(fn, ms);
  windowTarget.clearTimeout = (id) => clearTimeout(id);
  windowTarget.requestAnimationFrame = (fn) => setTimeout(fn, 0);
  windowTarget.cancelAnimationFrame = (id) => clearTimeout(id);
  // The fixture drives the 30 s F11 poll and the unstick watchdog without
  // waiting 30 s: `runTimers()` fires the recorded timeouts immediately.
  const scheduled = [];
  windowTarget.setTimeout = (fn, ms) => {
    const id = setTimeout(() => {
      const at = scheduled.findIndex((entry) => entry.id === id);
      if (at !== -1) scheduled.splice(at, 1);
      fn();
    }, ms);
    // The F11 poll is a 30 s timer and a test that fails before its `unmount()`
    // leaves it armed. `unref()` means a pending fixture timer can never hold
    // the runner's event loop open; nothing here depends on real elapsed time,
    // because `runTimers()` fires them synchronously.
    if (typeof id.unref === 'function') id.unref();
    scheduled.push({ id, fn, ms });
    return id;
  };
  windowTarget.clearTimeout = (id) => {
    const at = scheduled.findIndex((entry) => entry.id === id);
    if (at !== -1) scheduled.splice(at, 1);
    clearTimeout(id);
  };
  windowTarget.runTimers = () => {
    const due = scheduled.splice(0);
    for (const entry of due) {
      clearTimeout(entry.id);
      entry.fn();
    }
    return due.length;
  };
  windowTarget.pendingTimers = () => scheduled.length;

  setGlobal('document', document);
  setGlobal('window', windowTarget);
  setGlobal('navigator', nav);
  setGlobal('localStorage', localStorage);
  setGlobal('location', location);
  setGlobal('history', history);
  setGlobal('scrollY', 0);
  setGlobal('addEventListener', windowTarget.addEventListener.bind(windowTarget));
  setGlobal('removeEventListener', windowTarget.removeEventListener.bind(windowTarget));
  setGlobal('dispatchEvent', windowTarget.dispatchEvent.bind(windowTarget));
  setGlobal('CustomEvent', class CustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  });
  setGlobal('Event', class Event {
    constructor(type) {
      this.type = type;
    }
  });

  return {
    document,
    root: target,
    location,
    history,
    navigator: nav,
    localStorage,
    window: windowTarget,
    runTimers: (...args) => windowTarget.runTimers(...args),
    pendingTimers: () => windowTarget.pendingTimers(),
    setOnline(value) {
      nav.onLine = value;
      windowTarget.dispatchEvent({ type: value ? 'online' : 'offline' });
    },
    setHash(value) {
      location.hash = value;
    },
    /** Find every element carrying a data-* value, for the assertions. */
    findAll(predicate) {
      return walk(target).filter((node) => node.nodeType === 1 && predicate(node));
    },
    byData(role) {
      return this.findAll((node) => node.dataset.role === role);
    },
    restore() {
      for (const [key, descriptor] of Object.entries(saved)) restoreGlobal(key, descriptor);
    },
  };
}
