"""Server-owned resolution of strict Daily Note dates to vault-relative paths.

**The browser never supplies a path, only a date.** `Settings.daily_notes_root`
is server-owned and the year is derived from the parsed date, so a request body
carrying `"date": "2026-09-27"` names exactly one file,
`日记/2026/2026-09-27.md`, and there is no request field that can redirect a
write anywhere else. That invariant is the reason this module exists at all.

Ported from `pwa-obsidian-daily/app/vault/daily_paths.py`. The `years` bound is
the one addition: the sibling validated the year inside its CLI caller, and
this app's `Settings.daily_notes_year_policy` makes the bound part of the path
policy itself, so a date outside it fails here as `date_outside_configured_scope`
rather than at some later call site that may not run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath

from ..config import Settings

#: `Settings.daily_notes_year_policy`'s own grammar, so a value config.py
#: already accepted is never re-litigated here.
_YEAR_RANGE = re.compile(r"\d{4}-\d{4}")


class DailyNotePathError(ValueError):
    """The configured policy or requested date cannot name a Daily Note."""


@dataclass(frozen=True)
class DailyNotePathPolicy:
    """`DAILY_NOTES_ROOT` plus the optional inclusive year bounds.

    `years` is `None` when the operator configured no policy, which is a real
    configured state and not "unvalidated": the daily-notes folder layout is then
    the only bound on the year.
    """

    root: str
    years: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        pure = PurePosixPath(self.root)
        # `not pure.parts` is the sibling's missing clause and it is the one
        # that matters: `PurePosixPath(".")` normalizes to zero parts *and*
        # `as_posix()` still returns `"."`, so the sibling's `not self.root`
        # check misses it and every other clause iterates an empty tuple. A `"."`
        # root then resolves to `./2026/2026-09-27.md`, which
        # `AtomicNoteStore._relative_parts` happily accepts because PurePosix
        # drops the `.` — a policy that names the vault root by accident rather
        # than by configuration.
        if (
            not self.root
            or not pure.parts
            or pure.is_absolute()
            or pure.as_posix() != self.root
            or any(part in {"", ".", ".."} or part.startswith(".") for part in pure.parts)
        ):
            raise DailyNotePathError("invalid_daily_notes_root")
        if self.years is not None:
            low, high = self.years
            if low > high:
                raise DailyNotePathError("invalid_daily_notes_year_policy")

    @classmethod
    def from_settings(cls, settings: Settings) -> DailyNotePathPolicy:
        """Build the policy from the fail-closed `Settings` surface.

        `os.environ` is never read here: the root and the year bounds are both
        server-owned, and a policy assembled from the environment directly
        would be a second, unvalidated copy of `Settings`.
        """
        return cls(settings.daily_notes_root, _parse_year_policy(settings.daily_notes_year_policy))

    def resolve(self, requested_date: str) -> str:
        """The vault-relative path for one ISO date, or a typed refusal.

        `date.fromisoformat` accepts `2026-9-27` on some inputs and the
        round-trip comparison is what closes that: a path built from a date the
        server did not parse back to itself is a path the server cannot
        re-derive later, and the daily-note reader must be able to name the file
        from the date alone.
        """
        try:
            parsed = date.fromisoformat(requested_date)
        except (TypeError, ValueError) as exc:
            raise DailyNotePathError("invalid_daily_note_date") from exc
        if parsed.isoformat() != requested_date:
            raise DailyNotePathError("invalid_daily_note_date")
        if self.years is not None and not self.years[0] <= parsed.year <= self.years[1]:
            raise DailyNotePathError("date_outside_configured_scope")
        return f"{self.root}/{parsed.year:04d}/{requested_date}.md"


def _parse_year_policy(raw: str | None) -> tuple[int, int] | None:
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    if not _YEAR_RANGE.fullmatch(text):
        raise DailyNotePathError("invalid_daily_notes_year_policy")
    low, high = (int(part) for part in text.split("-"))
    return low, high
