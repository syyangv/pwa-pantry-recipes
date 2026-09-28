"""The bytes the Cooking Log writes, and the refusals it must not write around.

Every test here runs against a `tmp_path` vault built from the frozen bytes in
`tests/cooklog/notes.py`. Nothing reads a live path, and nothing asserts that a
private helper was called — what is asserted is the file on disk, byte for byte,
and the typed refusal the caller receives.

The groups exist so each one kills a specific mutant of the write path:

* **layout** kills a splice at `region.end` instead of `heading.end`, and one
  that adds or eats a blank line;
* **terminators** kills a hard-coded `b"\\n"`;
* **sections** kills a "create the heading if it is missing" shortcut and a
  first-match-wins region pick;
* **dedupe** kills a region-scoped dedupe and a dropped one;
* **CAS and races** kills a skipped revision check and a swallowed
  `ConcurrentFileChange`;
* **store invariants** kills any path that is not the pinned, no-follow,
  backup-then-replace primitive.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

from app.config import Settings
from app.cooklog.writer import (
    AmbiguousNotesSection,
    CookingLogWriter,
    CookLogError,
    DailyNoteChanged,
    DailyNoteWriteUnverified,
    NotesSectionMissing,
    PostWriteVerificationError,
    already_linked,
    append_cook_link,
    cook_link_line,
    note_revision,
)
from app.db.database import connect_db
from app.vault.atomic_write import AtomicNoteStore, PathSafetyError
from app.vault.daily_paths import DailyNotePathPolicy
from app.vault.sections import SectionError, parse_sections

from . import harness
from .notes import (
    DAILY_NOTE_PATH,
    column_fence_note_bytes,
    daily_note_bytes,
    linked_outside_the_region_bytes,
    no_notes_section_bytes,
    populated_note_bytes,
    two_notes_sections_bytes,
)

pytestmark = pytest.mark.anyio

#: The F3 shape: a bare list item, no timestamp, no emoji, no task box. If this
#: ever grows a decoration, the note stops being what the vault contains.
COOK = "盐焗鸡"
LINK_LINE = "- [[盐焗鸡]]".encode()
SPLICE = LINK_LINE + b"\n"


# --- F3's byte layout -----------------------------------------------------


async def test_the_append_writes_one_list_item_as_the_first_body_line_of_the_notes_section(
    vault: Path, note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    """F3's exact layout on the measured live shape: heading, item, blank, fence.

    The `笔记` region is `blank=True` and its only content is one blank byte,
    because the next line opens a four-backtick `columns` fence. After the write
    it is two lines — the heading and the new item — with **no additional blank
    line and no consumed one**, so the fence still starts on its own line.
    """
    before = note_factory(daily_note_bytes())
    result = await writer.append(COOK, "2026-09-27")

    assert result.status == "logged"
    assert result.wrote is True
    assert result.relative_path == DAILY_NOTE_PATH

    after = (vault / DAILY_NOTE_PATH).read_bytes()
    assert "# 笔记\n- [[盐焗鸡]]\n\n````columns".encode() in after
    document = parse_sections(after)
    region = document.require_unique("笔记")
    assert region is not None
    assert region.blank is False
    # The region is now the heading, the new item, and the blank line the user
    # already had. No blank line was added and none was consumed: inserting one
    # would place the link inside the columns fence's visual area on some
    # renderers, and eating one would reformat a note the app does not own.
    assert after[region.heading.end : region.end] == SPLICE + b"\n"

    # The whole-file assertion R3 asks for: the file is the pre-image with the
    # splice removed, and nothing else moved. `task-date-recorder` rewrites daily
    # notes on a debounced `modify` event and `recipeTracker` runs
    # `processFrontMatter` on recipe notes, so "byte-identical everywhere else" is
    # the property that keeps a one-line append from becoming a reformat.
    assert after.replace(SPLICE, b"", 1) == before


async def test_the_insertion_point_is_heading_end_not_region_end(
    note_factory: Callable[..., bytes], vault: Path, writer: CookingLogWriter
) -> None:
    """A populated region ends at the columns fence; splicing there is outside it.

    The link has to land *between* `# 笔记` and the fence. `region.end` is the
    first following fence, so a splice there would place the cook below the fence
    — outside the section, in a different column layout — and the new item would
    be last rather than first.
    """
    before = note_factory(populated_note_bytes())
    await writer.append(COOK, "2026-09-27")
    after = (vault / DAILY_NOTE_PATH).read_bytes()

    assert "# 笔记\n- [[盐焗鸡]]\n- [[花蛤拌饭]]\n- [[烤鸡翅]]\n".encode() in after
    assert after.index("- [[盐焗鸡]]".encode()) < after.index("- [[花蛤拌饭]]".encode())
    assert after.replace(SPLICE, b"", 1) == before


async def test_a_region_that_is_only_the_heading_still_takes_the_item(
    note_factory: Callable[..., bytes], vault: Path, writer: CookingLogWriter
) -> None:
    """`# 笔记` immediately followed by the fence: the other real note shape."""
    before = note_factory(column_fence_note_bytes())
    await writer.append(COOK, "2026-09-27")
    after = (vault / DAILY_NOTE_PATH).read_bytes()
    assert "# 笔记\n- [[盐焗鸡]]\n````columns\n".encode() in after
    assert after.replace(SPLICE, b"", 1) == before


async def test_the_committed_revision_is_the_revision_of_the_committed_bytes(
    note_factory: Callable[..., bytes], vault: Path, writer: CookingLogWriter
) -> None:
    """`noteRevision` is the CAS token, so it must be the post-image's hash.

    A revision of anything else — the pre-image, the region, a mtime — would let
    a client offer a second write against bytes that are not on disk.
    """
    note_factory(daily_note_bytes())
    result = await writer.append(COOK, "2026-09-27")
    on_disk = (vault / DAILY_NOTE_PATH).read_bytes()
    assert result.note_revision == "sha256:" + hashlib.sha256(on_disk).hexdigest()
    assert note_revision(on_disk) == result.note_revision


# --- the line terminator (F3 step 5, R17) ---------------------------------


@pytest.mark.parametrize(
    ("newline", "label"),
    [(b"\n", "LF"), (b"\r\n", "CRLF"), (b"\r", "lone CR")],
)
async def test_the_terminator_is_read_from_the_source_and_reused_verbatim(
    newline: bytes,
    label: str,
    note_factory: Callable[..., bytes],
    vault: Path,
    writer: CookingLogWriter,
) -> None:
    """The inserted line carries the pre-image's own terminator, byte for byte.

    This is the property a hard-coded `b"\\n"` fails: on a CRLF note it leaves one
    lone LF in an otherwise CRLF file, which `git` renders as a whole-file diff
    and Obsidian shows as mixed line endings — and which `task-date-recorder`
    then re-writes on its debounced `modify` event, so the noise is permanent
    rather than transient.

    The live daily note is pure LF (152 `\\n`, zero `\\r\\n`) and the spec is
    explicit that re-measuring it proves nothing: the rule stands for the CRLF
    and lone-CR notes the engine must tolerate, and for a plugin that rewrites
    the note into CRLF after the read.
    """
    before = note_factory(daily_note_bytes(newline))
    await writer.append(COOK, "2026-09-27")
    after = (vault / DAILY_NOTE_PATH).read_bytes()
    line = "- [[盐焗鸡]]".encode() + newline

    assert after.count(line) == 1, label
    if newline == b"\r\n":
        assert b"\n" not in after.replace(b"\r\n", b""), "CRLF note gained a lone LF"
    if newline == b"\r":
        assert b"\n" not in after, "lone-CR note gained an LF"
    assert after.replace(line, b"", 1) == before


def test_a_crlf_daily_note_never_gains_a_lone_lf() -> None:
    """The byte-level statement of the same rule, on the pure function.

    `append_cook_link` delegates to `sections.insert_after_heading`, so this
    asserts the shipped primitive's contract through this module's entry point:
    every terminator in the result is the heading line's own terminator.
    """
    source = daily_note_bytes(b"\r\n")
    region = parse_sections(source).require_unique("笔记")
    assert region is not None
    rendered = append_cook_link(source, region, cook_link_line(COOK))
    assert b"\n" not in rendered.replace(b"\r\n", b"")
    assert rendered.replace("- [[盐焗鸡]]\r\n".encode(), b"", 1) == source


def test_the_patch_refuses_a_link_line_that_carries_a_newline() -> None:
    """Two items in one call is not a shape this app can produce or undo."""
    source = daily_note_bytes()
    region = parse_sections(source).require_unique("笔记")
    assert region is not None
    with pytest.raises(SectionError) as raised:
        append_cook_link(source, region, b"- [[a]]\n- [[b]]")
    assert "invalid_section_line" in str(raised.value)


# --- sections (F3 steps 2 and 4) ------------------------------------------


async def test_a_note_with_two_notes_sections_fails_closed_and_writes_nothing(
    note_factory: Callable[..., bytes], vault: Path, writer: CookingLogWriter
) -> None:
    """Two `笔记` regions is a 409, and the note is left byte-identical.

    The vault's own `sectionUpsert` is first-match-wins substring matching, so
    picking one of the two is a guess about which the user meant.
    """
    before = note_factory(two_notes_sections_bytes())
    with pytest.raises(AmbiguousNotesSection) as raised:
        await writer.append(COOK, "2026-09-27")
    assert raised.value.code == "ambiguous_notes_section"
    assert raised.value.status_code == 409
    assert (vault / DAILY_NOTE_PATH).read_bytes() == before


async def test_a_note_with_no_notes_section_is_refused_and_never_created(
    note_factory: Callable[..., bytes], vault: Path, writer: CookingLogWriter
) -> None:
    """No `笔记` heading is 422 and unreachable, not a silent heading insertion.

    `parse_sections` emits no heading and `insert_after_heading` requires a
    `Region`, so there is no code path here that could write a `# 笔记` line. The
    pre-image is asserted to contain no such heading, so this cannot pass by
    finding one that was already there.
    """
    before = note_factory(no_notes_section_bytes())
    assert "# 笔记".encode() not in before
    with pytest.raises(NotesSectionMissing) as raised:
        await writer.append(COOK, "2026-09-27")
    assert raised.value.code == "notes_section_missing"
    assert raised.value.status_code == 422
    assert (vault / DAILY_NOTE_PATH).read_bytes() == before


# --- the dedupe (D2, F13, §13.6) -----------------------------------------


async def test_logging_the_same_recipe_twice_on_one_date_writes_one_link(
    note_factory: Callable[..., bytes], vault: Path, writer: CookingLogWriter
) -> None:
    """Idempotence: the second call is a duplicate with no write.

    **This dedupe is not what keeps `cooking_count` correct.**
    `recipeTracker` computes `cooking.length = dv.pages('"日记"').where(...)`,
    which counts **pages, not links**, so a duplicate append inside one daily
    note cannot inflate the count at all. The dedupe is here for the note's
    readability and for the audit trail, and it must not be deleted on the theory
    that the counter needs protecting. The assertion below is therefore a
    *readability* assertion: the note the user has to live with has one copy.
    """
    note_factory(daily_note_bytes())
    first = await writer.append(COOK, "2026-09-27")
    after_first = (vault / DAILY_NOTE_PATH).read_bytes()
    second = await writer.append(COOK, "2026-09-27")
    after_second = (vault / DAILY_NOTE_PATH).read_bytes()

    assert (first.status, first.wrote) == ("logged", True)
    assert (second.status, second.wrote) == ("duplicate", False)
    assert after_second == after_first
    assert after_second.count("- [[盐焗鸡]]".encode()) == 1
    # The duplicate still reports a current revision, so a client holding it can
    # offer its next write against something fresh rather than a stale token.
    assert second.note_revision == first.note_revision


async def test_the_dedupe_scans_the_whole_note_not_only_the_notes_region(
    note_factory: Callable[..., bytes], vault: Path, writer: CookingLogWriter
) -> None:
    """A cook already logged *outside* `笔记` is still a duplicate.

    The vault's two hand-typed cooks are not under `笔记` at all: one sits right
    after the `![[dailyModify.base|ordered-list]]` embed and one right after
    `# Event`. A region-scoped dedupe would miss both and write a second copy
    into a note the user then has to clean by hand.
    """
    before = note_factory(linked_outside_the_region_bytes())
    result = await writer.append(COOK, "2026-09-27")
    assert result.status == "duplicate"
    assert result.wrote is False
    assert (vault / DAILY_NOTE_PATH).read_bytes() == before


async def test_a_duplicate_submits_only_the_write_lock(
    settings: Settings,
    vault: Path,
    recovery_root: Path,
    initialised_db: None,
    note_factory: Callable[..., bytes],
) -> None:
    """A duplicate is a **read-only** operation: one read, no commit, no lock.

    This is what the pre-flight dedupe buys, and it is the only thing it buys —
    the in-lock re-derivation inside the transform is what makes the dedupe
    *correct*, so dropping the pre-flight check leaves every status code and
    every byte on disk identical. What it changes is that a duplicate would have
    to reach `transform_existing`, and therefore take the advisory write lock and
    be capable of failing for a write reason the user could do nothing about.

    Asserted by handing the second attempt a store whose `transform_existing`
    raises unconditionally: a duplicate must never reach it.
    """

    class _UncommittableStore(AtomicNoteStore):
        def transform_existing(
            self, relative: str, transform: Callable[[bytes], bytes]
        ) -> bytes:
            raise AssertionError("a duplicate must not reach the commit")

    note_factory(daily_note_bytes())
    first = harness.writer_for(settings, AtomicNoteStore(vault, recovery_root))
    assert (await first.append(COOK, "2026-09-27")).status == "logged"

    refusing, _store = harness.racing_writer(
        settings, vault, recovery_root, store_factory=_UncommittableStore
    )
    result = await refusing.append(COOK, "2026-09-27")
    assert (result.status, result.wrote) == ("duplicate", False)


@pytest.mark.parametrize(
    "line",
    [
        "- [[盐焗鸡]]",
        "- [[盐焗鸡 ]]",
        "- [[ 盐焗鸡]]",
        "- [[日记/2026/盐焗鸡]]",
        "- [[Hobbies/做饭/Recipes/盐焗鸡]]",
    ],
)
def test_the_dedupe_predicate_mirrors_the_link_shapes_the_vault_contains(line: str) -> None:
    """Every spelling of a link to this recipe the vault actually holds is a hit.

    §9.14 step 3's predicate, `\\[\\[[^\\]]*\\|?\\s*<recipe>\\s*\\]\\]`, is a
    widened transcription of `recipeTracker.md`'s own
    `l.path.includes(recipe)`: a path-qualified link matches because the recipe
    appears inside `path`, and so does a link with surrounding whitespace.
    """
    assert already_linked((f"a line\n{line}\n".encode()), COOK) is True


@pytest.mark.parametrize(
    "line",
    ["- [[盐焗]]", "- [[盐焗鸡饭]]", "- [[宫保鸡丁]]", "- [[盐焗鸡订好]]", "- 盐焗鸡"],
)
def test_the_dedupe_does_not_fire_on_a_different_recipe(line: str) -> None:
    """A near miss is not a duplicate, or a second recipe could never be logged."""
    assert already_linked((f"a line\n{line}\n".encode()), COOK) is False


# --- CAS and races (R4, §9.14 step 4) ------------------------------------


async def test_a_stale_base_revision_is_a_409_and_nothing_is_written(
    note_factory: Callable[..., bytes],
    vault: Path,
    recovery_root: Path,
    writer: CookingLogWriter,
) -> None:
    """A Cooking Log is a statement about a *date*, not a delta: never auto-retry.

    The 409 carries the current revision so the client refetches and re-offers.
    A blind retry of the same stale revision is the "silent 409 loop" the Sync
    Conflict contract exists to prevent.
    """
    before = note_factory(daily_note_bytes())
    with pytest.raises(DailyNoteChanged) as raised:
        await writer.append(COOK, "2026-09-27", base_revision="sha256:" + "0" * 64)
    assert raised.value.code == "daily_note_changed"
    assert raised.value.status_code == 409
    assert raised.value.current_revision == "sha256:" + hashlib.sha256(before).hexdigest()
    assert (vault / DAILY_NOTE_PATH).read_bytes() == before
    assert _backups(recovery_root) == [], "a refused request must not leave a snapshot"


async def test_a_matching_base_revision_commits(
    note_factory: Callable[..., bytes], writer: CookingLogWriter
) -> None:
    """The happy path for the CAS: the offered revision is the one on disk."""
    before = note_factory(daily_note_bytes())
    result = await writer.append(
        COOK, "2026-09-27", base_revision="sha256:" + hashlib.sha256(before).hexdigest()
    )
    assert result.status == "logged"


async def test_a_concurrent_obsidian_write_is_a_409_and_their_bytes_survive(
    settings: Settings,
    vault: Path,
    recovery_root: Path,
    note_factory: Callable[..., bytes],
) -> None:
    """`ConcurrentFileChange` is mapped, not swallowed, and nobody's bytes are lost.

    The `race_hook` seam the store documents is used here: at `before_backup` a
    concurrent writer rewrites the note, so the store's own dev/ino/size/mtime
    check fires before the replace. The commit is refused as a 409 and the
    concurrent writer's cook is still in the file — the R4 obligation stated as
    behaviour rather than as a comment.
    """
    note_factory(daily_note_bytes())
    target = vault / DAILY_NOTE_PATH
    fired: list[str] = []

    def hook(stage: str) -> None:
        if stage == "before_backup" and not fired:
            fired.append(stage)
            target.write_bytes(daily_note_bytes() + "# 打断\n- [[花蛤拌饭]]\n".encode())

    racing, _store = harness.racing_writer(settings, vault, recovery_root, race_hook=hook)
    with pytest.raises(DailyNoteChanged) as raised:
        await racing.append(COOK, "2026-09-27")

    assert fired == ["before_backup"]
    assert raised.value.code == "daily_note_changed"
    after = target.read_bytes()
    assert "- [[花蛤拌饭]]".encode() in after, "the concurrent writer's bytes must survive"
    assert "- [[盐焗鸡]]".encode() not in after, "a refused write must not be half-applied"


async def test_a_link_that_lands_between_the_read_and_the_commit_is_a_no_op(
    settings: Settings,
    vault: Path,
    recovery_root: Path,
    initialised_db: None,
    note_factory: Callable[..., bytes],
) -> None:
    """The load-bearing concurrency property, deterministically.

    The pre-flight read in `_plan` and the bytes `transform_existing` re-reads
    under the flock are **not** the same read, so the commit re-derives its
    decision from the second one. This injects the exact hazard: a concurrent
    writer puts the link into the note after the pre-flight read saw an empty
    `笔记` region, using the store's own `race_hook` at `after_resolution` — the
    documented stage that fires after the lock is held and before the read.

    The expected outcome is a **no-op**: the transform returns the source
    unchanged, so `transform_existing` short-circuits before it takes a backup,
    allocates a temp file, or touches mtime. Asserting the absence of all three
    is the whole point — a commit that "succeeded" here would put a second copy
    of the same wikilink in a note the user has to clean by hand.

    **There is deliberately no two-thread version of this test, and the reason is
    a reported defect in the shipped store.** `AtomicNoteStore._locked` opens its
    advisory lock file with
    `os.open(name, O_RDWR | O_CREAT | O_NOFOLLOW, 0o600, dir_fd=recovery_fd)` —
    *outside* the in-process lock — and on this platform (CPython 3.12.7,
    Darwin 23.x) two threads doing that `openat` concurrently on one directory
    intermittently get a spurious `ENOENT` for a directory and file that both
    exist. The store maps any `OSError` there to
    `PathSafetyError("invalid_advisory_lock_file")`, so a legitimate pair of
    concurrent cook-log writes turns into a 500. Measured here: 10 of 15
    isolated runs and 8 of 8 whole-directory runs of the two-thread variant
    failed exactly this way. `app/vault/` is #6's shipped surface and this
    ticket does not edit it.
    """
    note_factory(daily_note_bytes())
    target = vault / DAILY_NOTE_PATH
    landed = daily_note_bytes() + LINK_LINE + b"\n"
    landed_mtime: list[int] = []

    def hook(stage: str) -> None:
        if stage == "after_resolution":
            target.write_bytes(landed)
            # Sampled *after* the injection, because the injection is itself a
            # write and the point is that the app's commit adds no second one.
            landed_mtime.append(target.stat().st_mtime_ns)

    racing, _store = harness.racing_writer(settings, vault, recovery_root, race_hook=hook)
    result = await racing.append(COOK, "2026-09-27")

    assert (result.status, result.wrote) == ("duplicate", False)
    assert result.note_revision == "sha256:" + hashlib.sha256(landed).hexdigest()
    assert target.read_bytes() == landed
    assert target.stat().st_mtime_ns == landed_mtime[0], "a no-op write must not touch mtime"
    assert _backups(recovery_root) == [], "a no-op write must not take a backup"
    assert sorted(p.name for p in (vault / "日记" / "2026").iterdir()) == ["2026-09-27.md"]


# --- the shipped store's guarantees, observed from this path --------------


async def test_a_successful_write_takes_a_backup_of_the_pre_image(
    note_factory: Callable[..., bytes], recovery_root: Path, writer: CookingLogWriter
) -> None:
    """The backup is what makes a post-write verification failure recoverable."""
    before = note_factory(daily_note_bytes())
    await writer.append(COOK, "2026-09-27")
    assert _backups(recovery_root) == [before]


async def test_the_post_write_read_back_actually_runs(
    settings: Settings, vault: Path, recovery_root: Path, note_factory: Callable[..., bytes]
) -> None:
    """`PostWriteVerificationError` is reachable, so the read-back is not a comment.

    The store's final `_read_at` is made to return a corrupted view on the
    verification read; the write must then refuse to report success, and the
    backup taken one step earlier must still be there for recovery. A store that
    skipped the read-back would return `logged` here, and a writer that swallowed
    the error would hide the whole thing.
    """
    note_factory(daily_note_bytes())

    class _LyingStore(AtomicNoteStore):
        reads = 0

        def _read_at(  # type: ignore[override]
            self, parent_fd: int, name: str, **kwargs: object
        ) -> tuple[bytes, object]:
            source, metadata = super()._read_at(parent_fd, name, **kwargs)  # type: ignore[arg-type]
            type(self).reads += 1
            if type(self).reads > 1:
                return b"corrupted", metadata
            return source, metadata

    lying = _LyingStore(vault, recovery_root)
    lying_writer = CookingLogWriter(
        lying, DailyNotePathPolicy.from_settings(settings), partial(connect_db, settings)
    )
    with pytest.raises(CookLogError) as raised:
        await lying_writer.append(COOK, "2026-09-27")

    assert isinstance(raised.value, DailyNoteWriteUnverified)
    assert isinstance(raised.value.__cause__, PostWriteVerificationError)
    assert raised.value.status_code == 500
    assert _backups(recovery_root), "the backup is the recovery path; it must exist"


async def test_a_refused_write_leaves_no_temp_file_behind(
    note_factory: Callable[..., bytes], vault: Path, writer: CookingLogWriter
) -> None:
    """A refusal must not leave a `.pwa-cook-log-` file in the user's note folder.

    Obsidian would surface an orphaned temp file as a note, which is why both
    documented race stages sit inside the store's cleanup region.
    """
    before = note_factory(daily_note_bytes())
    with pytest.raises(DailyNoteChanged):
        await writer.append(COOK, "2026-09-27", base_revision="sha256:stale")
    assert sorted(p.name for p in (vault / "日记" / "2026").iterdir()) == ["2026-09-27.md"]
    assert (vault / DAILY_NOTE_PATH).read_bytes() == before


def test_a_symlinked_vault_root_is_refused(
    settings: Settings, recovery_root: Path, tmp_path: Path
) -> None:
    """`_open_configured_root` pins descriptors with `O_NOFOLLOW`; a link is fatal."""
    link = tmp_path / "linked-vault"
    link.symlink_to(settings.vault_path, target_is_directory=True)
    with pytest.raises(PathSafetyError) as raised:
        AtomicNoteStore(link, recovery_root)
    assert "symlink" in str(raised.value)


@pytest.mark.parametrize("component", ["..", ".hidden"])
def test_traversal_and_dot_prefixed_components_are_refused(
    component: str, store: AtomicNoteStore
) -> None:
    """No request can name a path, so the store's own guard is the only one needed."""
    with pytest.raises(PathSafetyError) as raised:
        store.read_existing_if_exists(f"日记/2026/{component}/2026-09-27.md")
    assert "invalid_relative_note_path" in str(raised.value)


def test_a_single_dot_component_is_normalized_away_rather_than_refused(
    store: AtomicNoteStore, note_factory: Callable[..., bytes], vault: Path
) -> None:
    """A **reported gap in the shipped guard**, asserted so it cannot rot silently.

    `AtomicNoteStore._relative_parts` intends to refuse any component in
    `{"", ".", ".."}`, and the `.` clause is unreachable: `PurePosixPath` drops a
    lone `.` before the check runs, so `日记/2026/./2026-09-27.md` reaches the
    store as three clean parts and is accepted. `..` and a dot-*prefixed* name
    both survive normalization and are still refused, which is why this is a
    documentation gap rather than a traversal hole — the accepted path resolves
    to the same file the safe spelling names.

    It is recorded here rather than fixed: `app/vault/` is #6's shipped surface
    and this ticket does not edit it. `DailyNotePathPolicy.__post_init__`
    documents the identical `PurePosixPath(".")` trap for a configured *root*,
    so the fix, when it happens, belongs next to that comment.
    """
    source = note_factory(daily_note_bytes())
    assert store.read_existing_if_exists("日记/2026/./2026-09-27.md") == source
    assert store.read_existing_if_exists(DAILY_NOTE_PATH) == source
    assert (vault / DAILY_NOTE_PATH).read_bytes() == source


def test_a_decoy_outside_the_vault_cannot_be_reached_by_traversal(
    store: AtomicNoteStore, recovery_root: Path
) -> None:
    """`..` cannot climb out of the pinned root, so a decoy is unreachable.

    The decoy sits in the *recovery* root — outside the vault — holding exactly
    the bytes a successful append would produce. If traversal worked, this read
    would return them and the test would see a note the app never wrote.
    """
    decoy = recovery_root / "decoy.md"
    decoy.write_bytes(daily_note_bytes() + SPLICE)
    with pytest.raises(PathSafetyError):
        store.read_existing_if_exists("../../vault-recovery/decoy.md")


def test_the_write_path_uses_one_primitive_and_no_creation_mechanism() -> None:
    """F4, as an AST assertion against the shipped module.

    `tests/vault/test_atomic_write.py` already asserts that **nothing** under
    `app/` calls `.create_new(` or `.create_directory(`. What is left for this
    module to say is narrower and stronger about its own source: the *only*
    `AtomicNoteStore` methods this path reaches for are the two read/commit pair,
    and there is no `subprocess` import, no `mkdir`, and no Obsidian CLI name
    anywhere in it.

    Asserted over the parsed tree rather than the text so a mention in a
    docstring — which this file has several of, on purpose — cannot satisfy or
    break it.
    """
    from app.cooklog import writer as writer_module

    tree = ast.parse(inspect.getsource(writer_module))
    store_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_store"
    }
    assert store_calls <= {"read_existing_if_exists", "transform_existing"}, store_calls
    assert "transform_existing" in store_calls, "the one write this app performs"

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "subprocess" not in imported, imported

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "mkdir" not in called and "makedirs" not in called, called
    assert "OBSIDIAN_CLI_EXECUTABLE" not in inspect.getsource(writer_module)


def _backups(recovery_root: Path) -> list[bytes]:
    """Every pre-image snapshot the store took, one per relative-path bucket."""
    snapshots: list[bytes] = []
    for bucket in sorted(recovery_root.iterdir()):
        if not bucket.is_dir():
            continue
        for name in sorted(bucket.iterdir()):
            if name.name.endswith(".md"):
                snapshots.append(name.read_bytes())
    return snapshots
