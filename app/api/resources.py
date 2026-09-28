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

from ..cooklog.writer import CookingLogWriter
from ..mapping.store import IngredientMappingStore
from ..pantry.catalog import PantryCatalog
from ..pantry.stock import PantryStockIndex
from ..recipes.reader import RecipeIndex
from ..shortlists.intents import ShortlistIntentLedger
from ..shortlists.store import MealShortlistStore

#: The Cooking Log writer's own key, imported rather than re-spelled. §9.15 makes
#: it `app/api/cooklog.py`'s contract and it predates this module, so a second
#: literal here would be exactly the silent-`None` failure this module exists to
#: prevent — the one thing worse now that a *second* router reads it. `cooklog.py`
#: imports nothing from here, so the dependency runs one way.
from .cooklog import COOK_LOG_WRITER_STATE_KEY

#: §9.19's four readers plus the `AtomicNoteStore` the two vault readers share.
#: Named here rather than in `app/main.py` so the router and the lifespan cannot
#: disagree, which is the same reason the key is not a literal.
RECIPE_INDEX_STATE_KEY: Final = "recipe_index"
PANTRY_CATALOG_STATE_KEY: Final = "pantry_catalog"
PANTRY_STOCK_STATE_KEY: Final = "pantry_stock"
MAPPING_STORE_STATE_KEY: Final = "ingredient_mapping_store"

#: The Meal Shortlist store (D3). It is the one resource here that is **not** a
#: reader of the vault — it owns a table in the PWA's SQLite and reads the vault
#: only through a `Callable[[], frozenset[str]]` of recipe basenames — and the key
#: sits here with the rest anyway, because the argument at the top of this module
#: is about the two sides agreeing on a name, not about what the thing is.
MEAL_SHORTLIST_STORE_STATE_KEY: Final = "meal_shortlist_store"

#: §9.18.3's `shortlist_intents` ledger, and the *only* reason the three mutating
#: shortlist routes take a connection of their own. It is published beside the
#: store rather than inside it for the reason this module exists at all: the
#`lifespan` opens it, the router reads it, and a bare string literal in two files
#: is how they would otherwise stop agreeing. It holds no connection (the
#: factory opens and closes one per operation) and no cache, so it is closed by
#: not being held open.
SHORTLIST_INTENT_LEDGER_STATE_KEY: Final = "shortlist_intent_ledger"

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
Resource = (
    RecipeIndex
    | PantryCatalog
    | PantryStockIndex
    | IngredientMappingStore
    | MealShortlistStore
    | ShortlistIntentLedger
    | CookingLogWriter
)


def recipe_index(request: Request) -> RecipeIndex:
    return _get(request, RECIPE_INDEX_STATE_KEY, RecipeIndex)


def pantry_catalog(request: Request) -> PantryCatalog:
    return _get(request, PANTRY_CATALOG_STATE_KEY, PantryCatalog)


def pantry_stock(request: Request) -> PantryStockIndex:
    return _get(request, PANTRY_STOCK_STATE_KEY, PantryStockIndex)


def mapping_store(request: Request) -> IngredientMappingStore:
    return _get(request, MAPPING_STORE_STATE_KEY, IngredientMappingStore)


def meal_shortlist_store(request: Request) -> MealShortlistStore:
    return _get(request, MEAL_SHORTLIST_STORE_STATE_KEY, MealShortlistStore)


def shortlist_intent_ledger(request: Request) -> ShortlistIntentLedger:
    return _get(request, SHORTLIST_INTENT_LEDGER_STATE_KEY, ShortlistIntentLedger)


def cook_log_writer(request: Request) -> CookingLogWriter:
    """The Cooking Log writer, as a READ source.

    A second consumer that is not a write: the recipe detail view needs the
    receipt ledger to report how far `cooking_count` is behind, and it reaches it
    through this accessor rather than through a second connection of its own. It
    is a reader here in the same sense `MealShortlistStore` is — the object on
    `app.state` is shared, and which of its methods a given caller may use is the
    caller's business, not the state's.
    """
    return _get(request, COOK_LOG_WRITER_STATE_KEY, CookingLogWriter)


def _get[ResourceT: Resource](
    request: Request, key: str, expected: type[ResourceT]
) -> ResourceT:
    value = getattr(request.app.state, key, None)
    if not isinstance(value, expected):
        raise ResourceUnavailable(key)
    return value


__all__ = [
    "COOK_LOG_WRITER_STATE_KEY",
    "MAPPING_STORE_STATE_KEY",
    "MEAL_SHORTLIST_STORE_STATE_KEY",
    "PANTRY_CATALOG_STATE_KEY",
    "PANTRY_STOCK_STATE_KEY",
    "RECIPE_INDEX_STATE_KEY",
    "RESOURCE_UNAVAILABLE_CODE",
    "SHORTLIST_INTENT_LEDGER_STATE_KEY",
    "ResourceUnavailable",
    "cook_log_writer",
    "mapping_store",
    "meal_shortlist_store",
    "pantry_catalog",
    "pantry_stock",
    "recipe_index",
    "shortlist_intent_ledger",
]
