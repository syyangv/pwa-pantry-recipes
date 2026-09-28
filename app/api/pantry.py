"""`GET /api/pantry/items?q=&limit=` — the catalog search behind the manual
re-map picker.

**The smallest surface in the app, and the one with the most restraint in it.**
Its whole job is to let a user type a Pantry Item's name and pick one of the rows
the catalog holds, so that `PUT /api/recipes/{name}/ingredients/{i}/mapping` can
be called with a real `pantryItemId`. It is therefore a **strict prefix of
`CatalogRow`**, and the fields it omits are omissions with reasons:

- **`last_price`, `first_seen`, `last_seen`, `order_count` are never published,
  anywhere, in any route.** They are Pantry-Write Contract data owned by
  `wholefoods-to-pantry`, and this app is a read-only consumer of a sibling
  project's committed asset (`AGENTS.md` #4). `CatalogRow` does not even project
  them — reading them would require a second, wider reader over `items` and
  would invite a future caller to treat the catalog's purchase history as
  current stock, which is the Pantry Item / Pantry Stock conflation
  `CONTEXT.md` forbids. §9.16's table row lists a `lastSeen` key; this route does
  not publish it, and the ticket's non-goals ("Emit `last_price`, `first_seen`,
  `last_seen`, or `order_count` in any response") are the tighter of the two
  constraints. Recorded here rather than left for a reader to discover.

- **`area` is published and is always `null`.** Not a placeholder and not a
  guess: `PantryCatalog` drops every row whose `area` is not NULL *before* it
  builds a `CatalogRow`, so every row in the candidate universe provably has a
  NULL area. Publishing the key keeps §9.16's field list intact and states a
  fact rather than hiding one.

**`CONTEXT.md`'s Pantry Category is a code and is published as a code.** §5.3
records that no code-to-display-text mapping exists and that inferring one from
the digits would be inventing vocabulary, so `category` is `1.1c` and the UI is
responsible for not pretending to know what that means.

**The route never writes.** There is no creation path in this repository, so
there is no `POST /api/pantry/items`, no Pantry Item creation, and no way to
reach `pantry_items.db` in write mode — the connection is opened through
`mode=ro` with `PRAGMA query_only=ON` (`app/pantry/catalog.py`).

**A `null` result is a 422, never an empty list.** `?q=` and `?limit=` are
validated by FastAPI before the handler runs, so a blank query string and an
out-of-range limit are 422 `invalid_request` and never reach the catalog. A query
that simply matches nothing is a 200 with `items: []`, which is a real answer:
"I have no Pantry Item by that name" is different from "your query was not a
query", and conflating them is the plausible-looking wrong answer
`AGENTS.md` #3 rules out.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from ..pantry.catalog import CatalogError, CatalogRow, fold_name
from .envelope import api_error
from .resources import ResourceUnavailable, pantry_catalog

#: The picker's page size. Bounded so a client cannot ask the catalog for all 178
#: rows through a search box, and so a typo'd `limit` is a 422 rather than a
#: silently different result set.
DEFAULT_LIMIT: Final = 20
MAX_LIMIT: Final = 50

#: §9.16's 503 for an unreadable catalog, shared with `app/api/recipes.py`'s
#: constant so the two cannot spell it differently. Both mean "a source this app
#: depends on is unreadable" and both are permanent until the user fixes it.
PANTRY_DB_UNREADABLE: Final = "pantry_db_unreadable"


def build_pantry_router() -> APIRouter:
    """`GET /api/pantry/items` and only that. §9.16's table is the whole surface.

    Takes no arguments, for the same reason every other router here does: the
    `lifespan` opens and closes the `PantryCatalog` and a router that opened its
    own would be a second, unclosed SQLite connection.
    """
    router = APIRouter()

    @router.get("/api/pantry/items")
    async def search_items(
        request: Request,
        q: str | None = Query(default=None),
        limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    ) -> JSONResponse:
        """Search the catalog by substring over the name and its aliases.

        `q` is a *substring* search and not a lookup: the picker is for a user
        typing half a product name, and `CatalogSnapshot.candidates_for` is an
        exact-folded-key lookup built for the matcher's tiers, which would return
        nothing for a partial name. Folding is still `fold_name` — the one
        normalizer — so the comparison is NFKC + trim + casefold on both sides and
        a half-width/half-case typo still matches.

        No `q` at all returns the first `limit` rows in `id` order, which is a
        real answer ("here is the catalog") and not a way to dump the table: the
        bound is `MAX_LIMIT`, so the picker cannot be used as an export.
        """
        try:
            catalog = pantry_catalog(request).snapshot()
        except CatalogError:
            return api_error(request, 503, PANTRY_DB_UNREADABLE)
        except ResourceUnavailable as exc:
            return api_error(request, 503, str(exc))
        if q is not None and not q.strip():
            # A blank query is a malformed request, not a search for the empty
            # string. Answering it with the first `limit` rows would make a
            # client bug look like a successful search.
            return api_error(request, 422, "invalid_pantry_query")
        rows = _search(catalog.rows, q, limit)
        return JSONResponse(
            {
                "items": [_item(row) for row in rows],
                "catalogRevision": catalog.catalog_revision,
            }
        )

    return router


def _search(rows: tuple[CatalogRow, ...], q: str | None, limit: int) -> list[CatalogRow]:
    """The matching rows, in `id` order, at most `limit`.

    `rows` is already in `id` order (`build_snapshot` sorts it), so the result is
    deterministic and a limit of 20 is the same 20 rows on every call — which is
    what makes a picker that re-queries as the user types usable at all.
    """
    if q is None or not q.strip():
        return list(rows[:limit])
    needle = fold_name(q)
    matched: list[CatalogRow] = []
    for row in rows:
        haystacks = (row.canonical_name, *row.variants, row.basename)
        if any(needle in fold_name(haystack) for haystack in haystacks):
            matched.append(row)
            if len(matched) == limit:
                break
    return matched


def _item(row: CatalogRow) -> dict[str, Any]:
    """One catalog row's exact wire shape — §9.16's picker fields.

    See the module docstring for `area` always being `null` and for the four
    Pantry-Write Contract columns that are deliberately absent.
    """
    return {
        "id": row.id,
        "canonicalName": row.canonical_name,
        "category": row.category,
        "area": None,
        "variants": list(row.variants),
    }


__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "PANTRY_DB_UNREADABLE", "build_pantry_router"]
