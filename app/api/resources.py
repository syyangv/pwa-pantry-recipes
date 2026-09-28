"""The `app.state` keys the `lifespan` publishes and the routers read.

**Same neutrality argument as `app/api/envelope.py`, for a different reason.**
`app/main.py` is the only module that *opens* a resource, and the routers are the
only modules that *use* one, so the two have to agree on a name. A bare string
literal in each file is how they would not: rename one and the other silently
reads `None`, which is the `AttributeError`-on-`None` 500 the Cooking Log
router already guards against with its own `COOK_LOG_WRITER_STATE_KEY`. So the
key lives here, in a module that imports nothing from `app.main`, and both sides
import it.

**Reading a resource that the lifespan has not published is a 503 with a named
code, never an `AttributeError`.** `resource_unavailable` for the recipe/pantry
readers, and `cook_log_unavailable` for the writer, which
`app/api/cooklog.py` names itself because the writer key is *its* contract (§9.15
F4) and predates this module.

**`ResourceUnavailable` is a control-flow exception, not an error response.** The
route catches it and returns the envelope; nothing else in the app raises it, and
nothing catches it by accident. Carrying it as a typed exception rather than a
sentinel keeps a missing resource from ever being mistaken for a present one that
happened to be empty — which is the whole difference between "the lifespan has
not run" and "the vault has no recipes".
"""

from __future__ import annotations

from typing import Final

from fastapi import Request

from ..mapping.store import IngredientMappingStore
from ..pantry.catalog import PantryCatalog
from ..pantry.stock import PantryStockIndex
from ..recipes.reader import RecipeIndex

#: §9.19's four readers plus the `AtomicNoteStore` the two vault readers share.
#: Named here rather than in `app/main.py` so the router and the lifespan cannot
#: disagree, which is the same reason the key is not a literal.
RECIPE_INDEX_STATE_KEY: Final = "recipe_index"
PANTRY_CATALOG_STATE_KEY: Final = "pantry_catalog"
PANTRY_STOCK_STATE_KEY: Final = "pantry_stock"
MAPPING_STORE_STATE_KEY: Final = "ingredient_mapping_store"

#: §9.15's 503 for "the lifespan never published this". Distinct from
#: `pantry_stock_unreadable`, which means "the vault's `Pantry.md` could not be
#: read" — one is a wiring state and the other is a user-fixable vault state, and
#: a UI that showed a retry button for both would be wrong about one of them.
RESOURCE_UNAVAILABLE_CODE: Final = "resource_unavailable"


class ResourceUnavailable(RuntimeError):
    """A collaborator the lifespan publishes was not on `app.state`."""

    code = RESOURCE_UNAVAILABLE_CODE


#: The four resources `app.state` can be asked for. A bounded type parameter
#: rather than `Any` so `_get` returns the concrete class the caller asked for —
#: a `-> object` here would push a `cast` into every route, and a `cast` is
#: exactly where a wrong key would go unnoticed.
Resource = RecipeIndex | PantryCatalog | PantryStockIndex | IngredientMappingStore


def recipe_index(request: Request) -> RecipeIndex:
    return _get(request, RECIPE_INDEX_STATE_KEY, RecipeIndex)


def pantry_catalog(request: Request) -> PantryCatalog:
    return _get(request, PANTRY_CATALOG_STATE_KEY, PantryCatalog)


def pantry_stock(request: Request) -> PantryStockIndex:
    return _get(request, PANTRY_STOCK_STATE_KEY, PantryStockIndex)


def mapping_store(request: Request) -> IngredientMappingStore:
    return _get(request, MAPPING_STORE_STATE_KEY, IngredientMappingStore)


def _get[ResourceT: Resource](
    request: Request, key: str, expected: type[ResourceT]
) -> ResourceT:
    value = getattr(request.app.state, key, None)
    if not isinstance(value, expected):
        raise ResourceUnavailable(key)
    return value


__all__ = [
    "MAPPING_STORE_STATE_KEY",
    "PANTRY_CATALOG_STATE_KEY",
    "PANTRY_STOCK_STATE_KEY",
    "RECIPE_INDEX_STATE_KEY",
    "RESOURCE_UNAVAILABLE_CODE",
    "ResourceUnavailable",
    "mapping_store",
    "pantry_catalog",
    "pantry_stock",
    "recipe_index",
]
