/* Render the F16/F17 headline for every recipe in the golden fixture.
 *
 *   node scripts/render_headlines.mjs < request.json > headlines.json
 *
 * **This file contains no formatting of its own.** It calls the two modules the
 * browser calls — `chipClass()` for whether a slot is missing and `headline()`
 * for the line — on the request `tests/recipes/golden.py` builds from the
 * committed `tests/fixtures/golden_match_results.json` slots, and prints the
 * result. The reason it exists at all is that `format.js` is a browser module:
 * a `?v=__APP_VERSION__` import specifier resolves in the browser and not in
 * Node, so the only honest way to obtain a headline string from a Python
 * regeneration script is to let Node import it exactly as the browser does.
 *
 * The alternative — a Python re-spelling of `format.js` — would be a second
 * implementation of F16/F17 in a repository that has spent several tickets
 * refusing second implementations of everything else, and the two would agree
 * until the first punctuation change. `tests/recipes/golden.py` carries the four
 * frozen forms of §9.13.1 as an *independent* cross-check, and the regeneration
 * script refuses to write when this renderer and that cross-check disagree.
 *
 * `inStock` is not in the request and is passed as `false`: this corpus
 * exercises the recipe→catalog join, which never consults Pantry Stock, and
 * `inStock` selects a chip's class and never its `missing` flag.
 */

import { chipClass } from '../app/static/js/logic/chip-class.js';
import { headline } from '../app/static/js/logic/format.js';

const readStdin = async () => {
  let raw = '';
  for await (const chunk of process.stdin) raw += chunk;
  return raw;
};

const scored = (slots, strict) =>
  slots.map((slot) => ({ ...slot, inStock: false, ...chipClass({ ...slot, strict }) }));

const request = JSON.parse(await readStdin());
const out = {};

for (const [name, recipe] of Object.entries(request.recipes ?? {})) {
  const slots = [
    ...(recipe.materials ?? []).map((slot) => ({ ...slot, isSeasoning: false })),
    ...(recipe.seasonings ?? []).map((slot) => ({ ...slot, isSeasoning: true })),
  ];
  out[name] = {
    headline: headline({ ingredients: scored(slots, false), strict: false }),
    headlineStrict: headline({ ingredients: scored(slots, true), strict: true }),
  };
}

process.stdout.write(JSON.stringify(out, null, 2));
