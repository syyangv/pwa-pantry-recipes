"""The fail-closed reader for `app/recipes/lexicon/brands.yaml`.

The lexicon is data, not code: a brand is added or corrected by editing one
YAML line, without a code change. What the reader must guarantee is the
opposite property. A brand lexicon that silently loaded as *empty* would leave
every packaged SKU's leading token attached to the product core, which is a
miss rather than a crash — and the matching tiers would then admit candidates
the lexicon was supposed to have ruled out. So every failure mode here raises
`ConfigurationError` at import time and the process refuses to start.

Import time is the load moment on purpose. `normalize_ingredient()` is on the
matching hot path, so the file is read once and the alternation compiled once;
`normalize.py` binds both at module scope and the per-call work stays pure. The
`source` parameter exists for the tests that prove the failure modes are loud.
It is not an application-level injection hook, and no request can reach it.

The path is resolved through `importlib.resources` against the `app.recipes`
package rather than the current working directory, so it is correct from a source
checkout and from an installed wheel alike. `lexicon/` is a data directory, not
a package: `package-data` globs reach a non-package subdirectory, verified
against a built wheel, and no `__init__.py` is needed there. The `package-data`
entry that ships it is ticket #2's to add; this module needs no change when it
lands.
"""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Final

import yaml

from ..config import ConfigurationError

SCHEMA_VERSION: Final = 1
BRANDS_SOURCE: Final[Traversable] = files(__package__).joinpath("lexicon", "brands.yaml")
_KNOWN_KEYS: Final = frozenset({"version", "brands"})


def load_brand_lexicon(source: Traversable | None = None) -> frozenset[str]:
    """Return the brand set declared by `source`, or raise `ConfigurationError`.

    A missing file, unparseable YAML, a wrong top-level shape, an unrecognised
    key or `version`, a non-string or whitespace-padded entry, a control
    character, a case-insensitive duplicate, and an empty list are all refusals
    to start. An empty result is never a valid answer.
    """
    resolved = BRANDS_SOURCE if source is None else source
    if not resolved.is_file():
        raise ConfigurationError("missing_brand_lexicon")
    try:
        document = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError("unreadable_brand_lexicon") from exc
    if not isinstance(document, dict):
        raise ConfigurationError("invalid_brand_lexicon_document")
    unknown = sorted(str(key) for key in set(document) - _KNOWN_KEYS)
    if unknown:
        raise ConfigurationError(f"unknown_brand_lexicon_keys:{','.join(unknown)}")
    if "version" not in document:
        raise ConfigurationError("missing_brand_lexicon_version")
    if document["version"] != SCHEMA_VERSION:
        raise ConfigurationError("unsupported_brand_lexicon_version")
    entries = document.get("brands")
    if not isinstance(entries, list):
        raise ConfigurationError("invalid_brand_lexicon_brands")
    if not entries:
        raise ConfigurationError("empty_brand_lexicon")
    brands: set[str] = set()
    folded: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, str) or not entry:
            raise ConfigurationError(f"invalid_brand_lexicon_entry:{index}")
        if entry != entry.strip():
            raise ConfigurationError(f"padded_brand_lexicon_entry:{index}")
        if any(ord(character) < 32 or ord(character) == 127 for character in entry):
            raise ConfigurationError(f"control_character_brand_lexicon_entry:{index}")
        if entry.casefold() in folded:
            raise ConfigurationError(f"duplicate_brand_lexicon_entry:{entry}")
        folded.add(entry.casefold())
        brands.add(entry)
    return frozenset(brands)
