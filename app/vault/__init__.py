"""Hardened, revision-aware vault access: atomic write, frontmatter, sections, paths.

Four primitives, ported from `pwa-obsidian-daily` and trimmed to what this app
uses. They share one discipline: **every read and every write is a byte span
over the note's exact bytes**, so nothing in a live Obsidian file is ever
re-serialized, reordered, or reformatted by this app.
"""

from .atomic_write import (
    AtomicNoteStore,
    ConcurrentFileChange,
    ConcurrentFileExists,
    PathSafetyError,
    PostWriteVerificationError,
)
from .daily_paths import DailyNotePathError, DailyNotePathPolicy
from .frontmatter import (
    AmbiguousFrontmatter,
    FieldSnapshot,
    FieldValue,
    FrontmatterConflict,
    FrontmatterDocument,
    FrontmatterError,
    note_revision,
    parse_frontmatter,
    patch_frontmatter_field,
)
from .sections import (
    AmbiguousSection,
    Region,
    SectionError,
    SectionsDocument,
    insert_after_heading,
    parse_sections,
)

__all__ = [
    "AmbiguousFrontmatter",
    "AmbiguousSection",
    "AtomicNoteStore",
    "ConcurrentFileChange",
    "ConcurrentFileExists",
    "DailyNotePathError",
    "DailyNotePathPolicy",
    "FieldSnapshot",
    "FieldValue",
    "FrontmatterConflict",
    "FrontmatterDocument",
    "FrontmatterError",
    "PathSafetyError",
    "PostWriteVerificationError",
    "Region",
    "SectionError",
    "SectionsDocument",
    "insert_after_heading",
    "note_revision",
    "parse_frontmatter",
    "parse_sections",
    "patch_frontmatter_field",
]
