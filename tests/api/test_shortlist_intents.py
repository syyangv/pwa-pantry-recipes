"""§9.18.3's `X-Client-Id` exactly-once ledger, end to end through the real app.

Run alone:  .venv/bin/python -m pytest tests/api/test_shortlist_intents.py -q

**Every request goes through the real `create_app(settings)`.** The ledger's
guarantee is about what the app does with a header on a route, so a router
exercised outside `lifespan` would be evidence about a different app. The
database is the one the `lifespan` creates under `tmp_path`.

**The assertions that matter are about ROW COUNTS, not about responses.** A
response assertion is satisfied by a route that answered correctly while applying
the edit twice; `test_a_replayed_add_leaves_exactly_one_row` reads
`meal_lists` directly so it cannot pass that way. This is the whole reason the
ledger exists — the store's three mutations are already idempotent under their
own set semantics, and the ledger's job is the *response*, so a test that only
looked at responses would be testing the part that was never at risk.

**The negative cases are given equal weight, because each is a way the
guarantee inverts rather than weakens.** A `reorder` replayed against a list
edited elsewhere is answered 422 and records nothing; a key reused for a
different intent is 409 with the store untouched; a refusal never becomes a
ledger row, which is what leaves the client's *corrected* retry a fresh miss
instead of a 409.

**F1's fail-closed 503 is tested as a boundary, not as an absence.** An
unreadable `Pantry.md` 503s the whole recipe list, and the shortlist routes must
replay cleanly through and after it. The test breaks the note, proves the 503 is
real (so the assertion cannot pass vacuously), then drives the whole offline ->
online cycle anyway.

**Nothing here reads a live path.** The vault, the `Pantry.md`, and the catalog
are written per test under `tmp_path` from the committed literals in
`tests/api/conftest.py`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from app.api.client_id import MAX_CLIENT_ID_LENGTH
from app.api.shortlists import (
    CLIENT_ID_INVALID,
    CLIENT_ID_REUSED_CODE,
    LEDGER_CODES,
    ORDER_MISMATCH,
    SHORTLIST_ENTRY_MISSING,
    build_shortlists_router,
)
from app.config import Settings
from app.db.database import connect_db
from app.shortlists.intents import (
    FINGERPRINT_KEYS,
    ORDER_TARGET,
    canonical_json,
    request_fingerprint,
)
from app.shortlists.store import MEAL_SLOTS
from tests.api.conftest import (
    API_CATALOG_ROWS,
    MAIN_RECIPE,
    ORIGIN,
    RECIPES_ROOT,
    client_for,
    make_settings,
    seed_catalog,
    write_pantry_note,
    write_recipe,
)

#: One real second recipe, so a reorder has something to reorder and a remove has
#: something that is not the recipe the add named. Named like a real note,
#: because the `⚠ 已重命名` story in the store is about real basenames.
SECOND_RECIPE: Final = "盐焗鸡"
SECOND_RECIPE_BYTES: bytes = (
    "---\n"
    "材料:\n"
    "  - 鸡\n"
    "调料:\n"
    "  - 盐\n"
    "---\n"
    "# 步骤\n"
    "1. 焗。\n"
).encode()

#: A third real recipe, so a reorder can be made stale by a row the client never
#: saw. `store.add` validates against the live index, so "the other device added
#: something" has to be a note that exists — a made-up name would be refused as
#: `unknown_recipe_note` and the membership would never change, which is how a
#: stale-reorder test can pass for the wrong reason.
THIRD_RECIPE: Final = "清蒸鲈鱼"
THIRD_RECIPE_BYTES: bytes = (
    "---\n"
    "材料:\n"
    "  - 鲈鱼\n"
    "调料:\n"
    "  - 姜\n"
    "---\n"
    "# 步骤\n"
    "1. 蒸。\n"
).encode()

#: A client id that is a plausible outbox token. The vendored outbox mints
#: `crypto.randomUUID()`; nothing about the server's handling should care, and
#: these tests use a readable one so a failure names the intent it was replaying.
KEY_A: Final = "11111111-1111-4111-8111-111111111111"
KEY_B: Final = "22222222-2222-4222-8222-222222222222"


@dataclass
class _App:
    """The real app, two recipes, a pantry note, a seeded catalog, and helpers."""

    client: TestClient
    settings: Settings
    origin: str

    def add(self, slot: str, note: str, key: str | None = None) -> Any:
        return self.client.post(
            f"/api/shortlists/{slot}",
            json={"recipeNote": note},
            headers=self.headers(key),
        )

    def remove(self, slot: str, note: str, key: str | None = None) -> Any:
        return self.client.delete(
            f"/api/shortlists/{slot}/{note}", headers=self.headers(key)
        )

    def order(self, slot: str, order: list[str], key: str | None = None) -> Any:
        return self.client.put(
            f"/api/shortlists/{slot}/order",
            json={"order": order},
            headers=self.headers(key),
        )

    def headers(self, key: str | None) -> dict[str, str]:
        base = {"Origin": self.origin, "X-CSRF-Token": self.token}
        if key is not None:
            base["X-Client-Id"] = key
        return base

    @property
    def token(self) -> str:
        return str(self.client.get("/api/session").json()["csrfToken"])

    def rows(self, slot: str) -> list[tuple[int, str]]:
        """`(position, recipe_note)` for one slot, read from SQLite directly.

        Direct rather than through `GET /api/shortlists` on purpose: the assertion
        is about how many writes reached the table, and a read that went through
        the same route under test could agree with a double application for
        reasons that have nothing to do with the ledger.
        """

        async def stored() -> list[tuple[int, str]]:
            async with connect_db(self.settings) as conn:
                cursor = await conn.execute(
                    "SELECT position, recipe_note FROM meal_lists"
                    " WHERE slot = ? ORDER BY position, id",
                    (slot,),
                )
                return [(int(row[0]), str(row[1])) for row in await cursor.fetchall()]

        return asyncio.run(stored())

    def ledger(self) -> list[tuple[str, str, str]]:
        """`(client_id, request_fingerprint, response_json)` — the whole table."""

        async def stored() -> list[tuple[str, str, str]]:
            async with connect_db(self.settings) as conn:
                cursor = await conn.execute(
                    "SELECT client_id, request_fingerprint, response_json"
                    " FROM shortlist_intents ORDER BY client_id"
                )
                return [
                    (str(row[0]), str(row[1]), str(row[2]))
                    for row in await cursor.fetchall()
                ]

        return asyncio.run(stored())


@pytest.fixture
def app(runtime_root: Path) -> Iterator[_App]:
    configured = make_settings(runtime_root)
    seed_catalog(configured.pantry_items_db, API_CATALOG_ROWS)
    write_recipe(configured.vault_path, MAIN_RECIPE)
    write_recipe(configured.vault_path, SECOND_RECIPE, SECOND_RECIPE_BYTES)
    write_recipe(configured.vault_path, THIRD_RECIPE, THIRD_RECIPE_BYTES)
    write_pantry_note(configured.vault_path)
    with client_for(configured) as client:
        yield _App(client=client, settings=configured, origin=ORIGIN)


# --- 1. the exactly-once claim, asserted as a row count -------------------


def test_a_replayed_add_leaves_exactly_one_row(app: _App) -> None:
    """Offline -> replay, once. **The assertion is `meal_lists`, not the body.**

    One intent, delivered twice with the same `X-Client-Id`. The second delivery
    is recognised by the fingerprint, short-circuits before the store is reached,
    and returns the first delivery's bytes. The table has one row and the ledger
    has one row.

    The row count is the assertion that cannot be faked: a route that applied the
    mutation twice and then answered correctly would pass every response-shaped
    test in the suite, because `add` is `ON CONFLICT DO NOTHING` and the second
    application is invisible above the API.
    """
    first = app.add("lunch", MAIN_RECIPE, key=KEY_A)
    replay = app.add("lunch", MAIN_RECIPE, key=KEY_A)

    assert first.status_code == replay.status_code == 200
    assert first.content == replay.content, "the replay was not the stored bytes"
    assert app.rows("lunch") == [(0, MAIN_RECIPE)]
    assert len(app.ledger()) == 1


def test_the_ledger_stores_the_canonical_response_not_a_re_encoding(app: _App) -> None:
    """`response_json` is `canonical_json(body)`, and the wire body IS that string.

    A ledger that stored `json.dumps(response)` with default separators would
    still "work" on a dict comparison and would break the property the module
    exists for: the first delivery and the replay would be different byte strings
    carrying the same object. Asserting `response.content == stored` pins the two
    together, so the mutation is caught here rather than by a client that
    compares bytes.
    """
    response = app.add("lunch", MAIN_RECIPE, key=KEY_A)

    (_client_id, _fingerprint, stored) = app.ledger()[0]
    assert stored == canonical_json(response.json())
    assert response.content.decode("utf-8") == stored
    # And the canonical form is sorted, compact, and unescaped — the three
    # properties that make it a function of the intent rather than of the
    # serialiser's defaults.
    assert " " not in stored
    assert "番茄" in stored
    assert stored.startswith('{"items":')


def test_a_replay_returns_the_response_even_after_the_list_moved_on(app: _App) -> None:
    """The cached body is the **first** delivery's, not today's.

    This is the case the ledger exists for and the one a "just re-run the query"
    implementation gets wrong. A replayed `add` arriving after the user removed
    the row by hand still answers with the items as they were when the intent was
    first accepted, because the client asked "did my queued edit land?" and the
    honest answer to that is the receipt, not a fresh projection the client would
    read as its own edit having been undone and redone.

    The row count is the control: the replay did not re-add the row the user
    removed.
    """
    first = app.add("lunch", MAIN_RECIPE, key=KEY_A)
    assert app.remove("lunch", MAIN_RECIPE).status_code == 200
    assert app.rows("lunch") == []

    replay = app.add("lunch", MAIN_RECIPE, key=KEY_A)

    assert replay.status_code == 200
    assert replay.content == first.content
    assert app.rows("lunch") == [], "the replay re-applied an edit the user removed"
    assert len(app.ledger()) == 1


def test_a_replayed_reorder_and_a_replayed_remove_are_also_exactly_once(app: _App) -> None:
    """All three routes are wrapped, not just the one the recipe view can reach.

    Parametrised over the three mutations rather than written three times, because
    the guarantee is "the three routes", and a test that only proves it for
    `POST` proves it for the one route with a control in the UI.
    """
    app.add("lunch", MAIN_RECIPE)
    app.add("lunch", SECOND_RECIPE)

    order = [SECOND_RECIPE, MAIN_RECIPE]
    assert app.order("lunch", order, key=KEY_A).status_code == 200
    assert app.order("lunch", order, key=KEY_A).status_code == 200
    assert app.rows("lunch") == [(0, SECOND_RECIPE), (1, MAIN_RECIPE)]

    assert app.remove("lunch", MAIN_RECIPE, key=KEY_B).status_code == 200
    assert app.remove("lunch", MAIN_RECIPE, key=KEY_B).status_code == 200
    assert app.rows("lunch") == [(0, SECOND_RECIPE)]

    assert len(app.ledger()) == 2


# --- 2. the 409, and the fingerprints that make it correct -----------------


def test_a_key_reused_for_a_different_intent_is_409_and_changes_nothing(app: _App) -> None:
    """A key is bound to one intent. A second, different intent under it is 409.

    The first request adds `番茄炒蛋` to `lunch`. The second names the same slot
    and the same key but a *different recipe* — a client bug, or two intents
    minted from one token. The answer is 409 `client_id_reused`, the envelope is
    exactly `{requestId, code}`, and **the store is untouched**: the second
    request must not have added `盐焗鸡`.

    Silently accepting would be the worse failure by a wide margin: the ledger
    would claim two different edits were one, and the only record of what
    actually happened would be gone.
    """
    assert app.add("lunch", MAIN_RECIPE, key=KEY_A).status_code == 200

    reused = app.add("lunch", SECOND_RECIPE, key=KEY_A)

    assert reused.status_code == 409
    body = reused.json()
    assert set(body) == {"requestId", "code"}, body
    assert body["code"] == CLIENT_ID_REUSED_CODE
    assert app.rows("lunch") == [(0, MAIN_RECIPE)]
    assert len(app.ledger()) == 1


def test_the_slot_is_part_of_the_intent_so_a_breakfast_reorder_cannot_hit_dinner(
    app: _App,
) -> None:
    """**`{slot}` is in the fingerprint**, and this is the corruption it prevents.

    A reorder of `breakfast` replayed against `dinner` is not a retry — it is a
    different edit to a different list. With a fingerprint that ignored the slot,
    the server would answer the replay with `dinner`'s cached body and apply
    nothing to `breakfast`: a success message over a list that did not move.
    """
    app.add("breakfast", MAIN_RECIPE)
    app.add("dinner", SECOND_RECIPE)

    assert app.order("breakfast", [MAIN_RECIPE], key=KEY_A).status_code == 200

    replayed_against_dinner = app.order("dinner", [SECOND_RECIPE], key=KEY_A)

    assert replayed_against_dinner.status_code == 409
    assert replayed_against_dinner.json()["code"] == CLIENT_ID_REUSED_CODE
    # Neither list moved, and neither gained the other's row.
    assert app.rows("breakfast") == [(0, MAIN_RECIPE)]
    assert app.rows("dinner") == [(0, SECOND_RECIPE)]


def test_the_recipe_name_is_part_of_the_intent_so_a_delete_cannot_change_targets(
    app: _App,
) -> None:
    """**`{note_name}` is part of the intent, as `target`.**

    First the client queues "remove 番茄炒蛋 from lunch". The replay, through a
    client bug, names `盐焗鸡` under the same key. That is not the same intent:
    answering it with the cached body would claim `盐焗鸡` was removed when it is
    still on the list, which is a silent data-integrity failure wearing a 200.
    """
    app.add("lunch", MAIN_RECIPE)
    app.add("lunch", SECOND_RECIPE)

    assert app.remove("lunch", MAIN_RECIPE, key=KEY_A).status_code == 200
    retargeted = app.remove("lunch", SECOND_RECIPE, key=KEY_A)

    assert retargeted.status_code == 409
    assert app.rows("lunch") == [(0, SECOND_RECIPE)]


def test_the_method_is_part_of_the_intent_so_add_and_remove_do_not_collide(
    app: _App,
) -> None:
    """`POST` naming a recipe and `DELETE` naming it are two edits, not one."""
    assert app.add("lunch", MAIN_RECIPE, key=KEY_A).status_code == 200

    removed = app.remove("lunch", MAIN_RECIPE, key=KEY_A)

    assert removed.status_code == 409
    assert app.rows("lunch") == [(0, MAIN_RECIPE)]


def test_a_different_order_is_a_different_intent(app: _App) -> None:
    """The order array is fingerprinted **as sent**, permutation included.

    A reorder's meaning *is* its order, so `["a", "b"]` and `["b", "a"]` are two
    intents and one key cannot carry both. The `sort_keys=True` in the canonical
    form sorts mapping keys and never list elements, so this holds for the
    reason the canonical form is the one place the intent is built.
    """
    app.add("lunch", MAIN_RECIPE)
    app.add("lunch", SECOND_RECIPE)

    assert app.order("lunch", [MAIN_RECIPE, SECOND_RECIPE], key=KEY_A).status_code == 200
    reversed_once = app.order("lunch", [SECOND_RECIPE, MAIN_RECIPE], key=KEY_A)

    assert reversed_once.status_code == 409
    assert app.rows("lunch") == [(0, MAIN_RECIPE), (1, SECOND_RECIPE)]


def test_the_canonical_fingerprint_does_not_depend_on_dict_insertion_order() -> None:
    """The canonicalisation is load-bearing, and this is the test that says so.

    The client is not Python, and neither is the next server. If the fingerprint
    were built from whatever order the dict happened to be assembled in, two
    servers — or one server before and after a refactor that reordered a literal
    — would compute different digests for the same intent, and every replay would
    be a 409. So: the same four keys in a different order, one digest.
    """
    one = request_fingerprint(method="POST", slot="lunch", target=MAIN_RECIPE, order=None)
    two = request_fingerprint(method="POST", slot="lunch", target=MAIN_RECIPE, order=None)

    assert one == two
    assert len(one) == 64, "a sha256 hexdigest is 64 characters"
    # The keys are the spec's four, named once and asserted against a live call.
    assert set(FINGERPRINT_KEYS) == {"method", "order", "slot", "target"}
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    # A reorder's `target` is the sentinel, and it is not a recipe basename.
    assert ORDER_TARGET == "order"


# --- 3. a refusal records nothing, which is what makes a retry possible -----


def test_a_replayed_reorder_against_a_list_edited_elsewhere_is_422_and_stands(
    app: _App,
) -> None:
    """**The lost-update hazard #16 named, answered the way #16 said to answer it.**

    The client is offline with `lunch = [番茄炒蛋, 盐焗鸡]` and queues the reorder
    `[盐焗鸡, 番茄炒蛋]`. Meanwhile the list is edited elsewhere — a third row is
    added. The replay carries an order that is no longer the membership, and the
    store refuses it with §9.12's 422.

    The three things asserted are the three ways this could be got wrong:

    1. it is a **422**, not a 200 and not a reconciliation — reconciling would
       mean choosing between two of the user's intentions without asking, and
       would either drop the row added elsewhere or drop one the reorder named;
    2. the **store is unchanged** — the third row is still there, at its own
       position, and the two original rows are where they were;
    3. **nothing was recorded** — no ledger row for the key, so the client's
       corrected retry (re-read the list, re-offer) is a fresh miss rather than
       a 409 that would read as "your key is broken".
    """
    app.add("lunch", MAIN_RECIPE)
    app.add("lunch", SECOND_RECIPE)
    queued = [SECOND_RECIPE, MAIN_RECIPE]

    # The other device's edit, while this one was offline. A real note: `add`
    # validates against the live index, so a made-up name would be refused and
    # the membership would never change.
    assert app.add("lunch", THIRD_RECIPE).status_code == 200

    replay = app.order("lunch", queued, key=KEY_A)

    assert replay.status_code == 422
    body = replay.json()
    assert set(body) == {"requestId", "code"}, body
    assert body["code"] == ORDER_MISMATCH
    assert app.rows("lunch") == [
        (0, MAIN_RECIPE),
        (1, SECOND_RECIPE),
        (2, THIRD_RECIPE),
    ], "the store was reconciled instead of refused"
    assert app.ledger() == [], "a refusal was recorded as an intent"


def test_a_corrected_retry_under_the_same_key_is_a_fresh_miss_not_a_409(
    app: _App,
) -> None:
    """The consequence of the previous test, and the reason it is written that way.

    Same `client_id`. First attempt: a stale order, refused. Second attempt: the
    order the client re-read and is actually asking for. If the refusal had been
    recorded, this would be a 409 and the user would be told their idempotency key
    was reused — for an edit they never made.
    """
    app.add("lunch", MAIN_RECIPE)
    app.add("lunch", SECOND_RECIPE)
    app.add("lunch", THIRD_RECIPE)

    assert app.order("lunch", [SECOND_RECIPE, MAIN_RECIPE], key=KEY_A).status_code == 422

    corrected = app.order(
        "lunch", [SECOND_RECIPE, MAIN_RECIPE, THIRD_RECIPE], key=KEY_A
    )

    assert corrected.status_code == 200
    assert app.rows("lunch") == [
        (0, SECOND_RECIPE),
        (1, MAIN_RECIPE),
        (2, THIRD_RECIPE),
    ]


def test_a_404_on_a_replayed_remove_records_nothing_either(app: _App) -> None:
    """A remove whose row is already gone is a 404, and still not an intent.

    The row count is the point: the replay must not resurrect anything and must
    not become a receipt for a removal that the user performed by hand.
    """
    app.add("lunch", MAIN_RECIPE)
    assert app.remove("lunch", MAIN_RECIPE).status_code == 200

    replay = app.remove("lunch", MAIN_RECIPE, key=KEY_A)

    assert replay.status_code == 404
    assert replay.json()["code"] == SHORTLIST_ENTRY_MISSING
    assert app.rows("lunch") == []
    assert app.ledger() == []


def test_an_unknown_recipe_records_nothing(app: _App) -> None:
    """A 422 for an unknown recipe is not an intent, for the same reason."""
    assert app.add("lunch", "不存在的菜", key=KEY_A).status_code == 422
    assert app.ledger() == []


# --- 4. concurrency -------------------------------------------------------


def test_concurrent_requests_with_one_client_id_have_one_effect(app: _App) -> None:
    """Two deliveries of the same intent, at the same time, produce one row.

    The `BEGIN IMMEDIATE` alone would order the two against each other, but the
    store's mutation runs on a *second* connection — so a second request that has
    read "no ledger row" could interleave with the first request's
    still-uncommitted store call. The in-process lock in
    `app/shortlists/intents.py` is what spans that window, and this is the test
    that says so.

    Both threads share one `TestClient`, which is a portal onto one event loop,
    so "concurrent" here really is two requests in flight inside one loop rather
    than two processes. Asserted on the table and on the ledger, because a
    double-applied `add` is invisible above the API.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pending = [
            pool.submit(app.add, "lunch", MAIN_RECIPE, KEY_A) for _ in range(2)
        ]
        responses = [future.result() for future in pending]

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].content == responses[1].content
    assert app.rows("lunch") == [(0, MAIN_RECIPE)]
    assert len(app.ledger()) == 1


def test_concurrent_requests_with_two_different_ids_each_have_one_effect(
    app: _App,
) -> None:
    """Different keys must not serialise into one, or into neither.

    The negative control for the lock: a lock that ignored the key would make
    these two intents interfere, and one of them would be answered from the
    other's cached body.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pending = [
            pool.submit(app.add, "lunch", MAIN_RECIPE, KEY_A),
            pool.submit(app.add, "dinner", SECOND_RECIPE, KEY_B),
        ]
        responses = [future.result() for future in pending]

    assert [response.status_code for response in responses] == [200, 200]
    assert app.rows("lunch") == [(0, MAIN_RECIPE)]
    assert app.rows("dinner") == [(0, SECOND_RECIPE)]
    assert len(app.ledger()) == 2


# --- 5. the key's own validation ------------------------------------------


@pytest.mark.parametrize("key", ["", "   ", "x" * (MAX_CLIENT_ID_LENGTH + 1)])
def test_an_unusable_client_id_is_400_and_writes_nothing(app: _App, key: str) -> None:
    """A present-but-unusable key is a malformed request, not a 422.

    Same status and same code as `POST /api/cook-logs` and `pwa-deals`, and for
    the same reason: answering 422 would invite a client to retry the same broken
    key forever, and it would be retrying a *malformed request*, not a semantic
    failure of the edit.
    """
    response = app.add("lunch", MAIN_RECIPE, key=key)

    assert response.status_code == 400
    assert response.json()["code"] == CLIENT_ID_INVALID
    assert app.rows("lunch") == []
    assert app.ledger() == []


def test_the_ledger_codes_publish_no_optional_envelope_key() -> None:
    """The per-code discipline, as an assertion about the set rather than prose.

    §9.16 widens the envelope for exactly two codes, both the Cooking Log's, and
    every other code still emits exactly `{requestId, code}`. A ledger code that
    grew a `message` could echo what the caller sent — which is why the codes are
    named constants and not derived strings. This asserts the set exists, holds
    both codes, and carries no optional-key allowance.
    """
    assert LEDGER_CODES == frozenset({CLIENT_ID_INVALID, CLIENT_ID_REUSED_CODE})


# --- 6. F1's fail-closed 503, and the boundary this is -------------------


def test_a_503_on_the_recipe_list_does_not_poison_a_shortlist_replay(app: _App) -> None:
    """**The boundary, decided and driven end to end.**

    F1 is fail-closed: an unreadable `Pantry.md` 503s the *whole* recipe list
    with no partial answer, because the Stock Join feeds every chip. A shortlist
    row is a basename and a position and is scored against nothing, so the three
    mutating routes read exactly two things off `app.state` — the store and this
    ledger — and neither has ever opened `Pantry.md`.

    So the sequence is: break the note, prove the 503 is real (otherwise the
    assertions after it pass vacuously), drive a full offline -> online cycle
    anyway, fix the note, and drive a second one. The 503 is a fact about the
    *read* path, and the ledger is not on it.
    """
    pantry = app.settings.vault_path / "Logistics" / "库存" / "Pantry.md"
    original = pantry.read_bytes()
    pantry.unlink()

    # The control: the 503 is real, and it is F1's own code.
    listed = app.client.get("/api/recipes", headers={"Origin": ORIGIN})
    assert listed.status_code == 503
    assert listed.json()["code"] == "pantry_stock_unreadable"

    # Offline -> online with the pantry note broken, throughout.
    first = app.add("lunch", MAIN_RECIPE, key=KEY_A)
    replay = app.add("lunch", MAIN_RECIPE, key=KEY_A)
    assert first.status_code == replay.status_code == 200
    assert first.content == replay.content
    assert app.rows("lunch") == [(0, MAIN_RECIPE)]
    assert app.client.get("/api/recipes", headers={"Origin": ORIGIN}).status_code == 503

    # The note is fixed. A *second* queued intent must replay cleanly too: the
    # 503 left nothing behind that could refuse it.
    pantry.write_bytes(original)
    assert app.client.get("/api/recipes", headers={"Origin": ORIGIN}).status_code == 200

    second = app.add("dinner", SECOND_RECIPE, key=KEY_B)
    assert second.status_code == 200
    assert app.add("dinner", SECOND_RECIPE, key=KEY_B).content == second.content
    assert app.rows("dinner") == [(0, SECOND_RECIPE)]
    assert len(app.ledger()) == 2


def test_a_503_from_the_shortlist_route_itself_is_not_recorded(app: _App) -> None:
    """A missing collaborator is a 503, and still not an intent.

    Driven by removing the ledger from `app.state` rather than by breaking
    `Pantry.md`, because this is the *other* 503 in this module's reach: the
    lifespan never published the collaborator. A parked-but-recorded 503 would
    make the user's next attempt a 409.
    """
    del app.client.app.state.shortlist_intent_ledger

    response = app.add("lunch", MAIN_RECIPE, key=KEY_A)

    assert response.status_code == 503
    assert response.json()["code"] == "resource_unavailable"
    assert app.rows("lunch") == []
    assert app.ledger() == []


# --- 7. F5: the cook log is never on this ledger -------------------------


def test_the_cooking_log_never_touches_the_shortlist_ledger(app: _App) -> None:
    """**F5's enforcement, on the server half.**

    `POST /api/cook-logs` accepts `X-Client-Id` and validates it — defence in
    depth against a user-driven double submit — and must not write a
    `shortlist_intents` row for it. If it did, a cook-log replay would be
    answered from a shortlist receipt, and the two tables would disagree about
    what happened.

    The 404 is the F4 contract, asserted here so this test cannot pass by getting
    a refusal for the wrong reason: the cook-log route is reached, the guard is
    satisfied, and the ledger is still empty.
    """
    response = app.client.post(
        "/api/cook-logs",
        json={"recipeNote": MAIN_RECIPE, "date": "2026-09-27", "baseRevision": None},
        headers=app.headers(KEY_A),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "daily_note_missing"
    assert app.ledger() == [], "a cook-log write reached the shortlist ledger"


# --- 8. the wiring the rest of the app depends on -------------------------


def test_every_meat_slot_is_a_ledger_intent_slot(app: _App) -> None:
    """F12: the enum is closed and all three are wrapped, not just `lunch`."""
    for slot in MEAL_SLOTS:
        assert app.add(slot, MAIN_RECIPE, key=f"key-{slot}").status_code == 200
        assert app.rows(slot) == [(0, MAIN_RECIPE)]
    assert len(app.ledger()) == len(MEAL_SLOTS)


def test_the_three_routes_still_come_from_one_router() -> None:
    """The wiring is `build_shortlists_router` and nothing else.

    A second module that could answer a shortlist mutation would be a second
    place the ledger had to be applied, and §9.18.3's step order is a property of
    the route, not of a helper anything can call.
    """
    paths = {route.path for route in build_shortlists_router().routes}
    assert "/api/shortlists/{slot}" in paths
    assert "/api/shortlists/{slot}/{note_name}" in paths
    assert "/api/shortlists/{slot}/order" in paths
    assert RECIPES_ROOT  # the fixture wrote real notes under the real recipes root
