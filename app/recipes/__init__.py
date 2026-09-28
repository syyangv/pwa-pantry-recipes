"""The recipe read side: the vocabulary core and the vault reader.

`ingredients` parses a `材料` / `调料` frontmatter value into a name and the
shape it was written in; `normalize` reduces a name to the product core both
sides of the matcher compare. Neither module performs I/O, reads a clock, or
reads the environment, which is what makes them testable without an app
(spec §4.1, test seam 2).

`reader` is the one module here that does read: it projects
`<RECIPES_ROOT>/*.md` into `RecipeNote` values and caches them behind a TTL
snapshot. It reads only — no module in this package writes to the vault.
"""
