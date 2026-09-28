/* D4's display sort, three levels, a total order.
 *
 * 1. foundRatio descending, `found / max(total, 1)`.
 * 2. lastCooked descending — a Recipe with no `last_cooked` sorts last within
 *    its ratio bucket, not globally.
 * 3. noteName ascending, code-point order.
 *
 * Code-point order, not `localeCompare`: a locale-sensitive comparison would
 * make the rendered order depend on the device's locale, and the browser flow
 * asserts on this order. `lastCooked` is ISO `YYYY-MM-DD` frontmatter, so
 * plain string comparison is chronological and cannot be shifted by a
 * timezone. foundRatio is compared as a cross product rather than a quotient,
 * so no float rounding can reorder two Recipes.
 *
 * No threshold, no collapse, no "show more": a 0/6 Recipe is a first-class row
 * (D4). This function filters nothing and mutates nothing — it returns a new
 * array in a new order and leaves the caller's array alone.
 *
 * Pure: no DOM, no fetch, no storage, no clock.
 */

export function sortRecipes(recipes) {
  return [...recipes].sort(compareRecipes);
}

function compareRecipes(a, b) {
  const ratio = a.found * Math.max(b.total, 1) - b.found * Math.max(a.total, 1);
  if (ratio !== 0) return ratio > 0 ? -1 : 1;

  const cookedA = a.lastCooked || null;
  const cookedB = b.lastCooked || null;
  if (cookedA !== cookedB) {
    if (cookedA === null) return 1;
    if (cookedB === null) return -1;
    return cookedA < cookedB ? 1 : -1;
  }

  if (a.noteName < b.noteName) return -1;
  if (a.noteName > b.noteName) return 1;
  return 0;
}
