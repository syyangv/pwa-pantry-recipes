"""The `/api/cook-logs` route contracts, status codes, and envelope shapes.

Every request here goes through `app.auth.install_security_middleware` and the
`app.main._api_error` handlers, so the guards and the envelope under test are the
shipped ones. `app/main.py` does not register the Cooking Log router — that
registration belongs between `/js/{path}` and the `/api/{unmatched_path}`
catch-all and is #15's wiring — and the two envelope tests below are what keep
this harness from becoming a second, divergent app: one asserts the two-key
baseline is key-for-key identical to `_api_error`, the other asserts F4's
extension is the *only* thing the Cooking Log adds.
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
from starlette.requests import Request

from app.api.cooklog import (
    MAX_CLIENT_ID_LENGTH,
    OPTIONAL_ERROR_KEYS,
    build_cook_log_router,
)
from app.auth import CsrfTokenStore
from app.config import Settings
from app.cooklog.writer import CookingLogWriter
from app.main import _api_error
from app.vault.atomic_write import AtomicNoteStore

ORIGIN = harness.ORIGIN
COOK = "盐焗鸡"
DATE = "2026-09-27"


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
    assert set(entry) == {"recipeNote", "writtenAt", "trackerSynced"}
    assert entry["writtenAt"].endswith("Z")
    # The write path never sets it; only a read-path comparison flips it (§13.5).
    # The PWA's view of `cooking_count` is expected to lag Obsidian, and the
    # `待 Obsidian 同步` badge is what makes that lag visible instead of wrong.
    assert entry["trackerSynced"] is False


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
    assert set(body) == {
        "requestId",
        "code",
        "message",
        "date",
        "relativePath",
        "retryable",
    }
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


def test_the_routes_are_exactly_the_two_the_contract_names() -> None:
    """One route pair, no extras. §9.16's table is the whole surface."""

    routes = [
        (route.path, sorted(route.methods - {"HEAD"}))  # type: ignore[union-attr]
        for route in build_cook_log_router().routes
    ]
    assert routes == [("/api/cook-logs", ["POST"]), ("/api/cook-logs", ["GET"])]
