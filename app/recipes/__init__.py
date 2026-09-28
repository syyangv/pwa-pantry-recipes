"""Pure vocabulary core for recipe Ingredients and Seasonings.

`ingredients` parses a `材料` / `调料` frontmatter value into a name and the
shape it was written in; `normalize` reduces a name to the product core both
sides of the matcher compare. Neither module performs I/O, reads a clock, or
reads the environment, which is what makes them testable without an app
(spec §4.1, test seam 2).
"""
