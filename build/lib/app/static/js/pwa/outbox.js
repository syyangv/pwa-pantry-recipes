/*
 * Browser-free offline-write outbox core.
 *
 * The core stores domain intents, not HTTP requests.  Storage, clock, and
 * replay semantics are injected so this file can be unit-tested without a
 * DOM, fetch, or a service-worker runtime.  The browser adapter added later
 * may use the same createOutbox() function with localStorage and its own
 * replay(action) implementation.
 */

export const OUTBOX_STORAGE_KEY = "pwa-outbox";
export const OUTBOX_SYNC_TAG = "outbox";

let generatedId = 0;

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isConflict(error) {
  return Boolean(
    error && (
      error.status === 409 ||
      error.statusCode === 409 ||
      error.code === "CONFLICT" ||
      error.code === "conflict" ||
      error.name === "ConflictError"
    )
  );
}

export function isNetworkFailure(error) {
  if (!error) return false;
  if (error.network === true || error.isNetworkError === true) return true;
  if (error.code === "NETWORK_ERROR" || error.code === "ERR_NETWORK") return true;
  // Browser fetch rejects with TypeError for a transport failure.  A typed
  // error with an HTTP status is a server response, not a network failure.
  return (error instanceof TypeError || error.name === "TypeError") &&
    error.status === undefined && error.statusCode === undefined;
}

function serializeParkReason(error) {
  if (!error) return null;
  const reason = {};
  if (error.status !== undefined) reason.status = error.status;
  if (error.code !== undefined) reason.code = String(error.code);
  if (error.message !== undefined) reason.message = String(error.message);
  return Object.keys(reason).length ? reason : null;
}

function readQueue(storage) {
  let raw;
  try {
    raw = storage.getItem(OUTBOX_STORAGE_KEY);
  } catch (_) {
    return [];
  }
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    // Accept the direct array shape written by this module.  The object shape
    // makes reloads tolerant of a future versioned envelope as well.
    const queue = Array.isArray(parsed) ? parsed : parsed && parsed.intents;
    return Array.isArray(queue) ? queue.filter(isRecord) : [];
  } catch (_) {
    // A corrupt local entry must not prevent the PWA from booting.  A later
    // enqueue/flush persists the valid queue shape over it.
    return [];
  }
}

function makeClientId(createdAt, existing, idFactory) {
  const occupied = new Set(existing.map((intent) => intent.clientId));
  let clientId;
  do {
    generatedId += 1;
    const generated = typeof idFactory === "function"
      ? idFactory()
      : (typeof globalThis !== "undefined" && globalThis.crypto &&
          typeof globalThis.crypto.randomUUID === "function")
        ? globalThis.crypto.randomUUID()
        : `outbox-${String(createdAt)}-${generatedId}-${Math.random().toString(36).slice(2)}`;
    clientId = generated === undefined || generated === null ? "" : String(generated);
    if (occupied.has(clientId)) clientId = `${clientId}-${generatedId}`;
  } while (!clientId || occupied.has(clientId));
  return clientId;
}

/**
 * Create a pure outbox core.
 *
 * @param {{storage: StorageLike, now: () => unknown, replay: (intent: object) => Promise<unknown>|unknown}} options
 */
export function createOutbox({ storage, now = () => new Date().toISOString(), replay, clientId: clientIdFactory }) {
  if (!storage || typeof storage.getItem !== "function" || typeof storage.setItem !== "function") {
    throw new TypeError("createOutbox requires localStorage-like storage");
  }
  if (typeof replay !== "function") {
    throw new TypeError("createOutbox requires replay(intent)");
  }
  if (typeof now !== "function") {
    throw new TypeError("createOutbox requires now()");
  }

  const queue = readQueue(storage);
  let flushPromise = null;

  function persist(nextQueue = queue) {
    storage.setItem(OUTBOX_STORAGE_KEY, JSON.stringify(nextQueue));
  }

  function commit(nextQueue) {
    // Persist first.  If localStorage is full/blocked, the in-memory queue
    // must not advance beyond what a reload can recover.
    persist(nextQueue);
    queue.splice(0, queue.length, ...nextQueue);
  }

  function createIntent(action) {
    if (!isRecord(action) || typeof action.type !== "string" || action.type.length === 0) {
      throw new TypeError("enqueue requires an intent with a non-empty type");
    }
    const createdAt = action.createdAt === undefined ? now() : action.createdAt;
    const requestedId = typeof action.clientId === "string" && action.clientId.length
      ? action.clientId
      : null;
    const clientId = requestedId && !queue.some((intent) => intent.clientId === requestedId)
      ? requestedId
      : makeClientId(createdAt, queue, clientIdFactory);
    return {
      type: action.type,
      payload: action.payload,
      clientId,
      createdAt,
    };
  }

  function enqueue(action) {
    const intent = createIntent(action);
    commit([...queue, intent]);
    return intent;
  }

  function retry(clientId) {
    const intent = queue.find((candidate) => candidate.clientId === clientId);
    if (!intent || intent.state !== "parked") return null;
    const next = { ...intent };
    delete next.state;
    delete next.parkReason;
    delete next.conflict;
    commit(queue.map((candidate) => candidate === intent ? next : candidate));
    return next;
  }

  function abandon(clientId) {
    const intent = queue.find((candidate) => candidate.clientId === clientId);
    if (!intent) return null;
    commit(queue.filter((candidate) => candidate !== intent));
    return intent;
  }

  async function runFlush() {
    const result = { synced: 0, parked: 0, stalled: false };
    for (let index = 0; index < queue.length;) {
      const intent = queue[index];
      // A parked intent remains pending for UI/retry resolution but is not
      // replayed on every focus/interval trigger (which would tight-loop).
      if (intent.state === "parked") {
        index += 1;
        continue;
      }

      try {
        await replay(intent);
      } catch (error) {
        if (isNetworkFailure(error)) {
          // Keep the current intent at the head of the outbox and stop.  A
          // later connectivity trigger can safely retry it in FIFO order.
          result.stalled = true;
          break;
        }

        // HTTP 409 and other explicit replay rejections are parked.  They
        // remain durable pending intents for the app to resolve, while later
        // independent intents can still flush in this pass.
        const reason = serializeParkReason(error);
        const parked = {
          ...intent,
          state: "parked",
          ...(reason ? { parkReason: reason } : {}),
          ...(isConflict(error) ? { conflict: true } : {}),
        };
        commit(queue.map((candidate) => candidate === intent ? parked : candidate));
        result.parked += 1;
        index += 1;
        continue;
      }

      // Remove by identity, not by array position: an app may enqueue a new
      // intent from a replay callback, and that must not be dropped.
      const currentIndex = queue.findIndex((candidate) => candidate === intent);
      if (currentIndex !== -1) commit(queue.filter((_, candidateIndex) => candidateIndex !== currentIndex));
      result.synced += 1;
      // Do not increment index after removing the current entry.
    }
    return result;
  }

  function flush() {
    if (!flushPromise) {
      flushPromise = runFlush().finally(() => {
        flushPromise = null;
      });
    }
    return flushPromise;
  }

  const outbox = {
    createIntent,
    enqueue,
    retry,
    abandon,
    flush,
    /** A snapshot avoids exposing mutable internal queue state. */
    get pending() {
      return queue.map((intent) => ({ ...intent }));
    },
    get pendingCount() {
      return queue.length;
    },
    get hasPending() {
      return queue.length > 0;
    },
  };

  return outbox;
}

function makeStatusEvent(type, detail) {
  if (typeof CustomEvent === "function") return new CustomEvent(type, { detail });
  const event = new Event(type);
  Object.defineProperty(event, "detail", { value: detail, enumerable: true });
  return event;
}

/**
 * Add the browser lifecycle/write-through layer around the pure core.
 *
 * `eventTarget`, `document`, `navigator`, `timers`, and `events` are
 * injectable to keep lifecycle behavior testable without a DOM.  In a page,
 * the defaults are the window, document, navigator, and localStorage.
 */
export function createBrowserOutbox(options = {}) {
  const root = typeof globalThis === "undefined" ? {} : globalThis;
  const storage = options.storage === undefined ? root.localStorage : options.storage;
  const eventTarget = options.eventTarget === undefined ? root : options.eventTarget;
  const documentTarget = options.document === undefined ? root.document : options.document;
  const navigatorTarget = options.navigator === undefined ? root.navigator : options.navigator;
  const serviceWorkerTarget = options.serviceWorker === undefined
    ? navigatorTarget && navigatorTarget.serviceWorker
    : options.serviceWorker;
  const timers = options.timers === undefined ? root : options.timers;
  const intervalMs = options.intervalMs === undefined ? 30000 : options.intervalMs;
  const replay = options.replay;
  const core = createOutbox({
    storage,
    now: options.now,
    replay,
    clientId: options.clientId,
  });
  const events = options.events || new EventTarget();
  const subscriptions = [];
  let intervalId = null;
  let closed = false;
  let operation = Promise.resolve();

  function serialize(task) {
    const next = operation.then(task, task);
    // A failed operation must not poison future lifecycle/write operations.
    operation = next.catch(() => {});
    return next;
  }

  function dispatch(type, detail) {
    events.dispatchEvent(makeStatusEvent(type, detail));
  }

  function requestBackgroundSync() {
    const registration = serviceWorkerTarget && serviceWorkerTarget.ready;
    if (!registration || typeof registration.then !== "function") return;
    void registration
      .then((readyRegistration) => {
        const syncManager = readyRegistration && readyRegistration.sync;
        if (!syncManager || typeof syncManager.register !== "function") return;
        return Promise.resolve(syncManager.register(OUTBOX_SYNC_TAG)).catch(() => {});
      })
      .catch(() => {});
  }

  function addListener(target, type, handler) {
    if (!target || typeof target.addEventListener !== "function" ||
        typeof target.removeEventListener !== "function") return;
    target.addEventListener(type, handler);
    subscriptions.push(() => target.removeEventListener(type, handler));
  }

  function triggerFlush() {
    if (closed) return Promise.resolve({ synced: 0, parked: 0, stalled: false });
    return flush().catch(() => ({ synced: 0, parked: 0, stalled: true }));
  }

  async function performFlush() {
    if (closed) return { synced: 0, parked: 0, stalled: false };
    try {
      const result = await core.flush();
      if (result.synced > 0) {
        dispatch("synced", {
          synced: result.synced,
          count: result.synced,
          pendingCount: core.pendingCount,
          pending: core.pending,
        });
      }
      if (result.stalled || result.parked > 0) {
        dispatch("stalled", {
          stalled: result.stalled,
          parked: result.parked,
          pendingCount: core.pendingCount,
          pending: core.pending,
        });
      }
      return result;
    } catch (error) {
      dispatch("stalled", {
        stalled: true,
        error,
        pendingCount: core.pendingCount,
        pending: core.pending,
      });
      throw error;
    }
  }

  function flush() {
    return serialize(performFlush);
  }

  async function performWrite(action) {
    if (closed) throw new Error("outbox is closed");
    const intent = core.createIntent(action);
    if (navigatorTarget && navigatorTarget.onLine === false) {
      core.enqueue(intent);
      requestBackgroundSync();
      dispatch("stalled", {
        stalled: true,
        reason: "offline",
        queued: 1,
        pendingCount: core.pendingCount,
        pending: core.pending,
      });
      return { queued: true, intent, reason: "offline" };
    }

    try {
      const value = await replay(intent);
      dispatch("synced", {
        synced: 1,
        count: 1,
        pendingCount: core.pendingCount,
        pending: core.pending,
      });
      return { queued: false, intent, value };
    } catch (error) {
      if (!isNetworkFailure(error)) throw error;
      // Preserve the ID/time used for the direct attempt when moving the
      // intent into the outbox; this is what gives retries server dedupe.
      core.enqueue(intent);
      requestBackgroundSync();
      dispatch("stalled", {
        stalled: true,
        reason: "network",
        queued: 1,
        error,
        pendingCount: core.pendingCount,
        pending: core.pending,
      });
      return { queued: true, intent, error, reason: "network" };
    }
  }

  function write(action) {
    return serialize(() => performWrite(action));
  }

  addListener(eventTarget, "online", triggerFlush);
  addListener(eventTarget, "focus", triggerFlush);
  addListener(documentTarget, "visibilitychange", () => {
    if (!documentTarget || documentTarget.visibilityState === undefined || documentTarget.visibilityState === "visible") {
      triggerFlush();
    }
  });
  addListener(serviceWorkerTarget, "message", (event) => {
    if (event && event.data && event.data.type === "flush-outbox") triggerFlush();
  });
  if (intervalMs !== false && Number.isFinite(intervalMs) && intervalMs > 0 &&
      timers && typeof timers.setInterval === "function") {
    intervalId = timers.setInterval(triggerFlush, intervalMs);
  }

  const ready = options.autoStart === false ? Promise.resolve(null) : triggerFlush();

  function close() {
    if (closed) return;
    closed = true;
    for (const unsubscribe of subscriptions.splice(0)) unsubscribe();
    if (intervalId !== null && timers && typeof timers.clearInterval === "function") {
      timers.clearInterval(intervalId);
      intervalId = null;
    }
  }

  return {
    createIntent: core.createIntent,
    enqueue: core.enqueue,
    retry: core.retry,
    abandon: core.abandon,
    write,
    mutate: write,
    writeThrough: write,
    flush,
    triggerFlush,
    close,
    ready,
    events,
    get pending() {
      return core.pending;
    },
    get pendingCount() {
      return core.pendingCount;
    },
    get hasPending() {
      return core.hasPending;
    },
    addEventListener: events.addEventListener.bind(events),
    removeEventListener: events.removeEventListener.bind(events),
    dispatchEvent: events.dispatchEvent.bind(events),
    on(type, handler) {
      events.addEventListener(type, handler);
      return () => events.removeEventListener(type, handler);
    },
    off(type, handler) {
      events.removeEventListener(type, handler);
    },
  };
}

// Descriptive aliases keep the adapter discoverable without duplicating code.
export const createOutboxBrowser = createBrowserOutbox;
export const createBrowserAdapter = createBrowserOutbox;

// Classic-script consumers can use the same vendored implementation without
// maintaining a second copy.  ESM consumers use the named exports above.
if (typeof globalThis !== "undefined") {
  globalThis.createOutbox = createOutbox;
  globalThis.createBrowserOutbox = createBrowserOutbox;
}
