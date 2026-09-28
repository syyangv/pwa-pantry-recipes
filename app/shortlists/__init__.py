"""The Meal Shortlists (D3): three PWA-owned, user-ordered planning lists.

`store` owns the vocabulary and the one table this app writes outside the vault.
The three facts a reader of this package should not have to open `store.py` to
learn:

- **A Meal Shortlist is not a vault record.** There is no `餐次` frontmatter key, no
  recipe classification, no grouping of Cooking Records by slot, and no sync-back.
  These rows live in `APP_DATA_DIR/recipes.sqlite3` and nowhere else, which is
  D3's price for keeping the vault meal-free and is recorded as accepted drift in
  spec §13.4.
- **An empty shortlist is a normal empty state.** All three keys are always
  published; `()` for a slot with no rows renders as an invitation, never as an
  error, because a missing key would make "the user has no lunch list" and "this
  build forgot lunch" the same wire answer.
- **A Recipe renamed in Obsidian leaves a row that renders as `⚠ 已重命名`.** The
  row is kept, marked `resolved: false` in-band, and removable by its stored name.
  Nothing auto-prunes it, because dropping user data on a rename is worse than
  showing it broken.

**F12: the three slots are closed and there is no `snack`.** Adding a slot to a
SQLite `CHECK` is a table rebuild, and the offline outbox replays this table, so
the enum is closed now — while the table is empty and the app is unimplemented —
rather than later, when it would be an availability-affecting migration. The three
answer the question the user actually has: "what do I usually eat at lunch".

**The outbox is split across two modules and neither is the store.** `store.py`
enqueues nothing, holds no queue, and accepts no client id — that is unchanged and
is the property #16 fixed. `intents.py` (#23) is the *server* half: the
`shortlist_intents` ledger, the request fingerprint, and the `X-Client-Id`
exactly-once contract that makes a replayed mutation return the first delivery's
bytes. The *client* half — the localStorage queue — is `app/static/js/pwa/outbox.js`
(vendored) plus `app/static/js/domain-intents.js` (this app's own), and the
Cooking Log is on neither, per F5.
"""
