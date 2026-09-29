"""`CookingLogWriter` — read, patch, and commit one Cooking Log wikilink (D2).

**This is the only code path in the app that writes to the vault**, and it does
so through exactly one shipped primitive: `AtomicNoteStore.transform_existing`
on a file that is already present (F4). Everything that makes that write safe
lives in code this module did not write and must not re-implement:

===========================  ==================================================
Guarantee                    Where it actually lives
===========================  ==================================================
flock + in-process lock      `atomic_write.AtomicNoteStore._locked`
CAS on content revision      the `base_revision` check in `append` (here) and
                             `_same_file` / `_stat_at` in `transform_existing`
backup before replace        `atomic_write.AtomicNoteStore._backup`
same-filesystem replace      `_temp_at` (O_EXCL temp in the target directory)
directory fsync              `_fsync_directory`
post-write byte verify       the read-back in `transform_existing`
no-follow traversal          `_open_configured_root` / `_target_parent`
temp cleanup on failure      the `finally` in `transform_existing`
===========================  ==================================================

**The patch is a byte splice, never a re-dump** (R3). `append_cook_link` is a
thin, pure wrapper over the shipped `sections.insert_after_heading`, so the
line terminator is read from the heading's own source span and reused verbatim.
A hard-coded `b"\\n"` would put one lone LF into an otherwise CRLF daily note,
which `git` and Obsidian both render as a whole-file diff and which
`task-date-recorder` then re-writes on its debounced `modify` event — so the
noise is permanent, not transient. The live note happens to be pure LF; that is
the easy case, not the justification.

**Dedupe is for the note and the audit trail, NOT for `cooking_count`.**
`Helper/utils/recipeTracker.md` computes
`cooking.length = dv.pages('"日记"').where(...)` — it counts **pages, not
links** — so a duplicate append inside one daily note cannot inflate the count,
not by one and not by any amount. The dedupe below and
`cook_log_receipts.ux_cook_log_receipts_recipe_date` therefore exist for
readability and for the answer to "did the app write this?". **Do not remove
them on the theory that the counter needs protecting. It does not.** A note
with four copies of `[[盐焗鸡]]` is a note the user has to clean by hand.

**`cook_log_receipts` is a mirror, not the correctness mechanism** (F13). The
daily-note wikilink is what `recipeTracker` reads; the receipts table is the
audit trail and the double-submit dedupe ledger. This module writes
`recipe_tracker_synced` as 0 and **does not update it** — the read-path
comparison spec §13.5 once described here has no implementation, so the column
was inert, and §13 step 8 has been amended accordingly. The PWA's view of
`cooking_count` is expected to lag Obsidian and this module does not compensate
for that; `CookLogReceipts.log_dates` and the recipe detail route report the lag
without writing anything.

**A missing daily note is an error, never a creation** (F4). `None` from
`read_existing_if_exists` is re-checked exactly once and then raises
`DailyNoteMissing`; the note appeared under us raises
`DailyNoteCreatedConcurrently`. There is no `create_new` call, no subprocess,
and no idempotency ledger, and `tests/vault/test_atomic_write.py` asserts no
code under `app/` reaches for `create_new`.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Final, Literal

import aiosqlite

from ..vault.atomic_write import (
    AtomicNoteStore,
    ConcurrentFileChange,
    PathSafetyError,
    PostWriteVerificationError,
)
from ..vault.daily_paths import DailyNotePathError, DailyNotePathPolicy
from ..vault.sections import (
    AmbiguousSection,
    Region,
    SectionError,
    insert_after_heading,
    parse_sections,
)

#: The daily-note heading the Cooking Log writes under (F3). The engine matches
#: headings by **exact title**, so this is `笔记` and not a substring probe: the
#: vault's own `sectionUpsert` is first-match-wins `lines[i].includes(keyword)`
#: and the PWA must not replicate that.
NOTES_SECTION_TITLE: Final = "笔记"
AMBIGUOUS_NOTES_SECTION: Final = "ambiguous_notes_section"
NOTES_SECTION_MISSING: Final = "notes_section_missing"

#: A recipe note name is a **basename**, never a path and never a display alias
#: (D2). The bound is the same one `pwa-deals` puts on its idempotency key.
MAX_RECIPE_NOTE_CHARS: Final = 200

#: A recipe note name is a single path component with no traversal and no
#: wikilink metacharacter. It ends up inside `[[…]]` and inside a SQL string,
#: so the whole shape is refused rather than escaped-and-hoped.
_UNSAFE_RECIPE_NOTE: Final = re.compile(r"[\[\]\\\x00-\x1f\x7f/]")

CookLogStatus = Literal["logged", "duplicate"]

#: `DailyNotePathError` codes that describe a broken operator configuration
#: rather than a request. Both are reachable only when `DAILY_NOTES_ROOT` or
#: `DAILY_NOTES_YEAR_POLICY` is unusable, and neither is fixable by editing a
#: request body.
_OPERATOR_MISCONFIGURATION: Final[frozenset[str]] = frozenset(
    {"invalid_daily_notes_root", "invalid_daily_notes_year_policy"}
)


class CookLogError(RuntimeError):
    """Base class for the Cooking Log's own refusals.

    Carries the four fields the API envelope can publish for it. `relative_path`
    is **vault-relative only** — never an absolute path — so the Server-Owned
    Root invariant and `/health` are unaffected (F4).
    """

    code: str = "cook_log_error"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        relative_path: str | None = None,
        log_date: str | None = None,
        current_revision: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.relative_path = relative_path
        self.log_date = log_date
        self.current_revision = current_revision
        self.retryable = retryable

    @property
    def user_message(self) -> str:
        """The message the UI renders verbatim; there is one wording to keep right."""
        return str(self)


class InvalidRequest(CookLogError):
    """The request named no recipe this path can render, or no resolvable date."""

    code = "invalid_request"
    status_code = 422


class DailyNoteMissing(CookLogError):
    """F4: the note for this date is absent, twice in a row. Retryable by the user."""

    code = "daily_note_missing"
    status_code = 404


class DailyNoteCreatedConcurrently(CookLogError):
    """F4: the note was absent and then appeared — a lost race, never a 404.

    Reporting this as `daily_note_missing` would send the user to create a note
    that already exists, and the second attempt would fail differently. It reuses
    the one 409 resolve panel rather than introducing a second conflict UI.
    """

    code = "daily_note_created_concurrently"
    status_code = 409


class DailyNoteChanged(CookLogError):
    """The note moved under us: a stale `base_revision`, or `ConcurrentFileChange`."""

    code = "daily_note_changed"
    status_code = 409


class AmbiguousNotesSection(CookLogError):
    """Two `笔记` regions. The vault's own `sectionUpsert` would pick the first."""

    code = AMBIGUOUS_NOTES_SECTION
    status_code = 409


class NotesSectionMissing(CookLogError):
    """No `笔记` heading. Unreachable, not silently created — see F3 step 4."""

    code = NOTES_SECTION_MISSING
    status_code = 422


class CookLogReceiptsUnavailable(CookLogError):
    """The ledger is unreadable — the write failed after the note was committed,
    or a read of it failed.

    Reported as its own 503 rather than swallowed, and rather than reported as
    a success: the note *is* on disk and the row that answers "did the app write
    this?" is not, and saying `logged` would be claiming an audit trail that does
    not exist. The remedy is to retry, and the retry is a deduped no-op that
    back-fills the row — so the shape is safe to retry, not a dead end.

    On the read side the argument is the same and the claim inverted: an
    unreadable ledger is not evidence of a synced tracker, so the detail view
    answers 503 rather than publishing an empty pending-cook list that reads as
    "nothing is behind".
    """

    code = "cook_log_receipts_unavailable"
    status_code = 503


class DailyNoteUnreadable(CookLogError):
    """The note could not be read or committed. A vault/host condition, not a 4xx."""

    code = "daily_note_unreadable"
    status_code = 500


class DailyNoteWriteUnverified(CookLogError):
    """The committed bytes did not read back as the bytes that were rendered.

    The backup taken on `transform_existing`'s step 5 is what makes this
    recoverable, and the note is *not* reported as a success.
    """

    code = "daily_note_write_unverified"
    status_code = 500


@dataclass(frozen=True)
class CookLogResult:
    """The outcome of one `append`.

    `wrote` is the only field that distinguishes a real commit from a no-op. It
    is deliberately redundant with `status` — `wrote == (status == "logged")` is
    an invariant, not a coincidence — because the two answer different questions:
    `status` is what the client renders, and `wrote` is what happened to the
    file. A result that reported `logged` for a no-op would put a `201` on a
    response whose note the app never touched.
    """

    status: CookLogStatus
    wrote: bool
    recipe_note: str
    log_date: str
    relative_path: str
    note_revision: str
    written_at: str | None = None


@dataclass(frozen=True)
class CookLogEntry:
    """One row of the read-back: what the receipts ledger says it wrote.

    **There is no `tracker_synced` field, and that is the fix rather than an
    omission.** It used to carry `cook_log_receipts.recipe_tracker_synced`, a
    column inserted as 0 that nothing ever writes, so the field could only ever
    be `False` and the API published that to the badge — which is how
    `待 Obsidian 同步` came to sit on the screen permanently. The comparison that
    answers the real question is `CookLogReceipts.log_dates` joined against
    `RecipeCookingHistory.last_cooked` on the recipe detail route, and it is a
    read of two facts that already exist. The column stays in the schema, inert.
    """

    recipe_note: str
    written_at: str


@dataclass(frozen=True)
class _Plan:
    """The pre-flight decision, taken from the first read of the note.

    Nothing here is committed. The commit re-derives the same decision from the
    bytes `transform_existing` re-reads under the lock, because between the two
    reads Obsidian, `task-date-recorder`, and a second PWA process may all have
    written.
    """

    relative_path: str
    log_date: str
    recipe_note: str
    link_line: bytes
    duplicate: bool
    note_revision: str


def append_cook_link(source: bytes, region: Region, link_line: bytes) -> bytes:
    """Splice `link_line` as the first body line of `region`. Pure; touches no other byte.

    This is deliberately a five-line wrapper and not a second splice. The
    terminator, the `heading.end` insertion point, the LF/CRLF/lone-CR handling,
    and the "the heading already exists, nothing is created" property are all
    `sections.insert_after_heading`'s, and a copy of that logic here is exactly
    the drift F3 was written to prevent. `link_line` is bytes because the caller
    built it as bytes; the shipped primitive takes `str` because a wikilink
    cannot contain a newline in either encoding, which is checked here rather
    than assumed.
    """
    if b"\n" in link_line or b"\r" in link_line:
        raise SectionError("invalid_section_line")
    return insert_after_heading(source, region, link_line.decode("utf-8"))


def cook_link_line(recipe_note: str) -> bytes:
    """`- [[<recipe_note>]]` — a list item, and nothing else (F3 step 6).

    No timestamp, no emoji, no trailing metadata, and **no `- [ ]` task box**:
    a task is checkable, the user could strike it, and `recipeTracker` would
    still read the link, so a struck cook would be silently counted. The text is
    the bare wikilink — never `[[盐焗鸡|盐焗鸡]]` — because `recipeTracker`'s
    predicate keys on the note's basename and the bare form is what the vault
    already contains.
    """
    return f"- [[{recipe_note}]]".encode()


def already_linked(source: bytes, recipe_note: str) -> bool:
    """True when this note already links to `recipe_note` **anywhere**.

    A whole-note scan, not a region check, and the boundary is deliberate. The
    vault's two existing logged cooks are not under `笔记` at all: `2026-03-10`
    has `[[盐焗鸡]]` after the `![[dailyModify.base|ordered-list]]` embed and
    `2026-09-14` has `[[花蛤拌饭]]` after `# Event`. A region check would miss
    both and write a second copy.

    The predicate mirrors `recipeTracker.md`'s own match — an outlink whose
    `path.includes(recipe)` or `display === recipe` — as §9.14 step 3 specifies:
    `\\[\\[[^\\]]*\\|?\\s*<escaped>\\s*\\]\\]`. That covers the bare form, a
    folder-qualified path form, and the `|alias` form whose *path* is the recipe.

    **One gap, stated rather than closed.** `[[Other|盐焗鸡]]` — a link whose
    *display* is this recipe and whose path is not — is counted by
    `recipeTracker` through its `display` branch and is **not** matched here. It
    is unreachable from this write path, which only ever writes the bare form,
    and the vault's two hand-typed cooks are bare too, so closing it would mean
    refusing a legitimate write for a link this app cannot produce.

    Matching is done on bytes, not on a decoded string: a note that fails to
    decode is rejected by `parse_sections` anyway, and a decode round-trip here
    would be a second, differently-failing UTF-8 policy.
    """
    pattern = re.compile(
        b"\\[\\[[^\\]]*\\|?\\s*" + re.escape(recipe_note.encode("utf-8")) + b"\\s*\\]\\]"
    )
    return pattern.search(source) is not None


def note_revision(source: bytes) -> str:
    """`sha256:<hex>` of the exact note bytes — the optimistic-concurrency token."""
    return "sha256:" + hashlib.sha256(source).hexdigest()


def _require_recipe_note(recipe_note: str) -> str:
    """A recipe note name is a bare basename or it is refused (Server-Owned Root).

    It is never joined to a path by the client and never trusted as one: the
    router looks it up in the recipe index (`#15`'s `RecipeIndex`) and the write
    only ever emits it as wikilink text. Validating the shape here means a value
    that somehow reached the writer without passing the index cannot become part
    of a path or a `[[…]]` metacharacter.
    """
    if not isinstance(recipe_note, str):
        raise InvalidRequest("recipe_note_must_be_a_string", code="invalid_recipe_note")
    if (
        not recipe_note
        or recipe_note != recipe_note.strip()
        or len(recipe_note) > MAX_RECIPE_NOTE_CHARS
        or _UNSAFE_RECIPE_NOTE.search(recipe_note)
        or recipe_note.endswith(".md")
    ):
        raise InvalidRequest("invalid_recipe_note", code="invalid_recipe_note")
    return recipe_note


def _locate_notes_section(source: bytes) -> Region:
    """The single `# 笔记` region, or a typed refusal.

    `None` is the *missing* case and it is a 422, not a silent heading
    insertion: the engine emits no heading, so a note without `笔记` is
    unreachable rather than creatable (F3 step 4). More than one is a 409 — the
    vault's `sectionUpsert` would take the first, and guessing which of two
    `笔记` regions the user means is not a decision this app may make.
    """
    document = parse_sections(source)
    region = document.require_unique(
        NOTES_SECTION_TITLE, level=None, code=AMBIGUOUS_NOTES_SECTION
    )
    if region is None:
        raise NotesSectionMissing(NOTES_SECTION_MISSING)
    return region


class CookLogReceipts:
    """The PWA-owned mirror: audit trail and double-submit dedupe ledger (F13).

    **Not the mechanism that keeps `cooking_count` correct.** The daily-note
    wikilink is; this table cannot be, because `recipeTracker` counts pages.
    Neither is redundant and neither may be described as the other.
    """

    _INSERT = (
        "INSERT INTO cook_log_receipts"
        " (recipe_note, log_date, relative_path, note_revision)"
        " VALUES (?, ?, ?, ?)"
    )
    _SELECT = (
        # `recipe_tracker_synced` is deliberately NOT selected. It is inserted as
        # 0 and nothing here ever writes 1, so selecting it would put a value on
        # the wire that is the absence of an implementation rather than a fact
        # about Obsidian. See `CookLogEntry`.
        "SELECT recipe_note, written_at"
        " FROM cook_log_receipts WHERE log_date = ? ORDER BY id"
    )

    async def record(
        self,
        connection: aiosqlite.Connection,
        *,
        recipe_note: str,
        log_date: str,
        relative_path: str,
        revision: str,
    ) -> bool:
        """Insert one receipt; `False` means the unique index caught a double submit.

        A constraint violation here is not an error: the second of two concurrent
        submits is reported as `status="duplicate"`, which is the last of the
        three dedupe layers behind the note (whole-note scan, the
        `transform_existing` flock, and this index).
        """
        try:
            await connection.execute(
                self._INSERT, (recipe_note, log_date, relative_path, revision)
            )
            await connection.commit()
        except sqlite3.IntegrityError:
            return False
        return True

    async def entries_for(
        self, connection: aiosqlite.Connection, log_date: str
    ) -> tuple[CookLogEntry, ...]:
        rows = await (await connection.execute(self._SELECT, (log_date,))).fetchall()
        return tuple(
            CookLogEntry(
                recipe_note=str(row["recipe_note"]),
                written_at=str(row["written_at"]),
            )
            for row in rows
        )

    async def written_at(
        self, connection: aiosqlite.Connection, *, recipe_note: str, log_date: str
    ) -> str | None:
        """When a given recipe was logged on a date, or `None`.

        Only used on the duplicate path, so the `200 duplicate` response still
        carries the audit row rather than inventing a timestamp.
        """
        cursor = await connection.execute(
            "SELECT written_at FROM cook_log_receipts"
            " WHERE recipe_note = ? AND log_date = ?",
            (recipe_note, log_date),
        )
        row = await cursor.fetchone()
        return None if row is None else str(row["written_at"])

    async def log_dates(
        self, connection: aiosqlite.Connection, *, recipe_note: str
    ) -> tuple[str, ...]:
        """Every `log_date` this recipe was logged on, ascending.

        **Read-only, and the one query here that exists for a read surface
        rather than for the write path.** It is how the detail view answers "the
        tracker has not counted this yet" without the PWA ever touching the nine
        tracker-owned frontmatter fields: the receipt says the PWA wrote a
        Cooking Record on a date, and comparing that date against
        `RecipeCookingHistory.last_cooked` is a read of two facts that already
        exist. Nothing here writes, and nothing here writes back.

        `recipe_tracker_synced` is deliberately NOT selected and deliberately not
        used to answer that question. It is inserted as 0 and **nothing in this
        repository ever flips it to 1** — the read-path comparison §13.5 describes
        has no implementation — so a filter on it would return every receipt
        forever and would report a permanent, growing "unsynced" count for a
        recipe whose tracker had in fact run. The date comparison is the honest
        signal because it is the definition: a cook is counted exactly when the
        tracker's `last_cooked` has reached its date.
        """
        rows = await (
            await connection.execute(
                "SELECT log_date FROM cook_log_receipts"
                " WHERE recipe_note = ? ORDER BY log_date",
                (recipe_note,),
            )
        ).fetchall()
        return tuple(str(row["log_date"]) for row in rows)


#: `app.db.connect_db` is an `asynccontextmanager`, so the writer depends on a
#: factory rather than a connection: it opens and closes one per operation, which
#: is also what keeps a single-user PWA from pinning a WAL handle forever.
ConnectFactory = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]


class CookingLogWriter:
    """Append one `- [[RecipeName]]` to a daily note's `# 笔记` section.

    All blocking descriptor work runs on a worker thread (`asyncio.to_thread`):
    this is a single-user Tailscale PWA whose vault lives on a local disk, and
    holding the event loop across a `flock`, a backup write, and an `fsync` would
    stall every other request. Only the aiosqlite receipt insert runs on the loop.
    """

    def __init__(
        self,
        store: AtomicNoteStore,
        path_policy: DailyNotePathPolicy,
        connect: ConnectFactory,
    ) -> None:
        self._store = store
        self._policy = path_policy
        self._connect = connect
        self._receipts = CookLogReceipts()

    # --- the write ------------------------------------------------------

    async def append(
        self,
        recipe_note: str,
        log_date: str,
        base_revision: str | None = None,
        client_id: str | None = None,
    ) -> CookLogResult:
        """Read, patch, and commit. `client_id` is accepted and ignored.

        The ignored argument is not an oversight (F5, §9.16): cook logs are
        online-only and never travel through the outbox, so there is no replay to
        deduplicate. The header survives as defence in depth against a
        user-driven double submit, and the real guarantee is the note plus
        `cook_log_receipts`.
        """
        del client_id
        recipe_note = _require_recipe_note(recipe_note)
        relative = self._resolve(log_date)
        plan = await asyncio.to_thread(self._plan, relative, log_date, recipe_note, base_revision)
        if plan.duplicate:
            return await self._duplicate_result(plan)
        committed, appended = await asyncio.to_thread(self._commit, plan)
        revision = note_revision(committed)
        async with self._connect() as connection:
            try:
                recorded = await self._receipts.record(
                    connection,
                    recipe_note=plan.recipe_note,
                    log_date=plan.log_date,
                    relative_path=plan.relative_path,
                    revision=revision,
                )
            except sqlite3.Error as exc:
                raise self._receipts_unavailable(exc, plan, appended) from exc
        if not recorded:
            # The unique index caught a concurrent double submit after the note
            # was already committed. Reported as a duplicate rather than a
            # second logged cook: the note's own whole-note dedupe is what makes
            # that state reachable at all, and the user's answer must not be
            # "logged twice".
            return CookLogResult(
                status="duplicate",
                wrote=False,
                recipe_note=plan.recipe_note,
                log_date=plan.log_date,
                relative_path=plan.relative_path,
                note_revision=revision,
            )
        return CookLogResult(
            status="logged" if appended else "duplicate",
            wrote=appended,
            recipe_note=plan.recipe_note,
            log_date=plan.log_date,
            relative_path=plan.relative_path,
            note_revision=revision,
        )

    # --- the read-back --------------------------------------------------

    async def cook_log_dates(self, recipe_note: str) -> tuple[str, ...]:
        """Every date this recipe was logged on through the PWA, ascending.

        The read-side counterpart to `append`, and the only reason a router
        outside the Cooking Log surface reads this object at all: the detail view
        has to say how far behind `cooking_count` is, and the receipt is the only
        record of a cook the tracker has not seen yet.

        **It does not read the vault.** `read_back` above must, because it
        answers about a daily note; this answers about a SQLite table, so a
        recipe whose note is unreadable still reports its pending cooks instead of
        404-ing on a staleness annotation. A `sqlite3.Error` becomes
        `CookLogReceiptsUnavailable` exactly as it does on the write path, and the
        caller answers 503 rather than reporting "nothing pending" — an unreadable
        ledger is not evidence of a synced tracker.

        The connection is opened and closed here rather than taken from the
        caller: this is a `GET`, it holds nothing across a yield, and borrowing
        the writer's factory is what keeps a second unclosed connection from
        appearing next to it.
        """
        recipe_note = _require_recipe_note(recipe_note)
        async with self._connect() as connection:
            try:
                return await self._receipts.log_dates(connection, recipe_note=recipe_note)
            except sqlite3.Error as exc:
                raise CookLogReceiptsUnavailable(
                    f"cook_log_receipts_unavailable: {exc}"
                ) from exc

    async def read_back(self, log_date: str) -> tuple[str, str, tuple[CookLogEntry, ...]]:
        """`(relative_path, note_revision, entries)` for one date.

        A missing note raises `DailyNoteMissing` here too, and the 404 is
        deliberate: an `entries: []` answer for a date with no note would assert
        "you cooked nothing that day", which is a different claim from "there is
        no record of that day" and not one this app can support (§9.15).
        """
        relative = self._resolve(log_date)
        source = await asyncio.to_thread(self._read_required, relative, log_date)
        async with self._connect() as connection:
            try:
                entries = await self._receipts.entries_for(connection, log_date)
            except sqlite3.Error as exc:
                raise CookLogReceiptsUnavailable(
                    f"cook_log_receipts_unavailable: {exc}", relative_path=relative
                ) from exc
        return relative, note_revision(source), entries

    # --- steps ----------------------------------------------------------

    @staticmethod
    def _receipts_unavailable(
        exc: sqlite3.Error, plan: _Plan, appended: bool
    ) -> CookLogReceiptsUnavailable:
        """Say plainly that the note is committed and the ledger row is not.

        The message distinguishes the two states because the user's next action
        differs: nothing at all if the write did not happen, and a retry if it
        did. A single "something went wrong" would leave them guessing which.
        """
        landed = "日记笔记已写入" if appended else "日记笔记未被修改"
        return CookLogReceiptsUnavailable(
            f"{landed}，但 cook_log_receipts 写入失败：{exc}。请重试。",
            relative_path=plan.relative_path,
            log_date=plan.log_date,
            retryable=True,
        )

    def _resolve(self, log_date: str) -> str:
        """The date becomes a path here and nowhere else.

        `DailyNotePathPolicy` is the only thing that turns a date into a path:
        `DAILY_NOTES_ROOT` is server-owned, the year is derived from the parsed
        date, and `resolve` requires `date.fromisoformat` to round-trip exactly —
        so `20260927` and `2026-W40-7` are refused rather than guessed at. A
        date outside `daily_notes_year_policy` is `date_outside_configured_scope`.

        The two refusals a *request* can cause (`invalid_daily_note_date`,
        `date_outside_configured_scope`) keep the path policy's own code, so the
        422 names the rule that actually refused it. The two a *misconfigured
        operator* can cause are 500s: no request body can fix them, and a 422
        would invite the client to retry a body that is already correct.
        """
        try:
            return self._policy.resolve(log_date)
        except DailyNotePathError as exc:
            if str(exc) in _OPERATOR_MISCONFIGURATION:
                raise DailyNoteUnreadable(f"daily_note_path_unusable: {exc}") from exc
            raise InvalidRequest(str(exc), code=str(exc)) from exc

    def _read_required(self, relative: str, log_date: str) -> bytes:
        """§9.15's table, steps 1–4: read, re-check once, then name the absence."""
        source = self._read(relative, log_date)
        if source is not None:
            return source
        # F4 step 1: re-resolve the path through the store and re-read, exactly
        # once. No sleep loop — the window is milliseconds, and a bounded retry
        # that sleeps is a retry that hides a genuinely absent note.
        source = self._read(relative, log_date)
        if source is not None:
            raise DailyNoteCreatedConcurrently(
                self._concurrent_message(relative, log_date),
                relative_path=relative,
                log_date=log_date,
                current_revision=note_revision(source),
                retryable=True,
            )
        raise DailyNoteMissing(
            f"找不到 {log_date} 的日记：{relative}。请先在 Obsidian 中创建这一天的日记，然后重试。",
            relative_path=relative,
            log_date=log_date,
            retryable=True,
        )

    def _read(self, relative: str, log_date: str) -> bytes | None:
        try:
            return self._store.read_existing_if_exists(relative)
        except PathSafetyError as exc:
            # A symlinked or otherwise unsafe entry is a host condition, not a
            # malformed request, and the request cannot express a path at all.
            raise DailyNoteUnreadable(
                f"daily_note_unreadable: {exc}", relative_path=relative, log_date=log_date
            ) from exc

    @staticmethod
    def _concurrent_message(relative: str, log_date: str) -> str:
        return (
            f"{relative} 刚刚出现了（iCloud 同步延迟或 Obsidian 写入）。"
            f"请重试记录 {log_date} 的这次烹饪。"
        )

    def _plan(
        self, relative: str, log_date: str, recipe_note: str, base_revision: str | None
    ) -> _Plan:
        """Steps 1–4 against the first read: absent, section, dedupe, CAS."""
        source = self._read_required(relative, log_date)
        try:
            # Step 2 runs on this first read purely for its **error code**: a
            # note with no `笔记` is a 422 and one with two is a 409, and both
            # have to be decided before the CAS so a stale revision does not
            # mask a malformed note. The offsets it computes are discarded —
            # `transform_existing` re-reads the note and re-locates the region
            # against the bytes it actually commits against.
            _locate_notes_section(source)
        except AmbiguousSection as exc:
            raise AmbiguousNotesSection(
                AMBIGUOUS_NOTES_SECTION, relative_path=relative, log_date=log_date
            ) from exc
        except SectionError as exc:
            raise DailyNoteUnreadable(
                f"daily_note_unparseable: {exc}", relative_path=relative, log_date=log_date
            ) from exc
        current = note_revision(source)
        if base_revision is not None and base_revision != current:
            # A Cooking Log is a statement about a *date*, not a delta, so a blind
            # retry of the same stale revision is wrong. 409 with the current
            # revision: the client refetches and re-offers. It is never retried
            # here — that is the "silent 409 loop" the Sync Conflict contract
            # exists to prevent.
            raise DailyNoteChanged(
                f"daily_note_changed: {relative}",
                relative_path=relative,
                log_date=log_date,
                current_revision=current,
                retryable=True,
            )
        return _Plan(
            relative_path=relative,
            log_date=log_date,
            recipe_note=recipe_note,
            link_line=cook_link_line(recipe_note),
            duplicate=already_linked(source, recipe_note),
            note_revision=current,
        )

    def _commit(self, plan: _Plan) -> tuple[bytes, bool]:
        """Step 6, and the whole safety argument of this module.

        The transform **re-derives its decision from the bytes
        `transform_existing` re-reads under the flock**, not from the pre-flight
        read. That is why a second PWA process, a `task-date-recorder` rewrite, or
        an Obsidian save landing in the window between the two reads cannot
        produce two copies: the loser sees the winner's link, returns the source
        unchanged, and `transform_existing` short-circuits before it backs up,
        allocates a temp file, or touches mtime.
        """
        appended = False

        def transform(fresh: bytes) -> bytes:
            # `appended` is a write-only observation channel, never an input: the
            # transform's *return value* is a pure function of `fresh`, which is
            # the property `transform_existing` actually relies on. The value has
            # to escape somehow, and threading it through the return type would
            # mean re-reading the committed bytes and inferring intent from them.
            nonlocal appended
            try:
                region = _locate_notes_section(fresh)
            except AmbiguousSection as exc:
                raise AmbiguousNotesSection(
                    AMBIGUOUS_NOTES_SECTION,
                    relative_path=plan.relative_path,
                    log_date=plan.log_date,
                ) from exc
            except SectionError as exc:
                raise DailyNoteUnreadable(
                    f"daily_note_unparseable: {exc}",
                    relative_path=plan.relative_path,
                    log_date=plan.log_date,
                ) from exc
            if already_linked(fresh, plan.recipe_note):
                appended = False
                return fresh
            appended = True
            return append_cook_link(fresh, region, plan.link_line)

        try:
            committed = self._store.transform_existing(plan.relative_path, transform)
        except ConcurrentFileChange as exc:
            # Includes the case §9.15 step 5 names: the target vanished before the
            # replace. That is a lost race, so it is the **existing** 409 code and
            # never `daily_note_missing`.
            raise DailyNoteChanged(
                f"daily_note_changed: {exc}",
                relative_path=plan.relative_path,
                log_date=plan.log_date,
                current_revision=plan.note_revision,
                retryable=True,
            ) from exc
        except PostWriteVerificationError as exc:
            raise DailyNoteWriteUnverified(
                f"daily_note_write_unverified: {exc}",
                relative_path=plan.relative_path,
                log_date=plan.log_date,
            ) from exc
        except PathSafetyError as exc:
            raise DailyNoteUnreadable(
                f"daily_note_unreadable: {exc}",
                relative_path=plan.relative_path,
                log_date=plan.log_date,
            ) from exc
        except ValueError as exc:  # note_too_large, from transform_existing
            raise DailyNoteUnreadable(
                f"daily_note_unreadable: {exc}",
                relative_path=plan.relative_path,
                log_date=plan.log_date,
            ) from exc
        return committed, appended

    async def _duplicate_result(self, plan: _Plan) -> CookLogResult:
        """HTTP 200, `wrote=False`, and no write of any kind.

        `note_revision` is the revision of the note as it stands, so a client
        holding this value can offer the next write against a current revision
        rather than a stale one.
        """
        written_at: str | None = None
        async with self._connect() as connection:
            try:
                written_at = await self._receipts.written_at(
                    connection, recipe_note=plan.recipe_note, log_date=plan.log_date
                )
            except sqlite3.Error as exc:
                raise self._receipts_unavailable(exc, plan, False) from exc
        return CookLogResult(
            status="duplicate",
            wrote=False,
            recipe_note=plan.recipe_note,
            log_date=plan.log_date,
            relative_path=plan.relative_path,
            note_revision=plan.note_revision,
            written_at=written_at,
        )
