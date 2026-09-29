"""Retracting a Cooking Record: the inverse of the append, and its refusals.

The append is a byte splice of exactly one line, so the retraction is the same
splice run backwards, and the property that matters is the round trip: log, then
retract, and the note is byte-for-byte what it was. Everything else here is a
refusal, because a retraction deletes from a file the user also edits and the
only safe behaviour on any doubt is to leave the note alone and say why.

The groups kill specific mutants:

* **round trip** kills a retract that re-dumps the note, or eats a blank line;
* **ownership** kills a retract that deletes a link the app did not write — the
  hand-typed `[[盐焗鸡]]` with no receipt must survive;
* **not removable** kills a retract that guesses when the line was edited or
  appears twice;
* **window** kills a retract with no time limit, and one measured from the wrong
  instant;
* **convergence** kills a retract that leaves the ledger and the note disagreeing
  after a failure or a double tap.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import pytest

from app.config import Settings
from app.cooklog.writer import (
    CookingLogWriter,
    CookLogReceiptsUnavailable,
    CookRecordNotFound,
    CookRecordNotRemovable,
    DailyNoteChanged,
    InvalidRequest,
    RetractionWindowClosed,
    cook_link_line,
    note_revision,
    remove_cook_link,
)
from app.db.database import connect_db
from app.vault.atomic_write import AtomicNoteStore
from app.vault.daily_paths import DailyNotePathPolicy

from .notes import (
    DAILY_NOTE_PATH,
    daily_note_bytes,
    linked_outside_the_region_bytes,
    populated_note_bytes,
)

pytestmark = pytest.mark.anyio

COOK = "盐焗鸡"
DATE = "2026-09-27"


def _writer_at(
    settings: Settings, store: AtomicNoteStore, clock: Callable[[], datetime]
) -> CookingLogWriter:
    return CookingLogWriter(
        store,
        DailyNotePathPolicy.from_settings(settings),
        partial(connect_db, settings),
        undo_window=timedelta(hours=settings.cook_log_undo_hours),
        clock=clock,
    )


async def _receipts(settings: Settings) -> list[tuple[str, str, str | None]]:
    async with connect_db(settings) as conn:
        rows = await (
            await conn.execute(
                "SELECT recipe_note, log_date, retracted_at FROM cook_log_receipts ORDER BY id"
            )
        ).fetchall()
    return [(str(r[0]), str(r[1]), r[2]) for r in rows]


# --- the pure splice -------------------------------------------------------


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"], ids=["LF", "CRLF", "lone CR"])
def test_remove_is_the_exact_inverse_of_the_append_on_every_terminator(newline: bytes) -> None:
    """The line and its OWN terminator go, so no terminator style is disturbed."""
    from app.cooklog.writer import _locate_notes_section, append_cook_link

    before = populated_note_bytes(newline)
    appended = append_cook_link(before, _locate_notes_section(before), cook_link_line(COOK))
    assert appended != before
    assert remove_cook_link(appended, COOK) == before


def test_remove_reports_a_note_that_no_longer_links_the_recipe_as_absent() -> None:
    assert remove_cook_link(daily_note_bytes(), COOK) is None


@pytest.mark.parametrize(
    "line",
    [
        "- [[盐焗鸡]] 好吃",  # the user added text to the line
        "- [[盐焗鸡|盐焗鸡]]",  # the alias form: not the bare line the app writes
        "* [[盐焗鸡]]",  # a different list marker
        "- [ ] [[盐焗鸡]]",  # a task, which the app never writes
        "  - [[盐焗鸡]]",  # indented
    ],
)
def test_remove_refuses_a_link_that_is_not_the_exact_line_the_app_writes(line: str) -> None:
    source = daily_note_bytes().replace("# 笔记\n".encode(), f"# 笔记\n{line}\n".encode(), 1)
    with pytest.raises(CookRecordNotRemovable):
        remove_cook_link(source, COOK)


def test_remove_refuses_when_the_recipe_is_linked_twice() -> None:
    """Two links: which one is the app's is a guess, and a guess deletes history."""
    twice = "# 笔记\n- [[盐焗鸡]]\n- [[盐焗鸡]]\n"
    source = daily_note_bytes().replace("# 笔记\n".encode(), twice.encode(), 1)
    with pytest.raises(CookRecordNotRemovable):
        remove_cook_link(source, COOK)


def test_remove_refuses_when_another_form_of_link_would_keep_the_page_counted() -> None:
    """The bare line plus a folder-qualified link to the same recipe elsewhere.

    Removing the app's line would leave the note linking the recipe, so the
    tracker (which counts pages) would still count it and the "retraction" would
    change nothing the user can see. Refusing is the honest answer.
    """
    source = daily_note_bytes().replace(
        "# 笔记\n".encode(),
        "# 笔记\n- [[盐焗鸡]]\n见 [[Hobbies/做饭/Recipes/盐焗鸡]]\n".encode(),
        1,
    )
    with pytest.raises(CookRecordNotRemovable):
        remove_cook_link(source, COOK)


def test_remove_does_not_match_a_longer_recipe_name() -> None:
    """`[[盐焗鸡翅]]` is another recipe; removing 盐焗鸡 must not touch it."""
    source = daily_note_bytes().replace("# 笔记\n".encode(), "# 笔记\n- [[盐焗鸡翅]]\n".encode(), 1)
    assert remove_cook_link(source, COOK) is None


# --- round trip through the real store -------------------------------------


async def test_log_then_retract_restores_the_note_byte_for_byte(
    vault: Path, note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    before = note_factory(populated_note_bytes())
    await writer.append(COOK, DATE)
    assert (vault / DAILY_NOTE_PATH).read_bytes() != before

    result = await writer.retract(COOK, DATE)

    assert result.status == "retracted"
    assert result.wrote is True
    assert result.relative_path == DAILY_NOTE_PATH
    assert (vault / DAILY_NOTE_PATH).read_bytes() == before
    assert result.note_revision == note_revision(before)


async def test_a_retraction_stamps_the_receipt_and_keeps_the_row(
    settings: Settings, note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)
    await writer.retract(COOK, DATE)

    [(recipe, date, retracted_at)] = await _receipts(settings)
    assert (recipe, date) == (COOK, DATE)
    assert retracted_at is not None


async def test_a_retracted_cook_can_be_logged_again(
    vault: Path, note_factory: Callable[..., bytes], writer: CookingLogWriter, settings: Settings
) -> None:
    note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)
    await writer.retract(COOK, DATE)

    again = await writer.append(COOK, DATE)

    assert again.status == "logged"
    assert "- [[盐焗鸡]]".encode() in (vault / DAILY_NOTE_PATH).read_bytes()
    rows = await _receipts(settings)
    assert [r[2] is None for r in rows] == [False, True]  # one retracted, one active


async def test_a_retracted_cook_stops_counting_as_pending_and_as_a_readback_entry(
    note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)
    await writer.retract(COOK, DATE)

    assert await writer.cook_log_dates(COOK) == ()
    _, _, entries = await writer.read_back(DATE)
    assert entries == ()


# --- ownership --------------------------------------------------------------


async def test_a_hand_typed_link_with_no_receipt_is_never_removed(
    vault: Path, note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    """The app never wrote this line, so it has no standing to delete it."""
    before = note_factory(linked_outside_the_region_bytes())

    with pytest.raises(CookRecordNotFound):
        await writer.retract(COOK, DATE)

    assert (vault / DAILY_NOTE_PATH).read_bytes() == before


async def test_a_duplicate_log_over_a_hand_typed_link_leaves_nothing_to_retract(
    vault: Path, note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    """`duplicate` writes no receipt (F13), which is what makes ownership sound."""
    before = note_factory(linked_outside_the_region_bytes())
    assert (await writer.append(COOK, DATE)).status == "duplicate"

    with pytest.raises(CookRecordNotFound):
        await writer.retract(COOK, DATE)
    assert (vault / DAILY_NOTE_PATH).read_bytes() == before


# --- not removable ----------------------------------------------------------


async def test_a_line_edited_in_obsidian_is_refused_and_nothing_changes(
    vault: Path, note_factory: Callable[..., bytes], writer: CookingLogWriter, settings: Settings
) -> None:
    note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)
    path = vault / DAILY_NOTE_PATH
    edited = path.read_bytes().replace(
        "- [[盐焗鸡]]".encode(), "- [[盐焗鸡]] 很好吃".encode(), 1
    )
    path.write_bytes(edited)

    with pytest.raises(CookRecordNotRemovable):
        await writer.retract(COOK, DATE)

    assert path.read_bytes() == edited
    assert (await _receipts(settings))[0][2] is None  # still an active cook


# --- CAS ---------------------------------------------------------------------


async def test_a_stale_base_revision_is_a_409_and_writes_nothing(
    vault: Path, note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)
    after_append = (vault / DAILY_NOTE_PATH).read_bytes()

    with pytest.raises(DailyNoteChanged):
        await writer.retract(COOK, DATE, base_revision="sha256:not-the-current-one")

    assert (vault / DAILY_NOTE_PATH).read_bytes() == after_append


async def test_the_current_base_revision_is_accepted(
    note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    note_factory(daily_note_bytes())
    logged = await writer.append(COOK, DATE)
    result = await writer.retract(COOK, DATE, base_revision=logged.note_revision)
    assert result.status == "retracted"


# --- the window --------------------------------------------------------------


async def test_a_record_older_than_the_window_is_refused(
    vault: Path,
    note_factory: Callable[..., bytes],
    settings: Settings,
    store: AtomicNoteStore,
    initialised_db: None,
) -> None:
    note_factory(daily_note_bytes())
    real = _writer_at(settings, store, lambda: datetime.now(UTC))
    await real.append(COOK, DATE)
    after_append = (vault / DAILY_NOTE_PATH).read_bytes()

    hours = settings.cook_log_undo_hours
    later = _writer_at(settings, store, lambda: datetime.now(UTC) + timedelta(hours=hours + 1))
    with pytest.raises(RetractionWindowClosed):
        await later.retract(COOK, DATE)

    assert (vault / DAILY_NOTE_PATH).read_bytes() == after_append


async def test_a_record_just_inside_the_window_is_accepted(
    note_factory: Callable[..., bytes],
    settings: Settings,
    store: AtomicNoteStore,
    initialised_db: None,
) -> None:
    note_factory(daily_note_bytes())
    await _writer_at(settings, store, lambda: datetime.now(UTC)).append(COOK, DATE)

    hours = settings.cook_log_undo_hours
    inside = _writer_at(settings, store, lambda: datetime.now(UTC) + timedelta(hours=hours - 1))
    assert (await inside.retract(COOK, DATE)).status == "retracted"


# --- convergence -------------------------------------------------------------


async def test_a_second_retract_is_a_no_op_that_says_so(
    vault: Path, note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    before = note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)
    await writer.retract(COOK, DATE)

    second = await writer.retract(COOK, DATE)

    assert second.status == "already_retracted"
    assert second.wrote is False
    assert (vault / DAILY_NOTE_PATH).read_bytes() == before


async def test_a_link_already_deleted_in_obsidian_still_closes_the_ledger(
    vault: Path, note_factory: Callable[..., bytes], writer: CookingLogWriter, settings: Settings
) -> None:
    """The user removed the line by hand first; the receipt must not stay active."""
    before = note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)
    (vault / DAILY_NOTE_PATH).write_bytes(before)

    result = await writer.retract(COOK, DATE)

    assert result.status == "retracted"
    assert result.wrote is False
    assert (await _receipts(settings))[0][2] is not None


async def test_a_ledger_failure_after_the_note_is_committed_is_retryable_and_converges(
    vault: Path,
    note_factory: Callable[..., bytes],
    writer: CookingLogWriter,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)

    real = writer._receipts.retract
    calls = {"n": 0}

    async def flaky(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return await real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(writer._receipts, "retract", flaky)

    with pytest.raises(CookLogReceiptsUnavailable) as raised:
        await writer.retract(COOK, DATE)
    assert raised.value.retryable is True
    assert (vault / DAILY_NOTE_PATH).read_bytes() == before  # the note DID change
    assert (await _receipts(settings))[0][2] is None  # the ledger did not

    retried = await writer.retract(COOK, DATE)  # the line is gone; the ledger catches up

    assert retried.status == "retracted"
    assert (await _receipts(settings))[0][2] is not None


async def test_an_unsafe_recipe_name_is_refused_before_anything_is_read(
    note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    note_factory(daily_note_bytes())
    with pytest.raises(InvalidRequest):
        await writer.retract("../盐焗鸡", DATE)


# --- what the recipe view needs to know --------------------------------------


async def test_state_lists_a_fresh_cook_as_retractable_until_the_window_ends(
    note_factory: Callable[..., bytes], writer: CookingLogWriter, settings: Settings
) -> None:
    note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)

    state = await writer.cook_log_state(COOK, last_cooked=None, auto_updated=None)

    [cook] = state.retractable
    assert cook.log_date == DATE
    written = datetime.fromisoformat(cook.written_at)
    until = datetime.fromisoformat(cook.retractable_until)
    assert until - written == timedelta(hours=settings.cook_log_undo_hours)
    assert state.pending_retractions == ()


async def test_state_omits_a_cook_past_the_window_and_a_retracted_one(
    note_factory: Callable[..., bytes],
    settings: Settings,
    store: AtomicNoteStore,
    initialised_db: None,
) -> None:
    note_factory(daily_note_bytes())
    await _writer_at(settings, store, lambda: datetime.now(UTC)).append(COOK, DATE)
    hours = settings.cook_log_undo_hours
    later = _writer_at(settings, store, lambda: datetime.now(UTC) + timedelta(hours=hours + 1))

    assert (await later.cook_log_state(COOK, last_cooked=None, auto_updated=None)).retractable == ()

    now = _writer_at(settings, store, lambda: datetime.now(UTC))
    await now.retract(COOK, DATE)
    assert (await now.cook_log_state(COOK, last_cooked=None, auto_updated=None)).retractable == ()


async def test_a_retraction_of_the_counted_date_is_pending_until_the_tracker_runs(
    note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    """`last_cooked` still names the retracted date, and `auto_updated` predates it."""
    note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)
    await writer.retract(COOK, DATE)

    stale = await writer.cook_log_state(COOK, last_cooked=DATE, auto_updated="2000-01-01 00:00")
    assert stale.pending_retractions == (DATE,)

    # The tracker ran after the retraction: nothing is pending any more, even if a
    # hand-typed link now keeps `last_cooked` on the same date.
    caught_up = await writer.cook_log_state(COOK, last_cooked=DATE, auto_updated="2999-01-01 00:00")
    assert caught_up.pending_retractions == ()


async def test_a_retraction_of_a_date_the_tracker_never_counted_is_not_pending(
    note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    """Undoing a cook the count never included changes nothing, so no badge."""
    note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)
    await writer.retract(COOK, DATE)

    state = await writer.cook_log_state(
        COOK, last_cooked="2026-09-01", auto_updated="2000-01-01 00:00"
    )

    assert state.pending_retractions == ()


async def test_a_re_logged_date_is_not_a_pending_retraction(
    note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)
    await writer.retract(COOK, DATE)
    await writer.append(COOK, DATE)

    state = await writer.cook_log_state(COOK, last_cooked=DATE, auto_updated="2000-01-01 00:00")

    assert state.pending_retractions == ()


async def test_an_unparseable_auto_updated_counts_as_the_tracker_not_having_run(
    note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    note_factory(daily_note_bytes())
    await writer.append(COOK, DATE)
    await writer.retract(COOK, DATE)

    state = await writer.cook_log_state(COOK, last_cooked=DATE, auto_updated="yesterday-ish")

    assert state.pending_retractions == (DATE,)
