"""F4: a missing daily note is a named error, and a lost race is not that error.

The whole F4 design is four rules and every one of them is asserted here rather
than described in a comment:

1. absent, twice in a row → **404** `daily_note_missing`, naming the date and the
   vault-relative path, `retryable: true`;
2. absent then present → **409** `daily_note_created_concurrently`, because
   reporting a lost race as "not found" sends the user to create a note that
   already exists and the second attempt then fails differently;
3. present at both reads but gone before the replace → **409**
   `daily_note_changed`, the existing code, never `daily_note_missing`;
4. **no file is created** in any case, and `retryable: true` is a claim that gets
   tested by performing the retry.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from app.config import Settings
from app.cooklog.writer import (
    CookingLogWriter,
    DailyNoteChanged,
    DailyNoteCreatedConcurrently,
    DailyNoteMissing,
)
from app.db.database import db_path
from app.vault.atomic_write import AtomicNoteStore

from . import harness
from .notes import DAILY_NOTE_PATH, daily_note_bytes

pytestmark = pytest.mark.anyio

COOK = "盐焗鸡"
DATE = "2026-09-27"
YEAR_DIR = Path("日记") / "2026"


async def test_a_missing_daily_note_is_a_404_that_names_the_date_and_the_path(
    vault: Path, writer: CookingLogWriter
) -> None:
    """The most specific error the API emits, and the one R16 is about.

    The message names **both** the human date and the exact vault-relative path,
    so the fix is one paste into Obsidian's quick switcher. It is in Chinese
    because the app's user-facing language throughout is Chinese (`笔记`,
    `严格模式（含调料）`, `待 Obsidian 同步`), and the UI renders it verbatim
    rather than synthesizing its own vaguer copy.
    """
    with pytest.raises(DailyNoteMissing) as raised:
        await writer.append(COOK, DATE)

    error = raised.value
    assert error.code == "daily_note_missing"
    assert error.status_code == 404
    assert error.relative_path == DAILY_NOTE_PATH
    assert error.log_date == DATE
    assert error.retryable is True
    assert DATE in error.user_message
    assert DAILY_NOTE_PATH in error.user_message
    # Vault-*relative* only. An absolute path here would break the Server-Owned
    # Root invariant and leak the vault's location to anything that can read a
    # response body.
    assert str(vault) not in error.user_message
    assert not Path(error.relative_path).is_absolute()


async def test_a_missing_daily_note_is_never_created(
    vault: Path, writer: CookingLogWriter
) -> None:
    """The refusal creates nothing: no file, no directory, no note, no ledger.

    F4 removed the Obsidian CLI, the subprocess sandbox, the Templater settle
    window, the rollback, and the UUID idempotency ledger in exchange for this.
    A test that asserted the error but not the absence would pass against the
    old design, which also errored — after creating the note.
    """
    with pytest.raises(DailyNoteMissing):
        await writer.append(COOK, DATE)

    assert not (vault / DAILY_NOTE_PATH).exists()
    assert not (vault / "日记" / "2026" / "2026-09-27.md").exists()
    # The year directory may exist (the `tmp_path` fixture creates it); the note
    # must not, and no sibling with a near-miss name may appear either.
    assert sorted(p.name for p in (vault / "日记" / "2026").iterdir()) == []


async def test_the_same_request_succeeds_once_the_user_creates_the_note(
    vault: Path, note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    """`retryable: true` is literal, so the retry is performed, not assumed.

    This is the whole point of the field: the user's next action is creating the
    note in Obsidian and pressing the button again, unchanged. If the retry
    needed a different body, a different header, or a different anything, the
    flag would be a lie and the UI would be showing a dead end as a door.
    """
    with pytest.raises(DailyNoteMissing):
        await writer.append(COOK, DATE)

    note_factory(daily_note_bytes())
    result = await writer.append(COOK, DATE)
    assert result.status == "logged"
    assert result.wrote is True
    assert "- [[盐焗鸡]]".encode() in (vault / DAILY_NOTE_PATH).read_bytes()


async def test_a_note_that_appears_between_the_two_reads_is_a_409_not_a_404(
    settings: Settings, vault: Path, recovery_root: Path
) -> None:
    """The creation-conflict case: iCloud sync lag, or an Obsidian write landing.

    F4 step 1 says re-check **exactly once**; a note that is there on the second
    read is `409 daily_note_created_concurrently`, carrying `currentRevision` and
    `retryable: true` and reusing the one 409 resolve panel. Only after **two**
    `None`s is it a 404 — `daily_note_missing` has to mean the note is absent,
    full stop, or it is a 404 that lies.
    """
    target = vault / DAILY_NOTE_PATH
    reads: list[int] = []

    class _AppearingStore(AtomicNoteStore):
        """Reports absence on the first read and materialises the note before the second."""

        def read_existing_if_exists(
            self, relative: str, *, max_bytes: int | None = None
        ) -> bytes | None:
            reads.append(1)
            if len(reads) > 1:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(daily_note_bytes())
            return super().read_existing_if_exists(relative, max_bytes=max_bytes)

    writer, _store = harness.racing_writer(
        settings, vault, recovery_root, store_factory=_AppearingStore
    )
    with pytest.raises(DailyNoteCreatedConcurrently) as raised:
        await writer.append(COOK, DATE)

    error = raised.value
    assert error.code == "daily_note_created_concurrently"
    assert error.status_code == 409
    assert error.relative_path == DAILY_NOTE_PATH
    assert error.retryable is True
    assert error.current_revision == "sha256:" + hashlib.sha256(
        daily_note_bytes()
    ).hexdigest()
    assert len(reads) == 2, "exactly one re-check, bounded at two attempts, no sleep loop"
    # A 409 leaves the note alone: the user taps retry and the write proceeds.
    assert "- [[盐焗鸡]]".encode() not in target.read_bytes()


async def test_a_note_that_vanishes_before_the_replace_is_the_existing_409(
    settings: Settings, note_factory: Callable[..., bytes], vault: Path, recovery_root: Path
) -> None:
    """The mirror case is `daily_note_changed`, never `daily_note_missing`.

    `ConcurrentFileChange("daily_note_disappeared_before_replace")` is a lost
    race, not an absent resource: the app already proved the note exists. F4
    step 5 names this explicitly, and reusing the one 409 is the point.
    """
    note_factory(daily_note_bytes())
    target = vault / DAILY_NOTE_PATH
    fired: list[str] = []

    def hook(stage: str) -> None:
        if stage == "after_temp" and not fired:
            fired.append(stage)
            target.unlink()

    racing, _store = harness.racing_writer(settings, vault, recovery_root, race_hook=hook)
    with pytest.raises(DailyNoteChanged) as raised:
        await racing.append(COOK, DATE)

    assert fired == ["after_temp"]
    assert raised.value.code == "daily_note_changed"
    assert raised.value.status_code == 409
    assert not raised.value.code.endswith("missing")


async def test_a_refused_creation_writes_no_receipt_row(
    settings: Settings, writer: CookingLogWriter
) -> None:
    """The audit trail records what was written, so a refusal records nothing.

    Read straight out of the SQLite file rather than through the writer, because
    "the ledger is empty" is a claim about the database and going through the
    object that would populate it would make it a claim about a mock.
    """
    with pytest.raises(DailyNoteMissing):
        await writer.append(COOK, DATE)
    connection = sqlite3.connect(db_path(settings))
    try:
        rows = connection.execute(
            "SELECT recipe_note, log_date, relative_path, note_revision"
            " FROM cook_log_receipts"
        ).fetchall()
    finally:
        connection.close()
    assert rows == []


def test_the_year_directory_is_untouched_by_a_refusal(vault: Path) -> None:
    """Sanity on the fixture itself: the year directory starts empty.

    Without this, `test_a_missing_daily_note_is_never_created`'s "nothing was
    created" assertion would be satisfied by a vault the fixture had already
    populated, and would prove nothing.
    """
    assert (vault / YEAR_DIR).is_dir()
    assert list((vault / YEAR_DIR).iterdir()) == []
