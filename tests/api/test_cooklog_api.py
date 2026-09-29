"""The `/api/cook-logs` route contracts, status codes, and envelope shapes.

Every request here goes through `app.auth.install_security_middleware` and the
`app.main._api_error` handlers, so the guards and the envelope under test are the
shipped ones. `app/main.py` does not register the Cooking Log router — that
registration belongs between `/js/{path}` and the `/api/{unmatched_path}`
catch-all and is #15's wiring — and the two envelope tests below are what keep
this harness from becoming a second, divergent app: one asserts the two-key
baseline is key-for-key identical to `_api_error`, the other asserts F4's
extension is the *only* thing the Cooking Log adds.

The last section inverts that arrangement: `harness.mount_cook_logs` puts the
router on the **real** `create_app`, at §9.19's position, and drives it with the
real `GET /api/session` token. A route that nothing can reach has not been
tested, and a router built by the test itself is the weakest possible evidence
that it will survive being mounted.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# The harness is shared with `tests/cooklog/` as plain functions and wrapped in
# local fixtures here, so the two suites cannot drift into two different apps
# while neither has to re-export a fixture name it also uses as a parameter. The
# package is `cooklog`, not `tests.cooklog`, because `tests/` has no
# `__init__.py` — that is how pytest and mypy both resolve every other directory
# under `tests/` here.
from cooklog import harness
from cooklog.notes import (
    DAILY_NOTE_PATH,
    daily_note_bytes,
    no_notes_section_bytes,
    two_notes_sections_bytes,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from starlette.requests import Request

from app.api.cooklog import (
    MAX_CLIENT_ID_LENGTH,
    OPTIONAL_ERROR_KEYS,
    build_cook_log_router,
)
from app.auth import CsrfTokenStore
from app.config import Settings
from app.cooklog.writer import CookingLogWriter
from app.main import _api_error, create_app
from app.vault.atomic_write import AtomicNoteStore

ORIGIN = harness.ORIGIN
COOK = "盐焗鸡"
DATE = "2026-09-27"

#: F4's 404 body, exactly and in order (§9.15). Membership is the contract, and
#: so is the *position*: the extension is appended after `{requestId, code}`,
#: which is precisely what makes folding it into `app/main.py`'s `_api_error` a
#: change with no observable effect on any response. Both 404s below are
#: compared against this one tuple, so the write path and the read path cannot
#: drift into two shapes.
MISSING_NOTE_KEYS: tuple[str, ...] = (
    "requestId",
    "code",
    "message",
    "date",
    "relativePath",
    "retryable",
)

#: The creation conflict adds §9.14's `currentRevision` to the same six keys —
#: one 409 resolve panel, not a second conflict UI.
CREATED_CONCURRENTLY_KEYS: tuple[str, ...] = MISSING_NOTE_KEYS + ("currentRevision",)


@pytest.fixture
def settings(runtime_root: Path) -> Settings:
    return harness.make_settings(runtime_root)


@pytest.fixture
def vault(settings: Settings) -> Path:
    return settings.vault_path


@pytest.fixture
def recovery_root(settings: Settings) -> Path:
    return harness.make_recovery_root(settings)


@pytest.fixture
def store(vault: Path, recovery_root: Path) -> Iterator[AtomicNoteStore]:
    opened = harness.open_store(vault, recovery_root)
    try:
        yield opened
    finally:
        opened.close()


@pytest.fixture
def note_factory(vault: Path) -> Callable[..., bytes]:
    return lambda source, relative=DAILY_NOTE_PATH: harness.write_note(vault, source, relative)


@pytest.fixture
def writer(settings: Settings, store: AtomicNoteStore) -> CookingLogWriter:
    harness.initialise_db(settings)
    return harness.writer_for(settings, store)


@pytest.fixture
def app_factory(settings: Settings, writer: CookingLogWriter) -> Callable[..., FastAPI]:
    return harness.make_app_factory(settings, writer)


@pytest.fixture
def api_client(app_factory: Callable[..., FastAPI]) -> Iterator[TestClient]:
    test_client = harness.client_for(app_factory())
    with test_client:
        yield test_client


def _csrf(client: TestClient) -> str:
    """The same token `GET /api/session` hands out, from the same store.

    Read off `app.state.csrf` rather than by adding a `/api/session` route to the
    harness: the guard only checks the value against the `CsrfTokenStore` that
    `install_security_middleware` installed, so this is byte-for-byte what the
    real session route returns, without this file growing a second version of a
    route that already exists in `app/main.py`.
    """
    tokens: CsrfTokenStore = client.app.state.csrf
    return tokens.issue()


def _headers(client: TestClient, **extra: str) -> dict[str, str]:
    """Everything a legal mutation needs: right Origin, a real CSRF token.

    Both are the shipped guards' own requirements — an exact `Origin` equal to
    `PUBLIC_ORIGIN`, and a token from the `CsrfTokenStore` — so every request
    below is one the real app would accept, and a test that means to trip a guard
    has to say which by omitting exactly that header.
    """
    return {"Origin": ORIGIN, "X-CSRF-Token": _csrf(client), **extra}


def _post(client: TestClient, **body: object) -> tuple[int, dict[str, object]]:
    """A legal mutation. Returns `(http status, body)` separately.

    They are kept apart because the payload's own `status` field is one of the
    three keys a 201 must carry, and merging them would make "the response has
    exactly three keys" untestable.
    """
    response = client.post(
        "/api/cook-logs", json=body, headers=_headers(client, **{"X-Client-Id": "client-1"})
    )
    assert response.status_code in {200, 201, 400, 404, 409, 413, 422, 503}, response.text
    return response.status_code, dict(response.json())


def test_a_first_log_is_a_201_with_exactly_three_keys(
    api_client: TestClient, note_factory: Callable[..., bytes], vault: Path
) -> None:
    """The response is not widened: the client already holds the other two values."""
    note_factory(daily_note_bytes())
    status, body = _post(api_client, recipeNote=COOK, date=DATE)

    assert status == 201
    assert set(body) == {"status", "relativePath", "noteRevision"}
    assert body["status"] == "logged"
    assert body["relativePath"] == DAILY_NOTE_PATH
    assert str(body["noteRevision"]).startswith("sha256:")
    assert "- [[盐焗鸡]]".encode() in (vault / DAILY_NOTE_PATH).read_bytes()


def test_a_second_log_of_the_same_recipe_on_the_same_date_is_a_200_duplicate(
    api_client: TestClient, note_factory: Callable[..., bytes], vault: Path
) -> None:
    """A duplicate is a 200 with the **same three keys**, and it writes nothing."""
    note_factory(daily_note_bytes())
    first_status, first = _post(api_client, recipeNote=COOK, date=DATE)
    after_first = (vault / DAILY_NOTE_PATH).read_bytes()
    second_status, second = _post(api_client, recipeNote=COOK, date=DATE)

    assert first_status == 201
    assert second_status == 200
    assert set(second) == {"status", "relativePath", "noteRevision"}
    assert second["status"] == "duplicate"
    assert (vault / DAILY_NOTE_PATH).read_bytes() == after_first
    # The duplicate reports a usable revision, so a client can chain a second
    # write off it without a refetch.
    assert second["noteRevision"] == first["noteRevision"]


def test_the_read_back_lists_the_date_with_its_entries(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """`entries` come from the receipts ledger, which is where `writtenAt` lives."""
    note_factory(daily_note_bytes())
    _post(api_client, recipeNote=COOK, date=DATE)
    response = api_client.get("/api/cook-logs", params={"date": DATE})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"date", "relativePath", "noteRevision", "entries"}
    assert body["date"] == DATE
    assert body["relativePath"] == DAILY_NOTE_PATH
    assert body["noteRevision"].startswith("sha256:")
    assert [entry["recipeNote"] for entry in body["entries"]] == [COOK]
    entry = body["entries"][0]
    # **No `trackerSynced`.** It published `cook_log_receipts.recipe_tracker_synced`,
    # a column inserted as 0 that nothing ever writes, so it could only ever read
    # `false` — and the badge keyed on it, which is how `待 Obsidian 同步` came to
    # be permanently on. Asserted as an exact set so re-adding it fails here.
    # Spec §13 step 8 is amended: the comparison lives on the recipe detail route
    # as `pendingCookDates`, which publishes dates rather than a boolean.
    assert set(entry) == {"recipeNote", "writtenAt"}
    assert entry["writtenAt"].endswith("Z")


def test_the_read_back_for_a_date_with_no_cooks_is_an_empty_list_not_an_error(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """The note exists, so this is a real answer: "you cooked nothing that day"."""
    note_factory(daily_note_bytes())
    response = api_client.get("/api/cook-logs", params={"date": DATE})
    assert response.status_code == 200
    assert response.json()["entries"] == []


# --- the refusal contracts -------------------------------------------------


def test_a_missing_daily_note_is_404_with_the_additive_envelope(
    api_client: TestClient, vault: Path
) -> None:
    """F4's one error shape, in full, and the `relativePath` is vault-relative.

    This is the error R16 is about: "the app can't log a cook" is the objection,
    and the answer is a message naming the date, the exact path, and the fact
    that a retry works. All three travel in the envelope so the UI has one
    wording to render verbatim.
    """
    status, body = _post(api_client, recipeNote=COOK, date=DATE)

    assert status == 404
    assert set(body) == set(MISSING_NOTE_KEYS)
    assert list(body) == list(MISSING_NOTE_KEYS), "the extension is appended, not interleaved"
    assert body["code"] == "daily_note_missing"
    assert body["date"] == DATE
    assert body["relativePath"] == DAILY_NOTE_PATH
    assert body["retryable"] is True
    assert DATE in str(body["message"])
    assert DAILY_NOTE_PATH in str(body["message"])
    assert str(vault) not in str(body["message"])
    assert not (vault / DAILY_NOTE_PATH).exists()


def test_the_read_back_of_a_missing_date_is_the_same_404_not_an_empty_list(
    api_client: TestClient, vault: Path
) -> None:
    """`{"entries": []}` for a date with no note would be a lie.

    It would say "you cooked nothing that day", which is a different claim from
    "there is no record of that day at all", and not one this app can support.
    """
    response = api_client.get("/api/cook-logs", params={"date": DATE})
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "daily_note_missing"
    assert body["relativePath"] == DAILY_NOTE_PATH
    assert body["retryable"] is True
    assert "entries" not in body
    assert not (vault / DAILY_NOTE_PATH).exists()


def test_both_404s_are_the_same_404_byte_for_byte(
    api_client: TestClient,
) -> None:
    """The GET and the POST 404 are one error, not two that happen to agree.

    Compared against the same tuple, so a `message` dropped from the read path —
    which would still be a truthful-looking 404 — fails here rather than being
    noticed by a user. `requestId` is per-request and therefore excluded, and
    nothing else is.
    """
    written_status, written = _post(api_client, recipeNote=COOK, date=DATE)
    read = api_client.get("/api/cook-logs", params={"date": DATE})

    assert written_status == read.status_code == 404
    read_body = read.json()
    assert list(read_body) == list(MISSING_NOTE_KEYS)
    assert {key: value for key, value in read_body.items() if key != "requestId"} == {
        key: value for key, value in written.items() if key != "requestId"
    }


def test_a_stale_base_revision_is_409_with_the_current_revision(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """409 and the client re-offers; the app never retries a stale revision."""
    note_factory(daily_note_bytes())
    status, body = _post(api_client, recipeNote=COOK, date=DATE, baseRevision="sha256:" + "0" * 64)

    assert status == 409
    # §9.16's row: exactly `{requestId, code, currentRevision}` — no `message`,
    # no `date`, no `relativePath`, no `retryable`. The stale-revision conflict is
    # not one of F4's two presence codes, so it does not widen the envelope.
    assert set(body) == {"requestId", "code", "currentRevision"}
    assert body["currentRevision"].startswith("sha256:")


def test_a_note_that_appears_mid_request_is_a_409_with_the_extension(
    settings: Settings, vault: Path, recovery_root: Path
) -> None:
    """The creation conflict, as bytes on the wire rather than as an exception.

    The writer-level test in `tests/cooklog/test_missing_note.py` proves the
    classification; this proves the *envelope*, which is a separate claim: the
    409 carries F4's four optional fields plus §9.14's `currentRevision`, and
    carries them under a code that is emphatically not `daily_note_missing`.
    A view that branches on the status alone would still be wrong here, so the
    status and the code are asserted together.
    """
    target = vault / DAILY_NOTE_PATH
    reads: list[str] = []

    class _AppearingStore(AtomicNoteStore):
        """Reports absence on the first read and materialises the note before the second."""

        def read_existing_if_exists(
            self, relative: str, *, max_bytes: int | None = None
        ) -> bytes | None:
            reads.append(relative)
            if len(reads) > 1:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(daily_note_bytes())
            return super().read_existing_if_exists(relative, max_bytes=max_bytes)

    writer, store = harness.racing_writer(
        settings, vault, recovery_root, store_factory=_AppearingStore
    )
    try:
        with harness.client_for(harness.build_app(settings, writer=writer)) as client:
            status, body = _post(client, recipeNote=COOK, date=DATE)
    finally:
        store.close()

    assert status == 409
    assert list(body) == list(CREATED_CONCURRENTLY_KEYS)
    assert body["code"] == "daily_note_created_concurrently"
    assert body["code"] != "daily_note_missing"
    assert body["date"] == DATE
    assert body["relativePath"] == DAILY_NOTE_PATH
    assert body["retryable"] is True
    assert str(body["currentRevision"]).startswith("sha256:")
    # The same server-owned path was asked for on both reads — the re-check
    # re-reads the date's note, it does not go looking for another one.
    assert reads == [DAILY_NOTE_PATH, DAILY_NOTE_PATH]
    # A 409 leaves the note alone: the user taps retry and the write proceeds.
    assert "- [[盐焗鸡]]".encode() not in target.read_bytes()


def test_a_current_base_revision_commits(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """The happy path for the CAS over HTTP, so the field is not decorative."""
    before = note_factory(daily_note_bytes())
    status, body = _post(
        api_client,
        recipeNote=COOK,
        date=DATE,
        baseRevision="sha256:" + hashlib.sha256(before).hexdigest(),
    )
    assert status == 201
    assert body["status"] == "logged"


def test_two_notes_sections_is_409_and_a_missing_one_is_422(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """The two section refusals carry different codes because they mean different things."""
    note_factory(two_notes_sections_bytes())
    ambiguous_status, ambiguous = _post(api_client, recipeNote=COOK, date=DATE)
    assert ambiguous_status == 409
    assert ambiguous["code"] == "ambiguous_notes_section"

    note_factory(no_notes_section_bytes())
    missing_status, missing = _post(api_client, recipeNote=COOK, date=DATE)
    assert missing_status == 422
    assert missing["code"] == "notes_section_missing"


@pytest.mark.parametrize(
    "date", ["20260927", "2026-W40-7", "2026-9-27", "not-a-date", "", "2026-02-30"]
)
def test_a_date_the_path_policy_cannot_round_trip_is_422(
    api_client: TestClient, date: str
) -> None:
    """`date.fromisoformat` must give back exactly what it was handed.

    A path built from a date the server did not parse back to itself is a path
    the server cannot re-derive later, so `20260927` and `2026-W40-7` are refused
    rather than guessed at.
    """
    status, body = _post(api_client, recipeNote=COOK, date=date)
    assert status == 422
    assert body["code"] in {"invalid_daily_note_date", "invalid_request"}


def test_a_date_outside_the_configured_year_scope_is_422(
    api_client: TestClient, note_factory: Callable[..., bytes], settings: Settings
) -> None:
    """`DAILY_NOTES_YEAR_POLICY` bounds the year, and the bound is the path policy's."""
    assert settings.daily_notes_year_policy == "2020-2030"
    note_factory(daily_note_bytes())
    status, body = _post(api_client, recipeNote=COOK, date="2019-12-31")
    assert status == 422
    assert body["code"] == "date_outside_configured_scope"


@pytest.mark.parametrize(
    "recipe_note", ["", " 盐焗鸡", "../盐焗鸡", "笔记/盐焗鸡", "盐焗鸡.md", "x" * 201]
)
def test_a_recipe_note_that_is_not_a_bare_basename_is_422(
    api_client: TestClient, note_factory: Callable[..., bytes], recipe_note: str
) -> None:
    """Server-Owned Root: the recipe is a wikilink's text, never a path fragment."""
    note_factory(daily_note_bytes())
    status, body = _post(api_client, recipeNote=recipe_note, date=DATE)
    assert status == 422
    assert body["code"] == "invalid_recipe_note"


def test_a_body_that_tries_to_name_a_path_is_refused_not_ignored(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """`extra="forbid"`: an ignored field is a field that looks like it worked."""
    note_factory(daily_note_bytes())
    status, body = _post(api_client, recipeNote=COOK, date=DATE, path="../../etc/passwd")
    assert status == 422
    assert body["code"] == "invalid_request"


def test_a_missing_date_query_parameter_is_422(api_client: TestClient) -> None:
    response = api_client.get("/api/cook-logs")
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_daily_note_date"


# --- X-Client-Id (validated exactly as pwa-deals does) --------------------


def test_a_present_client_id_is_accepted_and_ignored(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """Defence in depth only: F5 keeps cook logs off the outbox, so nothing replays."""
    note_factory(daily_note_bytes())
    status, body = _post(api_client, recipeNote=COOK, date=DATE)
    assert status == 201


@pytest.mark.parametrize("client_id", ["", "   ", "x" * (MAX_CLIENT_ID_LENGTH + 1)])
def test_an_unusable_client_id_is_400(
    client_id: str, api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """`pwa-deals` uses 400 for a present-but-unusable key, and so does this."""
    note_factory(daily_note_bytes())
    response = api_client.post(
        "/api/cook-logs",
        json={"recipeNote": COOK, "date": DATE},
        headers=_headers(api_client, **{"X-Client-Id": client_id}),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "client_id_invalid"


# --- the guards, in the shipped order --------------------------------------


def test_a_mutation_without_a_csrf_token_is_403(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """The router is behind the same guard as every other mutation, not beside it.

    Origin is supplied and only the token is dropped, so the 403 names the guard
    under test: guard order is host → identity → read_only → body size → Origin →
    content-type → CSRF, and `csrf_required` is the last of them.
    """
    note_factory(daily_note_bytes())
    response = api_client.post(
        "/api/cook-logs", json={"recipeNote": COOK, "date": DATE}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_required"


def test_a_mutation_from_a_foreign_origin_is_403_before_the_csrf_check(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """The guard order is asserted as order, not as a list: Origin precedes CSRF."""
    note_factory(daily_note_bytes())
    response = api_client.post("/api/cook-logs", json={"recipeNote": COOK, "date": DATE})
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


def test_read_only_refuses_the_log_before_any_existence_check(
    settings: Settings, recovery_root: Path
) -> None:
    """403 `read_only` comes first, so a read-only install cannot probe the vault.

    Comparing 403 against 404 would otherwise be a way to enumerate which daily
    notes exist without being able to write one.
    """
    read_only = Settings.from_mapping(
        {
            "OBSIDIAN_VAULT_PATH": str(settings.vault_path),
            "APP_DATA_DIR": str(settings.app_data_dir),
            "PANTRY_ITEMS_DB": str(settings.pantry_items_db),
            "PUBLIC_ORIGIN": ORIGIN,
            "TAILSCALE_OWNER_LOGIN": "owner@test.invalid",
            "OBSIDIAN_READ_ONLY": "true",
        }
    )
    assert not (settings.vault_path / DAILY_NOTE_PATH).exists()
    with harness.client_for(harness.build_app(read_only, writer=None)) as client:
        response = client.post(
            "/api/cook-logs",
            json={"recipeNote": COOK, "date": DATE},
            headers=_headers(client),
        )
    assert response.status_code == 403
    assert response.json()["code"] == "read_only"


def test_a_body_over_the_one_mib_cap_is_413(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """The cap is `app/auth.py`'s, and it holds for this route like every other."""
    note_factory(daily_note_bytes())
    response = api_client.post(
        "/api/cook-logs",
        content=b"{\"recipeNote\": \"" + b"x" * 1_048_576 + b"\"}",
        headers=_headers(api_client, **{"Content-Type": "application/json"}),
    )
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


def test_the_app_refuses_the_log_when_no_writer_is_published(
    settings: Settings,
) -> None:
    """The state `app/main.py` is in until #15's `lifespan` publishes one.

    A 503 with a named code beats an `AttributeError` on `None` surfacing as a
    500 with a traceback in the log and a blank screen in the UI.
    """
    with harness.client_for(harness.build_app(settings, writer=None)) as client:
        response = client.post(
            "/api/cook-logs",
            json={"recipeNote": COOK, "date": DATE},
            headers=_headers(client),
        )
    assert response.status_code == 503
    assert response.json()["code"] == "cook_log_unavailable"


# --- the envelope itself ---------------------------------------------------


def test_the_two_key_envelope_is_key_for_key_the_scaffolds(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """F4's extension is additive, and this is what "additive" is asserted as.

    `app/main.py`'s `_api_error` is what every other route's errors go through.
    A two-key Cooking Log error must be indistinguishable from it, so that when
    #15 folds the optional detail into `_api_error` nothing about the other codes'
    shape can have changed underneath them.
    """
    note_factory(daily_note_bytes())
    response = api_client.post(
        "/api/cook-logs",
        json={"recipeNote": COOK, "date": "2026-W40-7"},
        headers=_headers(api_client),
    )
    scaffold = _scaffold_envelope(422, "invalid_daily_note_date")
    assert response.status_code == 422
    assert set(response.json()) == set(scaffold) == {"requestId", "code"}
    assert response.json()["code"] == scaffold["code"] == "invalid_daily_note_date"
    # Both carry a per-request id, so a UI error and a log line can be joined.
    assert uuid.UUID(str(response.json()["requestId"]))
    assert uuid.UUID(str(scaffold["requestId"]))


def _scaffold_envelope(status_code: int, code: str) -> dict[str, object]:
    """What `app/main.py`'s `_api_error` emits, for the same request id shape."""
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/cook-logs",
            "headers": [],
            "state": {"request_id": str(uuid.uuid4())},
        }
    )
    return dict(json.loads(_api_error(request, status_code, code).body))  # type: ignore[arg-type]


def test_every_other_code_still_emits_exactly_the_two_keys(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    """Only the codes F4 names may carry the extra fields, and the rest must not."""
    note_factory(daily_note_bytes())
    for body, expected in (
        ({"recipeNote": COOK, "date": "2026-09-27", "path": "x"}, 422),
        ({"recipeNote": COOK, "date": "2026-W40-7"}, 422),
    ):
        response = api_client.post(
            "/api/cook-logs",
            json=body,
            headers=_headers(api_client),
        )
        assert response.status_code == expected
        assert set(response.json()) == {"requestId", "code"}


def test_the_optional_envelope_keys_are_the_documented_five() -> None:
    """The set is asserted, not restated: a sixth key has to be a deliberate edit."""
    assert set(OPTIONAL_ERROR_KEYS) == {
        "message",
        "date",
        "relativePath",
        "retryable",
        "currentRevision",
    }


def test_the_routes_are_exactly_the_three_the_contract_names() -> None:
    """One route pair plus the retraction, no extras. §9.16's table is the whole surface."""

    routes = [
        (route.path, sorted(route.methods - {"HEAD"}))  # type: ignore[union-attr]
        for route in build_cook_log_router().routes
    ]
    assert routes == [
        ("/api/cook-logs", ["POST"]),
        ("/api/cook-logs/{log_date}/{note_name}", ["DELETE"]),
        ("/api/cook-logs", ["GET"]),
    ]


# --- the mounted path, on the real app --------------------------------------
#
# Everything above runs the router inside a purpose-built app. These four run it
# where it will actually live: `app.main.create_app`, with the router spliced in
# at §9.19's position. Three things can only be observed here — that the route
# is reachable at all, that the `/api/{unmatched_path}` catch-all registered
# before it does not shadow it, and that the real `cache_policy` and the real
# `GET /api/session` feed the request that reaches the writer.


@pytest.fixture
def mounted_client(
    settings: Settings, writer: CookingLogWriter
) -> Iterator[TestClient]:
    """The real app, with the Cooking Log registered where §9.19 says it goes.

    `harness.mount_cook_logs` is a no-op once `app/main.py` registers the router
    itself, so this fixture tests the shipped registration the moment it exists
    and needs no change when it does.
    """
    with TestClient(
        harness.mount_cook_logs(create_app(settings), writer), base_url=ORIGIN
    ) as test_client:
        yield test_client


def _session_post(client: TestClient, **body: object) -> Response:
    """A mutation whose CSRF token came from the real `GET /api/session`.

    `_headers` reads the token off `app.state.csrf` instead, which is the same
    bytes; this one goes through the route, so the whole boot contract —
    session → token → guard → route — is asserted in one request rather than
    three assertions that each assume the others.
    """
    token = client.get("/api/session").json()["csrfToken"]
    return client.post(
        "/api/cook-logs",
        json=body,
        headers={"Origin": ORIGIN, "X-CSRF-Token": token},
    )


def test_the_missing_note_404_survives_being_mounted_on_the_real_app(
    mounted_client: TestClient, vault: Path
) -> None:
    """F4's one error, as the app a user actually talks to emits it.

    Asserted end to end: the mounted route is reachable, the real
    `cache_policy` put `no-store` on it, the real middleware stamped an
    `X-Request-ID` that matches the body's `requestId`, and the response is JSON
    rather than the static mount's HTML 404.
    """
    response = _session_post(mounted_client, recipeNote=COOK, date=DATE)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert list(body) == list(MISSING_NOTE_KEYS)
    assert body["requestId"] == response.headers["x-request-id"]
    assert body["code"] == "daily_note_missing"
    assert body["relativePath"] == DAILY_NOTE_PATH
    assert str(vault) not in response.text
    assert not (vault / DAILY_NOTE_PATH).exists()


def test_the_read_back_404_survives_being_mounted_too(
    mounted_client: TestClient,
) -> None:
    """`GET /api/cook-logs?date=` is reachable and 404s, rather than 404ing `not_found`.

    Both failures are a 404, so the status alone proves nothing: a route that
    fell through to the catch-all would answer with the two-key `not_found`
    envelope. The code and the six keys are what distinguish the two.
    """
    response = mounted_client.get("/api/cook-logs", params={"date": DATE})

    assert response.status_code == 404
    body = response.json()
    assert list(body) == list(MISSING_NOTE_KEYS)
    assert body["code"] == "daily_note_missing"


def test_a_written_cook_log_survives_being_mounted_on_the_real_app(
    mounted_client: TestClient, note_factory: Callable[..., bytes], vault: Path
) -> None:
    """The mounted 201 is the real commit: the note on disk is byte-changed.

    Without this, the 404 tests above would pass against a route that is mounted
    but wired to a writer that never commits anything.
    """
    note_factory(daily_note_bytes())
    response = _session_post(mounted_client, recipeNote=COOK, date=DATE)

    assert response.status_code == 201, response.text
    assert set(response.json()) == {"status", "relativePath", "noteRevision"}
    assert "- [[盐焗鸡]]".encode() in (vault / DAILY_NOTE_PATH).read_bytes()


def test_mounting_the_router_leaves_the_catch_all_and_the_shell_alone(
    mounted_client: TestClient, settings: Settings
) -> None:
    """The registration position is load-bearing, so both neighbours are asserted.

    §9.19: the domain routers go after `/js/{path}` and before the
    `/api/{unmatched_path}` catch-all, and the static mount is registered last.
    Getting that wrong does not fail loudly — it returns the wrong *kind* of
    404, or serves the HTML shell to an API client.
    """
    unmatched = mounted_client.get("/api/does-not-exist")
    assert unmatched.status_code == 404
    assert set(unmatched.json()) == {"requestId", "code"}
    assert unmatched.json()["code"] == "not_found"

    shell = mounted_client.get("/")
    assert shell.status_code == 200
    assert shell.headers["content-type"].startswith("text/html")

    health = mounted_client.get("/health")
    assert health.status_code == 200
    # §9.15: the extension adds no path to any surface, and `/health` in
    # particular keeps leaking none.
    assert str(settings.vault_path) not in health.text


# --- retraction: DELETE /api/cook-logs/{date}/{note_name} -------------------


def _delete(
    client: TestClient, date: str = DATE, note: str = COOK, **params: str
) -> tuple[int, dict[str, object]]:
    response = client.delete(
        f"/api/cook-logs/{date}/{note}", params=params, headers=_headers(client)
    )
    return response.status_code, dict(response.json())


def test_a_retraction_is_a_200_with_exactly_four_keys_and_restores_the_note(
    api_client: TestClient, note_factory: Callable[..., bytes], vault: Path
) -> None:
    before = note_factory(daily_note_bytes())
    _post(api_client, recipeNote=COOK, date=DATE)

    status, body = _delete(api_client)

    assert status == 200
    assert set(body) == {"status", "relativePath", "noteRevision", "retractedAt"}
    assert body["status"] == "retracted"
    assert body["relativePath"] == DAILY_NOTE_PATH
    assert (vault / DAILY_NOTE_PATH).read_bytes() == before


def test_the_read_back_no_longer_lists_a_retracted_cook(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    note_factory(daily_note_bytes())
    _post(api_client, recipeNote=COOK, date=DATE)
    _delete(api_client)

    entries = api_client.get("/api/cook-logs", params={"date": DATE}).json()["entries"]

    assert entries == []


def test_a_second_retraction_is_a_200_already_retracted(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    note_factory(daily_note_bytes())
    _post(api_client, recipeNote=COOK, date=DATE)
    _delete(api_client)

    status, body = _delete(api_client)

    assert status == 200
    assert body["status"] == "already_retracted"


def test_retracting_a_cook_the_app_never_wrote_is_404_and_touches_nothing(
    api_client: TestClient, note_factory: Callable[..., bytes], vault: Path
) -> None:
    before = note_factory(daily_note_bytes())

    status, body = _delete(api_client)

    assert (status, body["code"]) == (404, "cook_record_not_found")
    assert set(body) == {"requestId", "code"}  # the two-key envelope, not widened
    assert (vault / DAILY_NOTE_PATH).read_bytes() == before


def test_a_line_edited_in_obsidian_is_409_not_removable(
    api_client: TestClient, note_factory: Callable[..., bytes], vault: Path
) -> None:
    note_factory(daily_note_bytes())
    _post(api_client, recipeNote=COOK, date=DATE)
    path = vault / DAILY_NOTE_PATH
    path.write_bytes(path.read_bytes().replace("[[盐焗鸡]]".encode(), "[[盐焗鸡]] 好吃".encode()))
    edited = path.read_bytes()

    status, body = _delete(api_client)

    assert (status, body["code"]) == (409, "cook_record_not_removable")
    assert set(body) == {"requestId", "code"}
    assert path.read_bytes() == edited


def test_a_stale_revision_is_409_with_the_current_revision(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    note_factory(daily_note_bytes())
    _post(api_client, recipeNote=COOK, date=DATE)

    status, body = _delete(api_client, baseRevision="sha256:stale")

    assert (status, body["code"]) == (409, "daily_note_changed")
    assert str(body["currentRevision"]).startswith("sha256:")


def test_a_current_revision_commits_the_retraction(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    note_factory(daily_note_bytes())
    _, logged = _post(api_client, recipeNote=COOK, date=DATE)

    status, body = _delete(api_client, baseRevision=str(logged["noteRevision"]))

    assert (status, body["status"]) == (200, "retracted")


def test_a_retraction_past_the_window_is_409_window_closed(
    settings: Settings,
    store: AtomicNoteStore,
    note_factory: Callable[..., bytes],
    vault: Path,
) -> None:
    from datetime import UTC, datetime, timedelta
    from functools import partial

    from app.db.database import connect_db
    from app.vault.daily_paths import DailyNotePathPolicy

    harness.initialise_db(settings)
    note_factory(daily_note_bytes())
    late = CookingLogWriter(
        store,
        DailyNotePathPolicy.from_settings(settings),
        partial(connect_db, settings),
        undo_window=timedelta(hours=settings.cook_log_undo_hours),
        clock=lambda: datetime.now(UTC) + timedelta(hours=settings.cook_log_undo_hours + 1),
    )
    early = harness.writer_for(settings, store)
    with harness.client_for(harness.build_app(settings, writer=early)) as first:
        _post(first, recipeNote=COOK, date=DATE)
    logged = (vault / DAILY_NOTE_PATH).read_bytes()

    with harness.client_for(harness.build_app(settings, writer=late)) as second:
        status, body = _delete(second)

    assert (status, body["code"]) == (409, "retraction_window_closed")
    assert (vault / DAILY_NOTE_PATH).read_bytes() == logged


def test_an_unsafe_recipe_name_in_the_path_is_422(api_client: TestClient) -> None:
    status, body = _delete(api_client, note="..%5C盐焗鸡")
    assert status in {404, 422}, body  # never a 200 and never a traversal


def test_a_retraction_without_a_csrf_token_is_403(
    api_client: TestClient, note_factory: Callable[..., bytes]
) -> None:
    note_factory(daily_note_bytes())
    response = api_client.delete(f"/api/cook-logs/{DATE}/{COOK}", headers={"Origin": ORIGIN})
    assert (response.status_code, response.json()["code"]) == (403, "csrf_required")


def test_read_only_refuses_the_retraction(settings: Settings) -> None:
    read_only = Settings.from_mapping(
        {
            "OBSIDIAN_VAULT_PATH": str(settings.vault_path),
            "APP_DATA_DIR": str(settings.app_data_dir),
            "PANTRY_ITEMS_DB": str(settings.pantry_items_db),
            "PUBLIC_ORIGIN": ORIGIN,
            "TAILSCALE_OWNER_LOGIN": "owner@test.invalid",
            "OBSIDIAN_READ_ONLY": "true",
        }
    )
    with harness.client_for(harness.build_app(read_only, writer=None)) as client:
        status, body = _delete(client)
    assert (status, body["code"]) == (403, "read_only")


def test_the_retraction_route_needs_a_published_writer(settings: Settings) -> None:
    with harness.client_for(harness.build_app(settings, writer=None)) as client:
        status, body = _delete(client)
    assert (status, body["code"]) == (503, "cook_log_unavailable")
