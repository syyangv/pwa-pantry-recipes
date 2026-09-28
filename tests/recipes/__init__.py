"""Recipe reading, ingredients, normalization, and the brand lexicon.

`test_brand_lexicon.py` is also where the catalog-drift gate lives, and that gate
reads `tests/fixtures/pantry_items_snapshot.json` — the committed catalog dump —
rather than the producer's live `pantry_items.db` (F14), so it runs in every
clone and not only on a machine that has the sibling checkout.
"""
