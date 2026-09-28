"""`AtomicNoteStore`: the write primitive the Cooking Log is built on.

Every test here builds its vault and its recovery root under `tmp_path` and
reads the paths from the `Settings` fixture. No test touches the real vault.

**Races need hooks, not luck.** A concurrency test that spawns a thread and hopes
it interleaves is a test that passes for the wrong reason. `race_hook` is a
synchronous seam at four named stages, and each test below swaps a directory out
from under a *pinned descriptor* and asserts exactly where the write landed. If
the traversal were not descriptor-pinned, `after_resolution` and `before_replace`
would both write the attacker's decoy file and the test would fail — that is the
property under test, not a comment about it.

The other invariants each have their own failure mode, and each is asserted
separately so a regression names itself:

- a no-op transform writes nothing, backs up nothing, and leaves mtime alone;
- the pre-image is backed up *before* the replace, and the backup is what makes
  a post-write verification failure recoverable;
- a `ConcurrentFileChange` never destroys the racing writer's bytes;
- a post-write read-back mismatch raises and still cleans up its temp file;
- the recovery root must be outside the vault and must differ from it.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.config import Settings
from app.vault.atomic_write import (
    TEMP_PREFIX,
    AtomicNoteStore,
    ConcurrentFileChange,
    ConcurrentFileExists,
    PathSafetyError,
    PostWriteVerificationError,
)

NOTE = b"---\nday: 2026-09-27\n---\n# \xe7\xac\x94\xe8\xae\xb0\n\n````columns\nid: x\n````\n"
COOK_LINK = "- [[盐焗鸡]]"
RELATIVE = "日记/2026/2026-09-27.md"
STAGES = ("after_resolution", "before_backup", "after_temp", "before_replace")
ATTACKER = b"ATTACKER"


@pytest.fixture
def recovery_root(settings: Settings) -> Path:
    """The recovery root lives under `APP_DATA_DIR`, i.e. outside the vault."""
    root = settings.app_data_dir / "vault-recovery"
    root.mkdir()
    return root


@pytest.fixture
def note(settings: Settings) -> Path:
    """The daily note under test, written into the throwaway vault."""
    path = settings.vault_path / RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(NOTE)
    path.chmod(0o640)
    return path


@pytest.fixture
def store(settings: Settings, recovery_root: Path, note: Path) -> AtomicNoteStore:
    return AtomicNoteStore(settings.vault_path, recovery_root, backup_count=2)


def _append(source: bytes) -> bytes:
    link = f"{COOK_LINK}\n".encode()
    return source.replace(b"````columns", link + b"````columns")


def _backups(recovery_root: Path) -> list[Path]:
    return sorted(recovery_root.glob("*/*.md"))


# --- descriptor-pinned traversal ------------------------------------------


@pytest.mark.parametrize("stage", STAGES)
def test_a_directory_swapped_mid_write_cannot_redirect_the_write(
    settings: Settings, recovery_root: Path, note: Path, stage: str
) -> None:
    """The load-bearing test. A symlink or a rename swapped in after resolution
    must not move the write: the write lands in the directory the descriptor was
    opened on, and the decoy the swapper left behind is untouched."""
    swapped: dict[str, Path] = {}
    seen: list[str] = []

    def inject(current: str) -> None:
        seen.append(current)
        if current != stage or swapped:
            return
        pinned = settings.app_data_dir / "vault-pinned"
        settings.vault_path.rename(pinned)
        (settings.vault_path / "日记" / "2026").mkdir(parents=True)
        (settings.vault_path / RELATIVE).write_bytes(ATTACKER)
        swapped["decoy"] = settings.vault_path / RELATIVE
        swapped["pinned"] = pinned / RELATIVE

    with AtomicNoteStore(settings.vault_path, recovery_root, race_hook=inject) as pinned_store:
        committed = pinned_store.transform_existing(RELATIVE, _append)

    assert committed == _append(NOTE)
    # The write went to the pinned directory, not to the swapper's decoy.
    assert swapped["pinned"].read_bytes() == _append(NOTE)
    assert swapped["decoy"].read_bytes() == ATTACKER
    assert stage in seen


def test_the_root_and_recovery_root_survive_a_rename_after_they_are_pinned(
    settings: Settings, recovery_root: Path, note: Path
) -> None:
    """`os.path.realpath` resolved the configured roots once; a later rename of
    the path the operator configured must not make the store write somewhere the
    operator never named."""
    with AtomicNoteStore(settings.vault_path, recovery_root) as pinned_store:
        pinned_vault = settings.app_data_dir / "vault-moved"
        settings.vault_path.rename(pinned_vault)
        (settings.vault_path / "日记" / "2026").mkdir(parents=True)
        (settings.vault_path / RELATIVE).write_bytes(ATTACKER)
        committed = pinned_store.transform_existing(RELATIVE, _append)
    assert committed == _append(NOTE)
    assert (pinned_vault / RELATIVE).read_bytes() == _append(NOTE)
    assert (settings.vault_path / RELATIVE).read_bytes() == ATTACKER


def test_a_symlinked_configured_root_is_refused(settings: Settings, tmp_path: Path) -> None:
    """macOS exposes `/var` as the system-owned `/private/var` symlink, which is
    why the root is resolved *once* — but a symlink the operator pointed at is a
    different thing, and it is refused rather than silently followed."""
    real = tmp_path / "real-vault"
    (real / "日记" / "2026").mkdir(parents=True)
    (real / RELATIVE).write_bytes(NOTE)
    linked = tmp_path / "linked-vault"
    linked.symlink_to(real)
    recovery = tmp_path / "recovery"
    recovery.mkdir()
    with pytest.raises(PathSafetyError) as raised:
        AtomicNoteStore(linked, recovery)
    assert str(raised.value) == "vault_root_symlink_not_allowed"


def test_a_symlinked_component_inside_the_vault_is_refused(
    store: AtomicNoteStore, settings: Settings, tmp_path: Path
) -> None:
    """A symlink swapped into the middle of the path cannot be traversed, so a
    write cannot be redirected out of the vault by a plugin or a sync client."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "2026-09-27.md").write_bytes(ATTACKER)
    link = settings.vault_path / "日记" / "hijacked"
    link.symlink_to(outside)
    with pytest.raises(PathSafetyError) as raised:
        store.transform_existing("日记/hijacked/2026-09-27.md", _append)
    assert str(raised.value) == "unsafe_or_missing_note_parent"
    assert (outside / "2026-09-27.md").read_bytes() == ATTACKER


def test_a_symlinked_note_itself_is_refused(store: AtomicNoteStore, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_bytes(NOTE)
    (store.root / "日记" / "2026" / "link.md").symlink_to(outside)
    with pytest.raises(PathSafetyError):
        store.read_existing_if_exists("日记/2026/link.md")
    with pytest.raises(PathSafetyError):
        store.transform_existing("日记/2026/link.md", _append)


@pytest.mark.parametrize(
    "relative",
    ["../outside.md", "/etc/passwd", "日记/../../x.md", ".hidden/note.md", "..", "."],
)
def test_an_unsafe_relative_path_is_refused(store: AtomicNoteStore, relative: str) -> None:
    with pytest.raises(PathSafetyError) as raised:
        store.read_existing_if_exists(relative)
    assert str(raised.value) == "invalid_relative_note_path"


def test_a_dotdot_cannot_reach_a_real_file_outside_the_vault(
    store: AtomicNoteStore, settings: Settings
) -> None:
    """The traversal test that would actually fail if `_relative_parts` stopped
    rejecting `..`. A decoy sits one level *above* the vault, so a traversal that
    got through would return its bytes rather than merely a different error code
    — a refusal that raised the right exception for the wrong reason would pass
    a code-only assertion."""
    decoy = settings.vault_path.parent / "outside.md"
    decoy.write_bytes(b"SECRET\n")
    for escape in ("../outside.md", "../../etc/hosts", "日记/../../outside.md"):
        with pytest.raises(PathSafetyError) as raised:
            store.read_existing_if_exists(escape)
        assert str(raised.value) == "invalid_relative_note_path", escape
        with pytest.raises(PathSafetyError):
            store.transform_existing(escape, _append)
    assert decoy.read_bytes() == b"SECRET\n"


def test_a_dot_segment_is_normalized_rather_than_traversed(
    store: AtomicNoteStore, settings: Settings, note: Path
) -> None:
    """`PurePosixPath` drops a `.` segment, so `日记/./2026/2026-09-27.md` names
    the same file as the clean path. Recorded explicitly so the normalization is
    a stated behaviour: it stays inside the vault, unlike `..`, which is refused.
    A future `..` regression and this are the two halves of the same property."""
    assert store.read_existing_if_exists("日记/./2026/2026-09-27.md") == NOTE
    assert store.transform_existing("日记/./2026/2026-09-27.md", _append) == _append(NOTE)
    assert note.read_bytes() == _append(NOTE)


def test_a_directory_or_a_fifo_is_not_a_note(store: AtomicNoteStore) -> None:
    (store.root / "日记" / "2026" / "folder.md").mkdir()
    with pytest.raises(PathSafetyError) as raised:
        store.read_existing_if_exists("日记/2026/folder.md")
    assert str(raised.value) == "note_not_regular"


# --- recovery root containment --------------------------------------------


def test_a_recovery_root_inside_the_vault_is_refused(
    settings: Settings, recovery_root: Path
) -> None:
    """A backup inside the vault would be indexed by Obsidian and synced, and
    would surface to the user as a note they never wrote."""
    (settings.vault_path / "recovery").mkdir()
    with pytest.raises(PathSafetyError) as raised:
        AtomicNoteStore(settings.vault_path, settings.vault_path / "recovery")
    assert str(raised.value) == "recovery_root_must_be_outside_vault"


def test_a_recovery_root_equal_to_the_vault_is_refused(
    settings: Settings, recovery_root: Path
) -> None:
    with pytest.raises(PathSafetyError) as raised:
        AtomicNoteStore(settings.vault_path, settings.vault_path)
    assert str(raised.value) == "recovery_root_must_be_outside_vault"


def test_a_recovery_root_that_is_the_same_directory_under_another_name_is_refused(
    settings: Settings, tmp_path: Path
) -> None:
    """Path comparison alone is not enough: a hard-linked or bind-mounted alias
    of the vault is the same inode, and the dev/ino check is what catches it."""
    alias = tmp_path / "vault-alias"
    alias.symlink_to(settings.vault_path)
    with pytest.raises(PathSafetyError):
        # A symlinked recovery root is refused outright, which is the first gate.
        AtomicNoteStore(settings.vault_path, alias)


def test_a_missing_recovery_root_is_a_typed_refusal(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(PathSafetyError) as raised:
        AtomicNoteStore(settings.vault_path, tmp_path / "absent")
    assert str(raised.value) == "recovery_root_missing"


def test_a_zero_backup_count_is_refused(settings: Settings, recovery_root: Path) -> None:
    with pytest.raises(ValueError) as raised:
        AtomicNoteStore(settings.vault_path, recovery_root, backup_count=0)
    assert str(raised.value) == "backup_count_must_be_positive"


# --- backup then replace ---------------------------------------------------


def test_a_write_preserves_the_mode_backs_up_the_pre_image_and_leaves_no_temp(
    store: AtomicNoteStore, settings: Settings, recovery_root: Path
) -> None:
    committed = store.transform_existing(RELATIVE, _append)
    note = settings.vault_path / RELATIVE
    assert committed == _append(NOTE)
    assert note.read_bytes() == _append(NOTE)
    # The original mode survives; a temp file created at 0600 would silently
    # make the user's note unreadable to the rest of their toolchain.
    assert stat.S_IMODE(note.stat().st_mode) == 0o640
    backups = _backups(recovery_root)
    assert len(backups) == 1
    assert backups[0].read_bytes() == NOTE
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert list(note.parent.glob(f"{TEMP_PREFIX}*")) == []


def test_the_backup_is_taken_before_the_replace_and_rotation_is_bounded(
    store: AtomicNoteStore, recovery_root: Path
) -> None:
    """`backup_count=2` keeps the two most recent pre-images. An unbounded bucket
    would grow forever inside `APP_DATA_DIR`."""
    for suffix in (b"- one", b"- two", b"- three", b"- four"):

        def append(source: bytes, tail: bytes = suffix) -> bytes:
            return source + tail

        store.transform_existing(RELATIVE, append)
    assert len(_backups(recovery_root)) == 2


def test_a_no_op_transform_writes_nothing_and_backs_up_nothing(
    store: AtomicNoteStore, settings: Settings, recovery_root: Path
) -> None:
    """Returning the source unchanged must not touch mtime: a rewrite would make
    `task-date-recorder` fire and would leave a spurious recovery snapshot."""
    note = settings.vault_path / RELATIVE
    before = note.stat().st_mtime_ns
    assert store.transform_existing(RELATIVE, lambda source: source) == NOTE
    assert note.read_bytes() == NOTE
    assert note.stat().st_mtime_ns == before
    assert _backups(recovery_root) == []


def test_a_transform_that_would_exceed_the_byte_bound_fails_before_backup_and_write(
    settings: Settings, recovery_root: Path
) -> None:
    note = settings.vault_path / RELATIVE
    note.write_bytes(NOTE)
    bounded = AtomicNoteStore(settings.vault_path, recovery_root, max_bytes=len(NOTE) + 4)
    try:
        with pytest.raises(ValueError) as raised:
            bounded.transform_existing(RELATIVE, lambda source: source + b"a much longer tail")
    finally:
        bounded.close()
    assert str(raised.value) == "note_too_large"
    assert note.read_bytes() == NOTE
    assert _backups(recovery_root) == []


def test_a_note_larger_than_the_read_bound_is_refused(
    settings: Settings, recovery_root: Path
) -> None:
    note = settings.vault_path / RELATIVE
    note.write_bytes(NOTE)
    bounded = AtomicNoteStore(settings.vault_path, recovery_root, max_bytes=4)
    try:
        with pytest.raises(PathSafetyError) as raised:
            bounded.read_existing_if_exists(RELATIVE)
    finally:
        bounded.close()
    assert str(raised.value) == "note_too_large"


# --- the CAS on the note's identity ---------------------------------------


def test_a_concurrent_write_before_the_replace_is_caught_and_never_overwritten(
    store: AtomicNoteStore, settings: Settings, recovery_root: Path
) -> None:
    """`task-date-recorder` rewrites daily notes on a debounced `modify` event, so
    this is the ordinary case, not an exotic one. The racing writer's bytes must
    survive intact."""
    note = settings.vault_path / RELATIVE
    racing = NOTE.replace(b"day: 2026-09-27", b"day: 2026-09-26")

    def inject(stage: str) -> None:
        if stage == "before_replace":
            note.write_bytes(racing)

    store._race_hook = inject
    with pytest.raises(ConcurrentFileChange):
        store.transform_existing(RELATIVE, _append)
    assert note.read_bytes() == racing
    assert list(note.parent.glob(f"{TEMP_PREFIX}*")) == []
    # The backup still happened, which is what makes the refusal recoverable.
    assert [backup.read_bytes() for backup in _backups(recovery_root)] == [NOTE]


def test_a_concurrent_write_after_the_backup_is_caught_by_the_pre_backup_check(
    store: AtomicNoteStore, settings: Settings
) -> None:
    note = settings.vault_path / RELATIVE
    racing = NOTE.replace(b"day: 2026-09-27", b"day: 2026-09-25")

    def inject(stage: str) -> None:
        if stage == "before_backup":
            note.write_bytes(racing)

    store._race_hook = inject
    with pytest.raises(ConcurrentFileChange):
        store.transform_existing(RELATIVE, _append)
    assert note.read_bytes() == racing


def test_a_note_that_vanishes_before_the_replace_is_a_typed_change(
    store: AtomicNoteStore, settings: Settings
) -> None:
    note = settings.vault_path / RELATIVE

    def inject(stage: str) -> None:
        if stage == "before_replace":
            note.unlink()

    store._race_hook = inject
    with pytest.raises(ConcurrentFileChange) as raised:
        store.transform_existing(RELATIVE, _append)
    assert str(raised.value) == "daily_note_disappeared_before_replace"


def test_a_note_that_becomes_a_symlink_before_the_replace_is_a_typed_change(
    store: AtomicNoteStore, settings: Settings, tmp_path: Path
) -> None:
    note = settings.vault_path / RELATIVE
    decoy = tmp_path / "decoy.md"
    decoy.write_bytes(NOTE)

    def inject(stage: str) -> None:
        if stage == "before_replace":
            note.unlink()
            note.symlink_to(decoy)

    store._race_hook = inject
    with pytest.raises(ConcurrentFileChange) as raised:
        store.transform_existing(RELATIVE, _append)
    assert str(raised.value) == "daily_note_type_changed_before_replace"
    assert decoy.read_bytes() == NOTE


# --- post-write verification and cleanup ----------------------------------


def test_a_post_write_mismatch_is_typed_and_still_cleans_up_its_temp(
    store: AtomicNoteStore, settings: Settings, recovery_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read-back is what makes a silent partial write impossible. The
    mismatch is provoked by corrupting the *second* read, i.e. the verification
    read, so the assertion is about the verification path and not about the
    transform."""
    note = settings.vault_path / RELATIVE
    real_read = store._read_at
    calls = 0

    def corrupt_verification_read(parent_fd: int, name: str) -> tuple[bytes, os.stat_result]:
        nonlocal calls
        calls += 1
        data, metadata = real_read(parent_fd, name)
        return (data + b"corrupt", metadata) if calls == 2 else (data, metadata)

    monkeypatch.setattr(store, "_read_at", corrupt_verification_read)
    with pytest.raises(PostWriteVerificationError) as raised:
        store.transform_existing(RELATIVE, _append)
    assert str(raised.value) == "note_post_write_verification_failed"
    # The `finally` ran even though the verification failed, and the backup is on
    # disk so the note can be restored.
    assert list(note.parent.glob(f"{TEMP_PREFIX}*")) == []
    assert len(_backups(recovery_root)) == 1


def test_a_replace_that_fails_leaves_the_original_and_cleans_up(
    store: AtomicNoteStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = settings.vault_path / RELATIVE
    failure = OSError("synthetic replace failure")

    def failing_replace(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        store.transform_existing(RELATIVE, _append)
    assert note.read_bytes() == NOTE
    assert list(note.parent.glob(f"{TEMP_PREFIX}*")) == []


def test_the_read_normalizes_a_transient_descriptor_error(
    store: AtomicNoteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Obsidian can replace a vault entry mid-scan. EBADF escaping here would be
    a 500 that stalls startup rather than a typed unreadable source."""

    def failing_read(*_args: object, **_kwargs: object) -> bytes:
        raise OSError("descriptor changed during read")

    monkeypatch.setattr(os, "read", failing_read)
    with pytest.raises(PathSafetyError) as raised:
        store.read_existing_if_exists(RELATIVE)
    assert str(raised.value) == "note_unreadable"


def test_an_absent_note_reads_as_none_not_as_an_error(store: AtomicNoteStore) -> None:
    assert store.read_existing_if_exists("日记/2026/2099-01-01.md") is None
    assert store.read_existing_if_exists("2099/2099-01-01.md") is None
    with pytest.raises(PathSafetyError) as raised:
        store.transform_existing("日记/2026/2099-01-01.md", _append)
    assert str(raised.value) == "unsafe_or_missing_daily_note"


# --- the dormant exclusive create -----------------------------------------


def test_an_exclusive_create_never_overwrites(store: AtomicNoteStore) -> None:
    """`O_EXCL`, so a file that appeared between preflight and the open fails
    closed rather than being clobbered."""
    with pytest.raises(ConcurrentFileExists) as raised:
        store.create_new(RELATIVE, b"replacement\n")
    assert str(raised.value) == "target_already_exists"
    assert (store.root / RELATIVE).read_bytes() == NOTE


def test_an_exclusive_create_writes_and_verifies(store: AtomicNoteStore) -> None:
    committed = store.create_new("日记/2026/2026-09-28.md", b"fresh\n")
    assert committed == b"fresh\n"
    assert (store.root / "日记" / "2026" / "2026-09-28.md").read_bytes() == b"fresh\n"


def test_nothing_in_the_app_calls_a_creation_primitive() -> None:
    """F4: the PWA never creates a daily note. `create_new` is ported only
    because it is the sole raiser of `ConcurrentFileExists`; if a call site ever
    appears, this fails rather than letting a 404 turn into a silent creation."""
    app = Path(__file__).resolve().parents[2] / "app"
    callers = [
        path.relative_to(app).as_posix()
        for path in sorted(app.rglob("*.py"))
        if ".create_new(" in path.read_text() or ".create_directory(" in path.read_text()
    ]
    assert callers == []


def test_the_store_has_no_directory_creation_primitive() -> None:
    """F4 removed the only reason the sibling had one, so it is not ported: a
    creation surface nobody calls is a surface nobody has audited."""
    assert not hasattr(AtomicNoteStore, "create_directory")
    assert not hasattr(AtomicNoteStore, "directory_exists")


# --- the listing the recipe index wants ------------------------------------


def test_the_listing_is_bounded_and_skips_what_is_not_a_note(
    store: AtomicNoteStore, settings: Settings, note: Path, tmp_path: Path
) -> None:
    """Descriptor-pinned and no-follow: a symlink out of the vault, a directory
    named `.md`, and a dot-prefixed file are all refused as entries."""
    folder = note.parent
    for name in ("盐焗鸡.md", "b.md", "A.MD", "not-a-note.txt", ".hidden.md"):
        (folder / name).write_bytes(NOTE)
    outside = tmp_path / "outside.md"
    outside.write_bytes(NOTE)
    (folder / "escape.md").symlink_to(outside)
    (folder / "directory.md").mkdir()
    names = store.list_direct_markdown("日记/2026")
    assert set(names) == {"盐焗鸡.md", "b.md", "A.MD", "2026-09-27.md"}
    assert "escape.md" not in names
    assert "directory.md" not in names
    assert ".hidden.md" not in names
    # A name being listed does not make it readable through a symlink either.
    assert "escape.md" not in names
    assert outside.read_bytes() == NOTE


def test_the_listing_bound_is_an_error_not_a_truncation(store: AtomicNoteStore) -> None:
    with pytest.raises(PathSafetyError) as raised:
        store.list_direct_markdown("日记/2026", max_files=0)
    assert str(raised.value) == "directory_scan_limit"


def test_listing_a_missing_or_symlinked_root_is_typed(store: AtomicNoteStore) -> None:
    with pytest.raises(PathSafetyError) as raised:
        store.list_direct_markdown("2099")
    assert str(raised.value) == "unsafe_or_missing_scan_root"
    with pytest.raises(PathSafetyError) as raised:
        store.list_direct_markdown("../..")
    assert str(raised.value) == "invalid_relative_note_path"


# --- lifecycle -------------------------------------------------------------


def test_the_store_closes_its_descriptors_and_is_a_context_manager(
    settings: Settings, recovery_root: Path, note: Path
) -> None:
    with AtomicNoteStore(settings.vault_path, recovery_root) as opened:
        assert opened.read_existing_if_exists(RELATIVE) == NOTE
    # A closed store cannot be read through — a leaked descriptor per request
    # would exhaust the process's fd table over a long uptime. The refusal is
    # typed like every other unreadable-source case, not a bare OSError.
    with pytest.raises(PathSafetyError) as raised:
        opened.read_existing_if_exists(RELATIVE)
    assert str(raised.value) == "note_unreadable"
    opened.close()  # idempotent


def test_the_write_is_reproducible_for_a_pure_transform(store: AtomicNoteStore) -> None:
    """The transform must be a pure function of the source it is handed: the
    store re-reads the note under the lock, so a transform closing over anything
    else computes a patch against bytes it is not looking at. A pure transform
    applied twice is therefore a no-op the second time — and the store's
    identity check makes the non-pure one fail rather than double-append."""

    def link_once(source: bytes) -> bytes:
        return source if COOK_LINK.encode() in source else source + COOK_LINK.encode() + b"\n"

    first = store.transform_existing(RELATIVE, link_once)
    second = store.transform_existing(RELATIVE, link_once)
    assert first == second == NOTE + COOK_LINK.encode() + b"\n"
    assert first.count(COOK_LINK.encode()) == 1


def test_the_race_hook_sees_every_named_stage(store: AtomicNoteStore) -> None:
    """The four stage names are the seam's public contract; renaming one would
    silently stop every race test from injecting anything."""
    seen: list[str] = []
    store._race_hook = seen.append
    store.transform_existing(RELATIVE, _append)
    assert [stage for stage in seen if stage in STAGES] == [
        "after_resolution",
        "before_backup",
        "after_temp",
        "before_replace",
    ]


def test_an_race_hook_that_raises_aborts_the_write(
    store: AtomicNoteStore, settings: Settings
) -> None:
    """A hook is a test seam, but it is called inside the locked section, so a
    raising hook must propagate rather than be swallowed."""

    def explode(stage: str) -> None:
        if stage == "after_temp":
            raise RuntimeError("hook exploded")

    store._race_hook = explode
    with pytest.raises(RuntimeError):
        store.transform_existing(RELATIVE, _append)
    # The temp file was created before the hook fired, so the `finally` must
    # still have removed it.
    assert list((settings.vault_path / RELATIVE).parent.glob(f"{TEMP_PREFIX}*")) == []
