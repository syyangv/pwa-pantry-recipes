"""Descriptor-pinned, revision-aware atomic replacement for existing notes.

**`transform_existing` is the only write this app performs** (F4). It takes a
pure `bytes -> bytes` transform of one already-existing note and commits the
result through a fixed sequence, none of which may be re-invented at a call
site:

1. per-relative `flock` in the recovery root, plus an in-process lock, so two
   PWA processes and two threads serialize the same note;
2. read the source and its `stat` through the pinned descriptor;
3. run the transform, and return the source **unchanged** if it is identical —
   a no-op write must not touch mtime, must not back up, and must not consume a
   temp file;
4. re-stat and compare dev/ino/size/mtime_ns (`_same_file`), so a note that
   `task-date-recorder` rewrote between our read and our replace is caught as
   `ConcurrentFileChange` rather than silently overwritten;
5. take a timestamped backup snapshot in the recovery root, rotated to
   `backup_count`;
6. write an `O_EXCL` temp file **in the target directory** at the original mode,
   so `os.replace` is same-filesystem and the mode is preserved;
7. re-check identity, `os.replace` with both `dir_fd`s, `fsync` the directory;
8. read the committed file back and byte-compare, raising
   `PostWriteVerificationError` on mismatch — a silent partial write is
   impossible, and the backup on step 5 is what makes that failure recoverable;
9. unlink the temp file in a `finally` whenever it was not replaced.

**Traversal is descriptor-pinned and no-follow.** The configured root is
canonicalized once and every component is opened with
`O_RDONLY | O_DIRECTORY | O_NOFOLLOW` relative to the previous descriptor. A
symlink swapped in mid-write therefore cannot redirect the write out of the
vault: the write lands in the directory the descriptor was opened on, which is
the test that proves the property, not an assertion about it.

**`recovery_root` must live outside the vault** and be a different directory, so
a recovery snapshot can never be synced, indexed, or mistaken for a note.

**The store never assumes it is the only writer.** `read_existing_if_exists`
normalizes a transient descriptor race (Obsidian replacing an entry mid-scan)
into a typed `PathSafetyError` rather than letting EBADF/EISDIR/EMFILE escape as
a 500 that stalls startup.

Ported from `pwa-obsidian-daily/app/vault/atomic_write.py`. The `race_hook`
seam is what makes the concurrency properties testable: a test injects a
concurrent write at `after_resolution`, `before_backup`, `after_temp`, or
`before_replace` and asserts the outcome, rather than hoping for a race.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import secrets
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


class PathSafetyError(ValueError):
    """A relative path or a note shape was not safe to traverse or read."""


class ConcurrentFileChange(RuntimeError):
    """The note changed underneath us between the read and the replace."""


class ConcurrentFileExists(RuntimeError):
    """An exclusive create hit an existing target."""


class PostWriteVerificationError(RuntimeError):
    """The committed bytes did not read back as the bytes that were rendered."""


_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}

#: Prefix of the `O_EXCL` temp file written in the target directory. Named so a
#: test can assert "no temp file survived" without hard-coding a name in two
#: places — a renamed prefix must not make the cleanup assertion vacuous.
TEMP_PREFIX = ".pwa-cook-log-"


def _process_lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class AtomicNoteStore:
    """Operate only through directory descriptors pinned at configuration time.

    `recovery_root` is expected to already exist and to be outside the vault —
    conventionally `Settings.app_data_dir / "vault-recovery"`. This store never
    creates a directory: F4 removed the only reason it had one.
    """

    def __init__(
        self,
        root: Path,
        recovery_root: Path,
        *,
        max_bytes: int = 2_000_000,
        backup_count: int = 10,
        race_hook: Callable[[str], None] | None = None,
    ):
        self.root, self._root_fd = self._open_configured_root(root, "vault")
        try:
            self.recovery_root, self._recovery_fd = self._open_configured_root(
                recovery_root, "recovery"
            )
        except BaseException:
            os.close(self._root_fd)
            raise
        if self.recovery_root == self.root or self.root in self.recovery_root.parents:
            os.close(self._root_fd)
            os.close(self._recovery_fd)
            raise PathSafetyError("recovery_root_must_be_outside_vault")
        root_identity = os.fstat(self._root_fd)
        recovery_identity = os.fstat(self._recovery_fd)
        if (root_identity.st_dev, root_identity.st_ino) == (
            recovery_identity.st_dev,
            recovery_identity.st_ino,
        ):
            os.close(self._root_fd)
            os.close(self._recovery_fd)
            raise PathSafetyError("recovery_root_must_differ_from_vault")
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._race_hook = race_hook
        self._closed = False
        if backup_count < 1:
            self.close()
            raise ValueError("backup_count_must_be_positive")

    @staticmethod
    def _open_configured_root(path: Path, label: str) -> tuple[Path, int]:
        """Canonicalize once, then pin every component without following links."""
        try:
            if stat.S_ISLNK(path.lstat().st_mode):
                raise PathSafetyError(f"{label}_root_symlink_not_allowed")
        except FileNotFoundError as exc:
            raise PathSafetyError(f"{label}_root_missing") from exc
        # macOS exposes /var as the system-owned /private/var symlink. Resolve
        # the configured root once, then pin and traverse that canonical path.
        absolute = Path(os.path.realpath(path))
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        current_fd = os.open("/", flags)
        try:
            for component in absolute.parts[1:]:
                try:
                    next_fd = os.open(component, flags, dir_fd=current_fd)
                except OSError as exc:
                    raise PathSafetyError(f"unsafe_or_missing_{label}_root") from exc
                os.close(current_fd)
                current_fd = next_fd
            return absolute, current_fd
        except BaseException:
            os.close(current_fd)
            raise

    def close(self) -> None:
        if not getattr(self, "_closed", True):
            os.close(self._root_fd)
            os.close(self._recovery_fd)
            self._closed = True

    def __enter__(self) -> AtomicNoteStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _inject(self, stage: str) -> None:
        if self._race_hook:
            self._race_hook(stage)

    @staticmethod
    def _relative_parts(relative: str) -> tuple[str, ...]:
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} or part.startswith(".") for part in pure.parts)
        ):
            raise PathSafetyError("invalid_relative_note_path")
        return pure.parts

    @contextmanager
    def _target_parent(self, relative: str) -> Iterator[tuple[int, str]]:
        parts = self._relative_parts(relative)
        current_fd = os.dup(self._root_fd)
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            for component in parts[:-1]:
                try:
                    next_fd = os.open(component, flags, dir_fd=current_fd)
                except (FileNotFoundError, NotADirectoryError, OSError) as exc:
                    raise PathSafetyError("unsafe_or_missing_note_parent") from exc
                os.close(current_fd)
                current_fd = next_fd
            self._inject("after_resolution")
            yield current_fd, parts[-1]
        finally:
            os.close(current_fd)

    def _read_at(
        self, parent_fd: int, name: str, *, max_bytes: int | None = None
    ) -> tuple[bytes, os.stat_result]:
        limit = max_bytes if max_bytes is not None else self.max_bytes
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, flags, dir_fd=parent_fd)
        except (FileNotFoundError, OSError) as exc:
            raise PathSafetyError("unsafe_or_missing_daily_note") from exc
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise PathSafetyError("daily_note_not_regular")
            if metadata.st_size > limit:
                raise PathSafetyError("daily_note_too_large")
            chunks: list[bytes] = []
            total = 0
            while total <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > limit:
                raise PathSafetyError("daily_note_too_large")
            return b"".join(chunks), metadata
        finally:
            os.close(fd)

    @staticmethod
    def _stat_at(parent_fd: int, name: str) -> os.stat_result:
        try:
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ConcurrentFileChange("daily_note_disappeared_before_replace") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ConcurrentFileChange("daily_note_type_changed_before_replace")
        return metadata

    @staticmethod
    def _same_file(before: os.stat_result, after: os.stat_result) -> bool:
        return (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )

    @contextmanager
    def _locked(self, relative: str) -> Iterator[None]:
        key = hashlib.sha256(relative.encode()).hexdigest()
        name = f".{key}.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(name, flags, 0o600, dir_fd=self._recovery_fd)
        except OSError as exc:
            raise PathSafetyError("invalid_advisory_lock_file") from exc
        try:
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                raise PathSafetyError("invalid_advisory_lock_file")
            os.fchmod(lock_fd, 0o600)
            with _process_lock(f"{os.fstat(self._root_fd).st_ino}:{relative}"):
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    def read_existing_if_exists(
        self, relative: str, *, max_bytes: int | None = None
    ) -> bytes | None:
        """Read one note, or `None` for absence; normalize transient races.

        Obsidian can replace a vault entry while a projection is scanning it. Do
        not let EBADF/EISDIR/EMFILE escape as a 500 and stall the whole startup;
        callers already treat PathSafetyError as a typed unreadable/missing
        source.
        """
        try:
            return self._read_existing_if_exists_impl(relative, max_bytes=max_bytes)
        except OSError as exc:
            raise PathSafetyError("note_unreadable") from exc

    def _read_existing_if_exists_impl(
        self, relative: str, *, max_bytes: int | None = None
    ) -> bytes | None:
        """Return secure bytes or None for absence; never follow a path symlink."""
        limit = max_bytes if max_bytes is not None else self.max_bytes
        parts = self._relative_parts(relative)
        current_fd = os.dup(self._root_fd)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            for component in parts[:-1]:
                try:
                    next_fd = os.open(component, directory_flags, dir_fd=current_fd)
                except FileNotFoundError:
                    return None
                except OSError as exc:
                    raise PathSafetyError("unsafe_note_parent") from exc
                os.close(current_fd)
                current_fd = next_fd
            try:
                fd = os.open(
                    parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd
                )
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise PathSafetyError("unsafe_note") from exc
            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode):
                    raise PathSafetyError("note_not_regular")
                if metadata.st_size > limit:
                    raise PathSafetyError("note_too_large")
                source = bytearray()
                while len(source) <= limit:
                    chunk = os.read(fd, min(65_536, limit + 1 - len(source)))
                    if not chunk:
                        break
                    source.extend(chunk)
                if len(source) > limit:
                    raise PathSafetyError("note_too_large")
                return bytes(source)
            finally:
                os.close(fd)
        finally:
            os.close(current_fd)

    def transform_existing(self, relative: str, transform: Callable[[bytes], bytes]) -> bytes:
        """Atomically apply a deterministic transform to one exact existing note.

        Returns the committed bytes. The transform must be a pure function of the
        source it is handed: the store re-reads the note under the lock, so a
        transform that closed over anything else would be computing a patch
        against bytes it is not looking at.
        """
        with self._locked(relative), self._target_parent(relative) as (parent_fd, name):
            source, metadata = self._read_at(parent_fd, name)
            rendered = transform(source)
            if len(rendered) > self.max_bytes:
                raise ValueError("note_too_large")
            if rendered == source:
                return source
            if not self._same_file(metadata, self._stat_at(parent_fd, name)):
                raise ConcurrentFileChange("note_changed_before_replace")
            self._backup(relative, source)
            temporary = self._temp_at(parent_fd, stat.S_IMODE(metadata.st_mode), rendered)
            replaced = False
            try:
                # Injected here, not at the tail of `_temp_at`: the sibling fires
                # `after_temp` before the name reaches the caller's `try`, so an
                # exception at that documented stage orphans the temp file in the
                # user's note directory — where Obsidian would surface it as a
                # note. Both stages belong inside the cleanup region.
                self._inject("after_temp")
                self._inject("before_replace")
                if not self._same_file(metadata, self._stat_at(parent_fd, name)):
                    raise ConcurrentFileChange("note_changed_before_replace")
                os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                replaced = True
                self._fsync_directory(parent_fd)
                committed, _ = self._read_at(parent_fd, name)
                if committed != rendered:
                    raise PostWriteVerificationError("note_post_write_verification_failed")
                return committed
            finally:
                if not replaced:
                    try:
                        os.unlink(temporary, dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass

    def list_direct_markdown(self, relative: str, *, max_files: int = 500) -> tuple[str, ...]:
        """Direct ``.md`` children of one existing directory.

        Descriptor-pinned and no-follow; symlinked/non-regular entries are
        skipped and a missing/unsafe root raises :class:`PathSafetyError`
        (callers decide missing-root semantics). Bounded by ``max_files``.
        """
        parts = self._relative_parts(relative)
        current_fd = os.dup(self._root_fd)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            for component in parts:
                try:
                    next_fd = os.open(component, directory_flags, dir_fd=current_fd)
                except FileNotFoundError as exc:
                    raise PathSafetyError("unsafe_or_missing_scan_root") from exc
                except OSError as exc:
                    raise PathSafetyError("unsafe_scan_root") from exc
                os.close(current_fd)
                current_fd = next_fd
            names: list[str] = []
            try:
                with os.scandir(current_fd) as entries:
                    for entry in entries:
                        if len(names) >= max_files:
                            raise PathSafetyError("directory_scan_limit")
                        name = entry.name
                        if name.startswith(".") or not name.lower().endswith(".md"):
                            continue
                        try:
                            metadata = os.stat(
                                name, dir_fd=current_fd, follow_symlinks=False
                            )
                        except OSError:
                            continue
                        if not stat.S_ISREG(metadata.st_mode):
                            continue
                        names.append(name)
            except PathSafetyError:
                raise
            except OSError as exc:
                raise PathSafetyError("unsafe_scan_root") from exc
            # Directory enumeration order (creation order on APFS) is preserved:
            # the recipe list is presented in vault order, not alphabetically.
            return tuple(names)
        finally:
            try:
                os.close(current_fd)
            except OSError:
                pass

    def create_new(self, relative: str, content: bytes, *, max_bytes: int | None = None) -> bytes:
        """Exclusively create one new note; never overwrite.

        Dormant in this app: F4 makes a missing daily note a named error, so no
        daily-note path calls this. It is ported because it is the only raiser of
        :class:`ConcurrentFileExists`, and that typed failure is part of the
        primitive's contract — an O_EXCL create that could overwrite would be a
        worse defect than a missing one. `tests/vault/test_atomic_write.py`
        asserts no code under `app/` calls it.

        The parent directory must already exist. Post-write verification failure
        deletes the file this request provably created (best-effort) and raises
        ``PostWriteVerificationError``.
        """
        limit = max_bytes if max_bytes is not None else self.max_bytes
        if len(content) > limit:
            raise ValueError("note_too_large")
        with self._locked(relative), self._target_parent(relative) as (parent_fd, name):
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(name, flags, 0o666, dir_fd=parent_fd)
            except FileExistsError as exc:
                raise ConcurrentFileExists("target_already_exists") from exc
            except OSError as exc:
                raise PathSafetyError("unsafe_or_missing_note_parent") from exc
            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode):
                    raise PathSafetyError("target_not_regular")
                view = memoryview(content)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            self._fsync_directory(parent_fd)
            committed, _ = self._read_at(parent_fd, name, max_bytes=limit)
            if committed != content:
                try:
                    os.unlink(name, dir_fd=parent_fd)
                    self._fsync_directory(parent_fd)
                except OSError:
                    pass
                raise PostWriteVerificationError("new_note_post_write_verification_failed")
            return committed

    def _open_backup_bucket(self, relative: str) -> int:
        bucket = hashlib.sha256(relative.encode()).hexdigest()
        try:
            os.mkdir(bucket, 0o700, dir_fd=self._recovery_fd)
        except FileExistsError:
            pass
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            bucket_fd = os.open(bucket, flags, dir_fd=self._recovery_fd)
        except OSError as exc:
            raise PathSafetyError("invalid_recovery_bucket") from exc
        os.fchmod(bucket_fd, 0o700)
        return bucket_fd

    def _backup(self, relative: str, source: bytes) -> None:
        self._inject("before_backup")
        bucket_fd = self._open_backup_bucket(relative)
        try:
            name = f"{time.time_ns()}-{hashlib.sha256(source).hexdigest()[:12]}.md"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(name, flags, 0o600, dir_fd=bucket_fd)
            try:
                view = memoryview(source)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(bucket_fd)
            backups = sorted(
                (item for item in os.listdir(bucket_fd) if item.endswith(".md")), reverse=True
            )
            for stale in backups[self.backup_count :]:
                os.unlink(stale, dir_fd=bucket_fd)
            if len(backups) > self.backup_count:
                os.fsync(bucket_fd)
        finally:
            os.close(bucket_fd)
        self._inject("after_backup")

    def _temp_at(self, parent_fd: int, mode: int, rendered: bytes) -> str:
        for _ in range(20):
            name = f"{TEMP_PREFIX}{secrets.token_hex(12)}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(name, flags, mode, dir_fd=parent_fd)
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("unable_to_allocate_atomic_temp")
        try:
            os.fchmod(fd, mode)
            view = memoryview(rendered)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        return name

    @staticmethod
    def _fsync_directory(directory_fd: int) -> None:
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
                raise
