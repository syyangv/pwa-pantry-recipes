"""`shortlist_intents` — the server half of the offline outbox (§9.18.3).

**This module is the route wrapper, not a second writer.** `app/shortlists/store.py`
remains the only thing that writes `meal_lists`: the ledger calls the store's own
`add` / `remove` / `reorder` and wraps the *call*, never re-implementing the
re-sequence, the position arithmetic, or the `ON CONFLICT DO NOTHING`. There is
no second writer and there is no hook — #16 fixed that shape deliberately.

**The guarded sequence is three transactions, and it has to be.** The obvious
shape — one `BEGIN IMMEDIATE`, read, mutate, insert, commit — deadlocks here,
and the reason is worth stating because it looks like it should work: SQLite
permits exactly one writer at a time, and each store method opens *its own*
connection and issues its own `BEGIN IMMEDIATE`. Holding this module's write
transaction open across the store's call makes the second `BEGIN IMMEDIATE` wait
out `busy_timeout` and then raise `database is locked`. `pwa-deals` gets away
with a single transaction because its route issues the domain SQL itself on the
same connection; this app does not, and cannot without changing a store another
ticket just landed. So: read (ledger transaction) → mutate (the store's own
transaction) → write (ledger transaction).

Two things make that sequence safe, and both are argued rather than assumed:

1. **`serialized` holds one lock per `client_id` across all three steps.** The
   transaction orders two requests against each other; it cannot order a request
   that has already read "no ledger row" against one that is still inside the
   store's write on a *different* connection. The lock spans the gap.
2. **The three mutations are idempotent under their own set semantics**, which is
   what closes the crash window between "the store committed" and "the intent row
   was written":
   - `add` is `INSERT … ON CONFLICT (slot, recipe_note) DO NOTHING`, so a replay
     that re-runs it produces the same `items` and the same row count;
   - `remove` of an already-removed row is `ShortlistEntryMissing` — a refusal,
     not a second deletion;
   - `reorder` rewrites `position` from a whole-list `order`, and replaying the
     same `order` writes the same positions.

So the ledger's job is not to make the *effect* once — the store already is. Its
job is to make the **response** once: a replay must return the bytes the first
delivery returned rather than a fresh, possibly different, projection. That is
what `response_json` is, and why the fingerprint has to be a fingerprint of the
*whole* intent rather than the key alone.

**The fingerprint is `sha256` over a canonical `json.dumps` of
`{"method", "order", "slot", "target"}` — and the two path parameters are both
in it.** `{slot}` is the field whose absence would be silent corruption: a
`breakfast` reorder captured offline and replayed against `dinner` is not a
retry, it is a different edit to a different list, and the only thing that can
tell them apart is the intent. `{note_name}` enters as `target` for `add` and
`remove` and is the literal `"order"` for `reorder`, which is what the spec's
`"target": recipe_note | "order"` shape means. `method` is in it so a `DELETE`
and a `POST` naming the same recipe in the same slot are two intents rather than
one — they are, and a client that reused a key across them has a bug.

`order` is the **array, in the order sent**. A reorder's meaning *is* its order,
so `["a", "b"]` and `["b", "a"]` are different intents and must fingerprint
differently; `sort_keys=True` sorts mapping keys, never list elements, so the
canonical form preserves it. That is also why the order body is fingerprinted at
all rather than reconciled: see the module's `422` note below.

**A 422 is recorded as "not an intent", and the 409 is the bound.** A `reorder`
captured offline against a list that was edited elsewhere replays into
`OrderMembershipMismatch`, and §9.18.1/#16 both say that 422 **stands**: the
route answers it, no intent row is written, and the client's answer is to
re-read and re-offer. A ledger that recorded the refusal would make the *next*,
corrected attempt with the same `client_id` a 409 — turning "your list moved" into
"your key is broken", which is a strictly worse failure. So only a success is
recorded, and a recorded success is a promise the store already keeps.

**A hit with a different fingerprint is 409 `client_id_reused`, and the store is
untouched.** A key is bound to one intent. A client that reuses it for something
else has a bug, and silently accepting would make the replay history
unauditable: `shortlist_intents` would claim two different edits were one, and
the only record of what actually happened would be gone. 409 rather than 422
because the request is well-formed and the *conflict* is with prior state — the
same shape as `daily_note_changed`.

**`serialized` is an in-process lock, and that is a deployment-shaped choice.**
One plain `asyncio.Lock` per `client_id`, held across the whole
`lookup → mutate → record` sequence, is what makes "concurrent
same-`X-Client-Id` requests produce one effect" true rather than "usually true" —
and it is a real lock rather than a `threading.Lock` because the whole sequence
is `await`ed. It is held for the length of two SQLite transactions and one store
call in a single-user local app whose whole reason to exist is that a kitchen is a
plausible offline place, and the store's own `BEGIN IMMEDIATE` already
serialises *every* writer of `meal_lists` regardless. A multi-process deployment
would need a row-level claim in the table instead; that is recorded here rather
than pre-built, because the alternative would be a second writer for a problem
this deployment does not have.

**`ENSURE_INTENT_TABLE` exists because a direct ASGI test client and a rolling
deploy both reach the handler before the startup migration has ever run.**
`pwa-deals` does the same thing (`ensure_status_intent_table`). It is
`IF NOT EXISTS` and it is committed before the ledger's own `BEGIN IMMEDIATE`,
and the numbered migration `_migrate_001_shortlist_intents` in
`app/db/database.py` remains the authoritative creator — this is a floor under
the handler, not a replacement.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Final

import aiosqlite

#: The three SQL statements this module owns, and no more. The store owns
#: `meal_lists`; the ledger owns exactly this table, and a `SELECT` and an
#: `INSERT` into it. There is no `UPDATE` and no `DELETE`: a key's row is written
#: once, and "written once" is the whole guarantee.
SELECT_INTENT: Final = (
    "SELECT request_fingerprint, response_json FROM shortlist_intents WHERE client_id = ?"
)

INSERT_INTENT: Final = (
    "INSERT INTO shortlist_intents (client_id, request_fingerprint, response_json)"
    " VALUES (?, ?, ?)"
)

ENSURE_INTENT_TABLE: Final = (
    "CREATE TABLE IF NOT EXISTS shortlist_intents ("
    "client_id TEXT PRIMARY KEY,"
    "request_fingerprint TEXT NOT NULL,"
    "response_json TEXT NOT NULL,"
    "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
    ")"
)

#: The literal that occupies `target` for a `reorder`. The alternative — the
#: recipe basename `order` would have carried — is a real (if unlikely) recipe
#: name, and a sentinel that collides with a legal value is a fingerprint that
#: cannot distinguish two intents. §9.18.3 names this exact string.
ORDER_TARGET: Final = "order"

#: §9.18.3's 409. Named here rather than spelled at the call site so the route's
#: mapping and the tests that assert it cannot drift, and so a reader can grep
#: one word to find every place a reused key is answered.
CLIENT_ID_REUSED: Final = "client_id_reused"

#: The keys `canonical_json` fixes, in the order the spec names them. Asserted
#: against a live call by `tests/shortlists/test_intents.py` so adding a fifth
#: key to the fingerprint is a test failure and not a silent change of what an
#: intent *is*.
FINGERPRINT_KEYS: Final[tuple[str, ...]] = ("method", "order", "slot", "target")

#: One lock per in-flight `client_id`, and one only. Created on demand and
#: dropped when the holder releases it and nobody else is waiting, so a long
#: session replaying a thousand intents does not accumulate a thousand locks.
_LOCKS: dict[str, asyncio.Lock] = {}


class ClientIdReused(RuntimeError):
    """`X-Client-Id` was already bound to a *different* intent. Answered 409.

    Carries the key in the exception and never in the envelope, for the reason
    every shortlist code is a class attribute rather than a derived string: a
    refusal must not become a reflection of what the caller sent.
    """

    code = CLIENT_ID_REUSED


def canonical_json(payload: Any) -> str:
    """The one serialisation, used for the fingerprint *and* for `response_json`.

    `sort_keys=True` and the compact separators are what make a fingerprint a
    function of the *intent* rather than of the order the dict happened to be
    built in — the client is not Python, so two servers (or two Python versions)
    must agree on the bytes. `ensure_ascii=False` because every value here is a
    CJK basename or a slot, and ASCII-escaping them is a second, larger, and
    equally deterministic encoding that would only make the stored blobs
    unreadable when someone has to open the table with `sqlite3` by hand.

    `allow_nan` is left at its default because nothing here can be a float: the
    fingerprint payload is four strings-and-a-list, and the response payload is
    the two-key/three-key shortlist body this app already publishes.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_fingerprint(
    *, method: str, slot: str, target: str, order: list[str] | None
) -> str:
    """`sha256` over the canonical intent — the key alone is not an intent.

    Every field is load-bearing, and the two that are easiest to omit are the two
    that corrupt silently:

    * **`slot` must be in it.** A `breakfast` reorder replayed against `dinner`
      is a different edit to a different list, and with a fingerprint that
      ignored the slot the server would answer the replay with `dinner`'s cached
      body and quietly apply nothing to `breakfast`. The user would see a list
      that did not move and a success message.
    * **`target` must be in it, which means `{note_name}` is part of the intent.**
      It is the recipe basename for `add` and `remove`. Without it, a `DELETE` of
      `盐焗鸡` and a `DELETE` of `番茄炒蛋` with one key are one intent, and the
      cached response would claim the second row was removed when it was not.

    `method` is in it for the same reason: `POST` and `DELETE` naming the same
    recipe in the same slot are two edits.

    The digest is over UTF-8 bytes of the canonical string, and it is compared
    for **equality only** — it is never used to look anything up. The table is
    keyed by `client_id`; the fingerprint is the "is this the same intent?"
    question asked of a row the key already found.
    """
    canonical = canonical_json(
        {"method": method, "order": order, "slot": slot, "target": target}
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reorder_fingerprint(slot: str, order: list[str]) -> str:
    """The `PUT` fingerprint, spelled so the sentinel cannot be forgotten.

    `order` is the list **as sent**, in the client's order. This is the whole
    reason a replayed `reorder` is answered with §9.12's 422 rather than
    reconciled: the fingerprint says "this is the reorder I asked for", and the
    store then says "that is not what the list is now". Reconciling would mean
    deciding which of two user's intentions wins without asking, and #16's
    docstring names that hazard explicitly.
    """
    return request_fingerprint(
        method="PUT", slot=slot, target=ORDER_TARGET, order=list(order)
    )


def add_fingerprint(slot: str, recipe_note: str) -> str:
    """The `POST` fingerprint. The basename is the `target`; there is no `order`."""
    return request_fingerprint(
        method="POST", slot=slot, target=recipe_note, order=None
    )


def remove_fingerprint(slot: str, note_name: str) -> str:
    """The `DELETE` fingerprint.

    `note_name` is the **stored** name, not the live one — the same value the
    store's `remove` looks up, so a `⚠ 已重命名` row is fingerprinted by the name
    it is filed under and a replay of its removal is recognised as the same
    intent.
    """
    return request_fingerprint(
        method="DELETE", slot=slot, target=note_name, order=None
    )


@asynccontextmanager
async def serialized(client_id: str) -> AsyncIterator[None]:
    """One holder at a time per `client_id`, for the whole guarded sequence.

    The transaction alone cannot do this: the store's mutation runs on its own
    connection, so the "read no ledger row, then write one" pair has a window the
    `BEGIN IMMEDIATE` does not span. The lock is held across the mutation, not
    just across the ledger statements, and it is released by refcount so a waiter
    is never cut loose by the holder's cleanup.
    """
    lock = _LOCKS.get(client_id)
    if lock is None:
        lock = _LOCKS[client_id] = asyncio.Lock()
    # `acquire` first, then register: registering before acquiring would let a
    # waiter arrive, find no lock of its own, and create a second one for the
    # same key.
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()
        if not lock.locked() and _LOCKS.get(client_id) is lock:
            del _LOCKS[client_id]


class ShortlistIntentLedger:
    """`X-Client-Id` -> one stored response, for exactly one intent.

    Constructed once by the `lifespan` with the app's connection factory and
    published on `app.state`, and read by `app/api/shortlists.py` through
    `app/api/resources.py`. It holds no connection, no path, and no cache — the
    factory opens and closes one per operation, which is why it has no `close()`
    and why nothing in this module can pin a WAL handle for the process lifetime.

    **`lookup` and `record` are two separate transactions and they have to be.**
    The obvious shape — one `BEGIN IMMEDIATE`, read, mutate, insert, commit — is
    not available here, and the reason is worth stating because it looks like it
    should work: SQLite permits exactly one writer at a time, and the store's
    `add` / `remove` / `reorder` each open *their own* connection and issue their
    own `BEGIN IMMEDIATE`. Holding this module's write transaction open across the
    store's call deadlocks — the second `BEGIN IMMEDIATE` waits out
    `busy_timeout` and then raises `database is locked`. `pwa-deals` gets away
    with one transaction because its route issues the domain SQL itself on the
    same connection; this app does not, and #16 fixed the store as a black box
    with no transaction hook.

    So the guarded sequence is: read (transaction 1) -> mutate (the store's own
    transaction, on its own connection) -> write (transaction 2). What makes that
    safe is `serialized`, which holds one lock per `client_id` across all three,
    and the store's own idempotence, which is what makes the crash window between
    transactions 2 and 3 harmless. Both are argued at the top of this module.
    """

    def __init__(self, connect: Callable[[], AbstractAsyncContextManager[Any]]) -> None:
        self._connect = connect

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """`BEGIN IMMEDIATE` ... `COMMIT`, or a full rollback and a re-raise.

        `IMMEDIATE` rather than the default `DEFERRED` because the read has to be
        ordered against a *concurrent* write to the same key, not merely against
        itself. The write lock is taken before the `SELECT` so two requests
        carrying one key are serialised rather than both reading "no row".

        The lazy `CREATE TABLE` is inside this transaction and committed first,
        copied from `pwa-deals`' `ensure_status_intent_table`: a direct ASGI test
        client and a rolling deploy both reach the handler before the startup
        migration has ever run, and the numbered migration
        `_migrate_001_shortlist_intents` remains the authoritative creator.
        """
        async with self._connect() as conn:
            if conn.in_transaction:
                raise RuntimeError("nested_shortlist_intent_transaction")
            await conn.execute(ENSURE_INTENT_TABLE)
            await conn.commit()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                if conn.in_transaction:
                    await conn.rollback()
                raise
            if conn.in_transaction:
                await conn.commit()

    async def lookup(self, client_id: str, fingerprint: str) -> str | None:
        """The stored `response_json` for this key, or `None` if the key is new.

        **Raises `ClientIdReused` when the key exists under a *different*
        fingerprint, and rolls back before raising.** The rollback is here rather
        than in the caller so the rule "a different intent leaves the transaction
        exactly as a deliberate refusal would" is a property of the method that
        raises, and not something a caller can forget. Nothing has been written at
        this point, so the rollback is a no-op on the data — it is there to make
        the *intent* explicit: this request did not happen.
        """
        async with self._transaction() as conn:
            row = await (await conn.execute(SELECT_INTENT, (client_id,))).fetchone()
            if row is None:
                return None
            if str(row["request_fingerprint"]) != fingerprint:
                raise ClientIdReused(client_id)
            return str(row["response_json"])

    async def record(
        self, client_id: str, fingerprint: str, response: Any
    ) -> str:
        """Store the response for this key and hand back the exact bytes.

        The return value is what the route sends, **not** a re-serialisation of
        the object it was handed. That is the whole guarantee: the first delivery
        and every replay are one string, and a second `json.dumps` of a dict that
        has been through a request/response cycle is exactly where that quietly
        stops being true. So the canonical string is computed once, stored, and
        returned.

        The `IntegrityError` catch is not defensive noise. It is the third writer
        for one key — a different process, or a future deployment that drops the
        in-process lock. A key is written once; a second `INSERT` for it is the
        caller's bug, and answering with the *stored* body, which by construction
        is the first writer's, is both safe and honest.
        """
        payload = canonical_json(response)
        async with self._transaction() as conn:
            try:
                await conn.execute(INSERT_INTENT, (client_id, fingerprint, payload))
            except sqlite3.IntegrityError:
                row = await (await conn.execute(SELECT_INTENT, (client_id,))).fetchone()
                if row is None:
                    raise
                payload = str(row["response_json"])
        return payload


__all__ = [
    "CLIENT_ID_REUSED",
    "FINGERPRINT_KEYS",
    "ORDER_TARGET",
    "ClientIdReused",
    "ShortlistIntentLedger",
    "add_fingerprint",
    "canonical_json",
    "reorder_fingerprint",
    "remove_fingerprint",
    "request_fingerprint",
    "serialized",
]
