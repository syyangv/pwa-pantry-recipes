/* The five chip buckets of D4, in resolution order.
 *
 * Order is the contract, not an implementation detail: `manual` and `staples`
 * answer before the stock tiers are consulted, so a hand fix and a staples
 * assumption both outrank what the catalog says. F16 lives in the gap between
 * the `staples` return and the `isSeasoning && !strict` test — a
 * staple-satisfied Seasoning is found, and only an unresolved Seasoning is
 * missing.
 *
 * **A catalog match without stock is MISSING, not found** — the headline counts
 * Pantry Stock, never Pantry Item. The Pantry Item catalog is an immutable record
 * of what has been *bought*; a `茼蒿` line in `pantry_items.db` says the
 * household once bought it, and says nothing about the shelf. Scoring that as
 * found published `2/2 ingredients found` above a purple chip on `煮菜菜` — a
 * recipe the app called cookable while showing the evidence that it is not, and
 * the only form of that sentence a user can act on is the honest one. The
 * `manual` branch is deliberately exempt: a hand fix outranks the stock tier
 * above, so a mapped-but-unheld ingredient stays found there.
 *
 * `outOfStock` is the reason, kept separate from the verdict so a consumer can
 * tell "you bought it and it is gone" (a Pantry Item exists, no open Pantry
 * Stock) from "no Pantry Item was ever resolved" (`chip--missing`). Both are
 * `missing: true`; only the first carries `outOfStock: true`. The chip's colour
 * already separated them, and the flag is the same fact as data, so the 调试
 * view can read it without re-deriving it from the five inputs.
 *
 * Pure: no DOM, no fetch, no storage, no clock. The `missing`, `assumed` and
 * `outOfStock` flags are what the headline is scored from, so they must not
 * depend on how the chip happens to be painted.
 */

export function chipClass({ matchMethod, pantryItemId, inStock, isSeasoning, strict }) {
  if (matchMethod === 'manual')
    return {
      className: inStock ? 'chip--in-stock chip--manual' : 'chip--have-been-buying chip--manual',
      missing: false,
      assumed: false,
      outOfStock: false,
    };
  if (matchMethod === 'staples')
    return { className: 'chip--assumed-staple', missing: false, assumed: true, outOfStock: false };
  if (pantryItemId !== null && pantryItemId !== undefined)
    return {
      className: inStock ? 'chip--in-stock' : 'chip--have-been-buying',
      missing: !inStock,
      assumed: false,
      outOfStock: !inStock,
    };
  if (isSeasoning && !strict)
    return { className: 'chip--ignored-seasoning', missing: false, assumed: true, outOfStock: false };
  return { className: 'chip--missing', missing: true, assumed: false, outOfStock: false };
}
