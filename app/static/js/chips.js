/* The chip row — one chip per Ingredient and per Seasoning, in SLOT ORDER.
 *
 * **Slot order, never sorted (§9.13.2).** The recipe's own order is
 * information: it is the order the note's author wrote, and sorting it would
 * silently discard that. `sortRecipes()` orders the RECIPES; nothing in this
 * module orders the slots.
 *
 * **The five classes are produced by `logic/chip-class.js`, never here.** That
 * module is the single implementation of D4's ladder, it is `node --test`ed on
 * its own, and its branches are F16's whole argument. This file's only job is to
 * turn a slot plus `chipClass()`'s answer into DOM, so a change to the ladder
 * cannot be "fixed" in a second place and diverge.
 *
 * **`headline()` is scored from the same classification.** `scoredSlots()`
 * spreads `{missing, assumed}` onto each slot, which is exactly what
 * `logic/format.js` reads — so the number in the headline and the colour of the
 * chips come from one call, and a chip that is painted cannot disagree with the
 * sentence above it.
 *
 * **`data-match-method` / `data-tier` / `data-item-id` are the provenance
 * surface, and they are a DOM concern rather than a second data path** (§9.13.2,
 * F7). The 调试 view reads them off the rendered tree; there is no
 * `?debug=1`, no second response, and no second fetch. `data-join-state` and
 * `data-seasoning` ride along for the same reason.
 *
 * `el()` is textContent-only, so an ingredient name — which comes from the
 * user's own vault — can never become markup.
 */

import { el } from './dom.js?v=__APP_VERSION__';
import { chipClass } from './logic/chip-class.js?v=__APP_VERSION__';

/** D4's five buckets, exactly. A sixth class here is a bug in a view. */
export const CHIP_CLASSES = Object.freeze([
  'chip--in-stock',
  'chip--have-been-buying',
  'chip--assumed-staple',
  'chip--missing',
  'chip--ignored-seasoning',
]);

/** The name a chip shows: `parsed_name` when the parser produced one. */
export function slotLabel(slot) {
  return slot.parsedName || slot.rawValue || '';
}

/** `logic/chip-class.js`'s answer for one slot, at one strict setting. */
export function classifySlot(slot, strict) {
  return chipClass({
    matchMethod: slot.matchMethod,
    pantryItemId: slot.pantryItemId,
    inStock: slot.inStock,
    isSeasoning: slot.isSeasoning,
    strict,
  });
}

/**
 * Every slot with `{missing, assumed, className}` spread on, which is the shape
 * both `headline()` and `chipRow()` consume. One classification per render.
 */
export function scoredSlots(ingredients = [], strict = false) {
  return ingredients.map((slot) => ({ ...slot, ...classifySlot(slot, strict) }));
}

/** `tier N · match_method · #id · confidence` — the 调试 secondary line. */
export function provenanceText(slot) {
  const id =
    slot.pantryItemId === null || slot.pantryItemId === undefined ? '—' : `#${slot.pantryItemId}`;
  return `tier ${slot.matchTier} · ${slot.matchMethod} · ${id} · ${slot.confidence}`;
}

/**
 * One chip. `slot` must already be classified (or `scoredSlots` was used), and
 * `strict` is passed through only so a caller can classify a raw slot itself.
 */
export function chip(slot, { strict = false, debug = false } = {}) {
  // An already-scored slot IS the classifier's answer, spread on by
  // `scoredSlots` — so its flags are read, not recomputed. Only an unscored slot
  // needs a second call, and rebuilding the verdict from `className` alone would
  // drop `outOfStock` on every pre-scored chip.
  const verdict = slot.className ? slot : classifySlot(slot, strict);
  const node = el('span', {
    class: `chip ${verdict.className}`,
    dataset: {
      matchMethod: String(slot.matchMethod),
      tier: String(slot.matchTier),
      itemId: slot.pantryItemId === null || slot.pantryItemId === undefined ? '' : String(slot.pantryItemId),
      joinState: String(slot.stockJoinState),
      seasoning: slot.isSeasoning ? 'true' : 'false',
      // The classifier's own verdict, as data rather than as a class name, so
      // the 调试 view can read WHICH bucket a chip landed in without
      // re-deriving it from the five inputs. `outOfStock` separates "a Pantry
      // Item exists but nothing is on the shelf" from "nothing was ever
      // resolved" — two chips that are both `chip--missing` for the score but
      // not for the same reason.
      bucket: verdict.className,
      outOfStock: verdict.outOfStock ? 'true' : 'false',
    },
    text: slotLabel(slot),
  });
  if (debug) {
    node.appendChild(
      el('span', { class: 'chip__provenance', dataset: { role: 'provenance' }, text: provenanceText(slot) }),
    );
  }
  return node;
}

/**
 * The row. `.scroll-row` is the vendored flex/nowrap/overflow-x container and
 * `.chip-row` supplies the vertical padding that makes the 32px chip sit in a
 * 44px band — the app's only horizontal scroller, as §9.4 requires.
 */
export function chipRow({ ingredients = [], strict = false, debug = false } = {}) {
  const slots = ingredients.length > 0 && ingredients[0].className ? ingredients : scoredSlots(ingredients, strict);
  return el('div', { class: 'scroll-row chip-row', dataset: { role: 'chips' } }, slots.map((slot) => chip(slot, { strict, debug })));
}
