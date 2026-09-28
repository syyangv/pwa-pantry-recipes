"""The Pantry read side: the producer's catalog, and later the vault stock index.

`catalog` is a read-only accessor over `wholefoods-to-pantry`'s
`assets/pantry_items.db` — a file that repository owns and commits to its own
git history, so this package only ever reads it. It answers *"which product is
this?"* (`id`, brand, effective basename); it never answers *"do I have it right
now?"*, which is `Logistics/库存/Pantry.md` and is `stock.py`'s job (F1). The
catalog is a lifetime purchase history with no consumption state, so a row
existing there says nothing about stock — `红苋菜苗` (139) and `新鲜小叶茼蒿` (70)
are both in the catalog and neither is currently held.

`stock` does not exist yet. The Stock Join, `line_overrides.yaml`, and the
per-unit `💵` money math belong to a later ticket, and nothing in this package
should be read as a partial stock layer.
"""
