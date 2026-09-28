"""The Pantry read side: the producer's catalog, and later the vault stock index.

`catalog` is a read-only accessor over `wholefoods-to-pantry`'s
`assets/pantry_items.db` — a file that repository owns and commits to its own
git history, so this package only ever reads it. It answers *"which product is
this?"* (`id`, brand, effective basename); it never answers *"do I have it right
now?"*, which is `Logistics/库存/Pantry.md` and is `stock.py`'s job (F1). The
catalog is a lifetime purchase history with no consumption state, so a row
existing there says nothing about stock — `红苋菜苗` (139) and `新鲜小叶茼蒿` (70)
are both in the catalog and neither is currently held.

`stock` is the other half: the live `Logistics/库存/Pantry.md` answers *"do I
have it right now?"* and `stock.py` joins each open line to the catalog's
rename-stable `pantry_item_id` through a three-tier normalized-name join
(exact, basename, then the committed `line_overrides.yaml`). That join is
deliberately weaker than the recipe→ingredient join and has no tier ladder or
re-resolution pass, so a line it misses is absent from `in_stock_ids` and
surfaces in its own `unjoined` bucket instead of being dropped silently. It also
fails closed: a missing, unreadable, or unparseable pantry note raises rather
than degrading to "nothing held", because the alternative deflate every chip on
every recipe to a plausible-looking wrong answer.
"""
