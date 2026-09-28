/* The headline line: the score D4 publishes instead of a boolean.
 *
 * F17 — the count is `材料` only in the default view, and the missing list is
 * the Materials-only list, so a 调料 name can never appear in it. F16 — under
 * 严格模式 the 调料 slots join the count too, and a Seasoning the classifier did
 * not mark missing (a staples-satisfied one) adds to the numerator.
 *
 * The two clauses are separately labelled: the Materials-only list, then three
 * spaces, then the strict addendum naming the Seasonings that are unresolved
 * and nothing else. Those four strings are frozen by tests/js/logic/format.test.mjs.
 *
 * `missing` on each slot is the classifier's output (chipClass.js), spread onto
 * the slot by the caller. This module does not import the classifier: a
 * `?v=__APP_VERSION__` import specifier is correct in the browser and
 * unresolvable in Node, so keeping the two modules import-free is what lets one
 * test file drive both and freeze the real strings.
 *
 * Pure: no DOM, no fetch, no storage, no clock, no locale-sensitive
 * comparison, so the string is byte-stable.
 */

export function headline({ ingredients = [], strict = false }) {
  const materials = ingredients.filter((slot) => !slot.isSeasoning);
  const seasonings = ingredients.filter((slot) => slot.isSeasoning);
  const scored = strict ? ingredients : materials;
  const found = scored.filter((slot) => !slot.missing).length;

  const missingNames = (slots) =>
    slots.filter((slot) => slot.missing).map((slot) => slot.parsedName || slot.rawValue || '');

  const missingMaterials = missingNames(materials);
  const missingSeasonings = strict ? missingNames(seasonings) : [];

  let line = `${found}/${scored.length} ingredients found`;
  if (missingMaterials.length > 0) line += ` — missing: ${missingMaterials.join(', ')}`;
  if (missingSeasonings.length > 0)
    line += `   (严格模式（含调料）: ${missingSeasonings.join(', ')})`;
  return line;
}
