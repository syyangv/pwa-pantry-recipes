"""The Cooking Log writer — **the one vault write this app performs** (D2).

`CookingLogWriter.append` reads a daily note, splices one `- [[RecipeName]]`
list item under its `# 笔记` heading (F3), and commits the result through
`AtomicNoteStore.transform_existing`. Nothing here creates, renames, or
re-serializes a note: the patch is a byte splice over offsets computed from the
original source, and a missing daily note is a named error rather than an
automatic creation (F4).

`app.api.cooklog` is the HTTP surface over it; `app/vault/sections.py` and
`app/vault/atomic_write.py` are the shipped primitives this module uses and does
not re-implement.
"""
