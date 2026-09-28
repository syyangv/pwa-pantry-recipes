/* The pending-edit counter, and the F19 gate that reads it.
 *
 * F19 fixes `canStart: () => navigator.onLine && !hasPendingEdit()` for
 * pull-to-refresh. The second term exists so a pull cannot discard work the app
 * has not yet confirmed — the outbox replay of #23 is its intended producer, and
 * #23 has not landed. Rather than write the gate as a literal `false` (which
 * would make F19's promise unreviewable and give #23 a reason to change this
 * file) the counter is real, and this wave's own mutations drive it: a Meal
 * Shortlist add made from the recipe view marks itself pending for the duration
 * of its request.
 *
 * That is a NARROWER promise than the outbox's, and deliberately so: a shortlist
 * add is a single online POST whose only unconfirmed window is the request
 * itself, and pulling to refresh during it would swap the list out from under a
 * control the user is watching. #23 replaces the producer, not the counter.
 *
 * A counter, not a boolean, because the recipe view can have three shortlist
 * requests in flight at once (one per meal) and `true -> false` on the first
 * settle would re-open the gate while two are still outstanding.
 */

let pending = 0;

export function pendingEdits() {
  return pending;
}

export function markPendingEdits(delta) {
  const change = Number.isFinite(Number(delta)) ? Number(delta) : 0;
  pending = Math.max(0, pending + change);
  return pending;
}

export function hasPendingEdit() {
  return pending > 0;
}

/** For tests and for a view that is remounted from scratch. */
export function resetPendingEdits() {
  pending = 0;
}
