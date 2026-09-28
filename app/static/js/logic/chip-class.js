/* The five chip buckets of D4, in resolution order.
 *
 * Order is the contract, not an implementation detail: `manual` and `staples`
 * answer before the stock tiers are consulted, so a hand fix and a staples
 * assumption both outrank what the catalog says. F16 lives in the gap between
 * the `staples` return and the `isSeasoning && !strict` test — a
 * staple-satisfied Seasoning is found, and only an unresolved Seasoning is
 * missing.
 *
 * Pure: no DOM, no fetch, no storage, no clock. The `missing` and `assumed`
 * flags are what the headline is scored from, so they must not depend on how
 * the chip happens to be painted.
 */

export function chipClass({ matchMethod, pantryItemId, inStock, isSeasoning, strict }) {
  if (matchMethod === 'manual')
    return {
      className: inStock ? 'chip--in-stock chip--manual' : 'chip--have-been-buying chip--manual',
      missing: false,
      assumed: false,
    };
  if (matchMethod === 'staples')
    return { className: 'chip--assumed-staple', missing: false, assumed: true };
  if (pantryItemId !== null && pantryItemId !== undefined)
    return {
      className: inStock ? 'chip--in-stock' : 'chip--have-been-buying',
      missing: false,
      assumed: false,
    };
  if (isSeasoning && !strict)
    return { className: 'chip--ignored-seasoning', missing: false, assumed: true };
  return { className: 'chip--missing', missing: true, assumed: false };
}
