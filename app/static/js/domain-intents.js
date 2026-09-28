/* The app's half of the offline outbox: the intents, the replay, and the one
 * outbox the app owns. docs/pwa-template.md Part 6, spec 9.18.
 *
 * The vendored `/js/pwa/outbox.js` is the transport — localStorage intents,
 * `clientId` minting, the `online` / `focus` / `visibilitychange` / interval /
 * `{type:'flush-outbox'}` triggers, and a serialized flush. It is byte-identical
 * to `~/projects/pwa-template/` and must stay so; nothing in this file edits it
 * and nothing in this file re-implements a queue. What Part 6b says the app owns
 * is `replay`, because the app is what knows the endpoint, the CSRF token, the
 * revision, and the UI semantics — and what the app must not do is invent a
 * second pending-edit signal, so this file drives `pending-edits.js`'s existing
 * counter rather than adding a flag next to it.
 *
 * `X-Client-Id` is set from `intent.clientId` on every replay, and that is the
 * whole client half of the exactly-once contract: the server binds that header
 * to a `sha256` fingerprint of the intent's method, slot, target, and order
 * (spec 9.18.3), returns the first delivery's bytes on a matching replay, and
 * answers 409 `client_id_reused` if one key is ever spent on two intents. A
 * replay that omitted the header would be a second, unguarded mutation.
 *
 * WHY THE TYPE LIST IS CLOSED AND WHY `requestForIntent` THROWS. §9.18.1's two
 * lists are exhaustive, and the second one — the write that is deliberately,
 * permanently offline-only, because its domain statement is dated and its
 * revision goes stale by construction — is enforced here by *absence*: there is
 * no type constant for it, no intent builder that produces one, and
 * `replayDomainIntent` refuses any type it does not recognise. An unknown type
 * is a programming error, so it throws rather than being ignored, and an ignored
 * intent would leave it in the queue forever with no error the user could see.
 * `tests/js/outbox_contract.test.mjs` asserts the negative structurally (the
 * source carries no such type) and behaviourally (replaying one is refused), so
 * "just in case" wiring fails the suite rather than shipping.
 */

import { apiFetch } from './api.js?v=__APP_VERSION__';
import { markPendingEdits, pendingEdits, resetPendingEdits } from './pending-edits.js?v=__APP_VERSION__';
import { createBrowserOutbox } from './pwa/outbox.js?v=__APP_VERSION__';

/* The three mutations §9.18.1 puts on the outbox, and the whole of them. */
export const SHORTLIST_ADD = 'shortlist.add';
export const SHORTLIST_REMOVE = 'shortlist.remove';
export const SHORTLIST_REORDER = 'shortlist.reorder';

/**
 * The closed set of replayable intent types.
 *
 * Frozen, and asserted against the `switch` in `requestForIntent` from
 * `tests/js/outbox_contract.test.mjs`, so adding a case there without adding it
 * here (or the reverse) is a failing test rather than an intent that is silently
 * unreplayable.
 */
export const REPLAYABLE_INTENT_TYPES = Object.freeze([
  SHORTLIST_ADD,
  SHORTLIST_REMOVE,
  SHORTLIST_REORDER,
]);

/** The header §9.18.3's ledger is keyed on. Named once so it cannot drift. */
export const CLIENT_ID_HEADER = 'X-Client-Id';

/**
 * An intent type this build will not replay. A programming error, not a
 * condition a user can reach: the only way to produce one is to hand the outbox
 * a type the app never mints.
 */
export class UnsupportedIntentError extends Error {
  constructor(type) {
    super(`unsupported intent type: ${String(type)}`);
    this.name = 'UnsupportedIntentError';
    /* `code`, not `status`: the vendored outbox's `serializeParkReason` reads
     * it, and a parked intent must carry a reason the UI can show rather than
     * a number it would try to render as an HTTP status. */
    this.code = 'unsupported_intent';
  }
}

const segment = (value) => encodeURIComponent(String(value));

/**
 * The request an intent means, as a pure function of the intent.
 *
 * Pure, and the reason the contract test can assert "every enqueued type is
 * replayable" and "the replay sets `X-Client-Id`" without a DOM, a fetch stub, or
 * a running app. It returns a spec rather than performing it, so the same
 * function is what the view-level tests and `replayDomainIntent` both go
 * through — one place decides what a `shortlist.reorder` *is*.
 *
 * `{noteName}` is a URL path segment and is percent-encoded; the server resolves
 * it against its own recipe index and never joins it to a path (Server-Owned
 * Root, D2). `{slot}` is a closed enum and is encoded anyway, so a slot can
 * never be a way to escape the route shape.
 */
export function requestForIntent(intent) {
  if (!intent || typeof intent.type !== 'string') {
    throw new UnsupportedIntentError(intent && intent.type);
  }
  const payload = intent.payload || {};
  switch (intent.type) {
    case SHORTLIST_ADD:
      return {
        method: 'POST',
        path: `/api/shortlists/${segment(payload.slot)}`,
        body: { recipeNote: String(payload.recipeNote) },
      };
    case SHORTLIST_REMOVE:
      return {
        method: 'DELETE',
        /* No body: §9.16's DELETE carries the name in the path, and a body on a
         * DELETE is a second place for the same fact to disagree. */
        path: `/api/shortlists/${segment(payload.slot)}/${segment(payload.noteName)}`,
        body: undefined,
      };
    case SHORTLIST_REORDER:
      /* The whole list, in the user's order — §9.12's whole-list argument, not a
       * delta. The server refuses anything that is not exactly the current
       * membership, and that refusal is deliberately not reconciled here. */
      return {
        method: 'PUT',
        path: `/api/shortlists/${segment(payload.slot)}/order`,
        body: { order: [...(payload.order || [])] },
      };
    default:
      throw new UnsupportedIntentError(intent.type);
  }
}

/**
 * Replay one intent. The `replay` the app hands to the vendored outbox.
 *
 * Goes through `apiFetch`, so the replay carries the boot session's
 * `X-CSRF-Token` and is counted by `mutationInFlight()` — which is what keeps
 * F18's busy-guard honest: a queued intent being replayed is a write in flight,
 * and an update must not be applied underneath it exactly as it must not be
 * applied underneath a Cooking Log write.
 *
 * The `X-Client-Id` header is mandatory and asserted, not defaulted. The
 * vendored outbox always mints one, so an intent without it is a caller that
 * built the intent by hand; sending the mutation without it would apply it a
 * second time on every retry, which is the exact failure the ledger exists to
 * prevent, so this throws rather than quietly doing the unsafe thing.
 */
export async function replayDomainIntent(intent) {
  const request = requestForIntent(intent);
  const clientId = intent.clientId;
  if (typeof clientId !== 'string' || clientId === '') {
    throw new UnsupportedIntentError(intent.type);
  }
  return apiFetch(request.path, {
    method: request.method,
    body: request.body,
    headers: { [CLIENT_ID_HEADER]: clientId },
  });
}

/* --- the one outbox, and the F19 pending-edit seam ----------------------- */

/*
 * `pending-edits.js` is #21's counter and F19's `hasPendingEdit()` gate; #23
 * replaces the *producer*, not the counter. The mirror below is the whole
 * integration, and it is a reconcile rather than an event log on purpose: the
 * counter is a gate ("has the app confirmed my change?"), and a gate wants the
 * current truth, not an increment that can drift if an event is missed.
 *
 * `inFlight` covers the window the queue depth cannot. An intent being written
 * while online is *not* in the queue — the vendored core only enqueues on an
 * offline or network-failed attempt — so a queue-depth-only mirror would drop
 * the counter to zero for the whole duration of a request, which is the one
 * window F19's pull-to-refresh guard exists to protect.
 */
let mirrored = 0;
let inFlight = 0;
let instance = null;

function syncPendingEdits() {
  if (instance === null) return;
  const target = instance.pendingCount + inFlight;
  if (target === mirrored) return;
  markPendingEdits(target - mirrored);
  mirrored = target;
}

/**
 * One `createBrowserOutbox`, with `replay` bound and the pending-edit mirror
 * attached.
 *
 * The vendored adapter supplies every trigger (Part 6c) and serialises the
 * flush itself; this function adds exactly two things, and both are the app's:
 * what a replay *is*, and what "unconfirmed" means for the UI.
 */
export function createAppOutbox(options = {}) {
  const outbox = createBrowserOutbox({ ...options, replay: replayDomainIntent });
  outbox.on('synced', syncPendingEdits);
  outbox.on('stalled', syncPendingEdits);
  return outbox;
}

/** Create the app's single outbox. Idempotent, so a second call is a no-op. */
export function initOutbox(options = {}) {
  if (instance === null) instance = createAppOutbox(options);
  return instance;
}

/** The app's outbox, created on first use. Throws rather than guessing. */
export function getOutbox() {
  if (instance === null) {
    throw new Error('the outbox is not initialised; call initOutbox() at boot');
  }
  return instance;
}

/** Whether a view can write at all. `false` before boot, `true` after. */
export function outboxReady() {
  return instance !== null;
}

/**
 * Write one intent: straight through when online, durably queued when not.
 *
 * The only enqueue path in the app. A view that wanted to enqueue something
 * else would have to build the intent itself, and `requestForIntent` would
 * refuse to replay it — so "which mutations are on the outbox" is a question this
 * module answers, not one each view answers.
 *
 * The result is the vendored core's: `{queued: false, intent, value}` for a
 * write that landed, `{queued: true, intent, reason}` for one that did not.
 * `reason` is `'offline'` when `navigator.onLine` was already false and
 * `'network'` when the request failed at the transport layer — a 4xx or 5xx is
 * neither, and is rethrown so the view can show the server's own message.
 */
export async function writeIntent(type, payload) {
  const outbox = getOutbox();
  inFlight += 1;
  syncPendingEdits();
  try {
    return await outbox.write({ type, payload });
  } finally {
    inFlight -= 1;
    syncPendingEdits();
  }
}

/** Whether the app has an unconfirmed change. F19's `canStart` reads this. */
export function hasUnconfirmedChange() {
  return pendingEdits() > 0;
}

/**
 * Tear the outbox down and reset the counter. For tests and for a shell that is
 * genuinely being replaced; a normal reload just lets the next `initOutbox()`
 * read the queue back out of storage.
 */
export function resetOutbox() {
  if (instance !== null) instance.close();
  instance = null;
  mirrored = 0;
  inFlight = 0;
  resetPendingEdits();
}
