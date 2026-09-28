"""The fail-closed readers for the matcher's two closed lexicons.

`synonyms.yaml` and `staples.yaml` are data, exactly as `brands.yaml` is: a
synonym or a staple is added by editing one YAML line, with no code change. And
exactly as with the brand lexicon, the reader's job is to guarantee the
*opposite* property — that a malformed file is loud rather than quietly empty.
The blast radius is the same and it is the reason this is a separate concern:

* A silently empty `synonyms.yaml` costs the tier-6 vocabulary and nothing else:
  the ladder still refuses to guess, so the failure is a **miss**.
* A silently empty `staples.yaml` costs the assumed-on-hand treatment of
  `调料`, which §9.9 measures as the difference between ~5% and ~90% Seasoning
  coverage. A miss again, but a large one, and it would look like the user's
  frontmatter rather than like our bug.
* A **malformed** one is worse than either: a synonym entry that parsed into a
  `{"canonical": null}` shape, or a `families: [1.1]` written unquoted — PyYAML
  reads that as the float `1.1`, and `1.10` as `1.1` — can silently widen the
  category-family guard and admit a snack row for a condiment. That is the
  false-positive class D1 exists to prevent, so every one of these raises
  `ConfigurationError` at import time and the process refuses to start.

**Read once, at import.** The ladder is a pure function on the request path and
must not touch the filesystem per Ingredient, so `matcher.py` binds `SYNONYMS`,
`STAPLES` and `SYNONYM_INDEX` at module scope from the two `load_*` functions
here — the same shape `normalize.py` uses for `BRAND_LEXICON`, and the same
reason: a malformed file must stop the boot rather than silently change what a
recipe is allowed to match. `source` exists for the tests that prove the failure
modes are loud; it is not an application-level injection hook and no request can
reach it.

**A Pantry Item id can never appear in either file.** `items.id` is
`INTEGER PRIMARY KEY AUTOINCREMENT` and a producer re-import renumbers it, so a
lexicon entry is a *name predicate* resolved against the live catalog at read
time. Both loaders therefore reject a value that is nothing but an integer: it
is always a mistake, because a bare number is not a Pantry Item's name.

Schema versions are separate from the brand lexicon's. The three files are
independent hand-maintained artifacts with independent histories, and one
`version: 2` in `brands.yaml` must not be able to invalidate a perfectly good
`staples.yaml`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Final

import yaml
from yaml.nodes import MappingNode

from ..config import ConfigurationError

#: Both files ship schema revision 1. A loader that does not recognise the
#: value refuses to start rather than misreading the file.
SYNONYMS_VERSION: Final = 1
STAPLES_VERSION: Final = 1

SYNONYMS_SOURCE: Final[Traversable] = files(__package__).joinpath("lexicon", "synonyms.yaml")
STAPLES_SOURCE: Final[Traversable] = files(__package__).joinpath("lexicon", "staples.yaml")

_KNOWN_SYNONYM_KEYS: Final = frozenset({"version", "synonyms"})
_KNOWN_STAPLE_KEYS: Final = frozenset({"version", "staples", "class_suffixes"})
_KNOWN_ENTRY_KEYS: Final = frozenset({"canonical", "match", "families"})

#: A name is not a paragraph. The same shape `app/pantry/stock.py` uses for its
#: override file, and for the same reason: it keeps a pasted dataview block or a
#: whole note out of the index. 64 is far above the longest real synonym
#: (`Chicken Wings`) and far below any line worth indexing.
MAX_NAME_CHARS: Final = 64
#: A Pantry Category *code*, not a label: `1.1`, `1.1c`, `4`, `2`, `skip`.
#: `CONTEXT.md` defines no code-to-display-text mapping, so the matcher uses the
#: code only as a food-class predicate and must never infer a label from it.
#: The pattern is what stops the unquoted-float trap above from being a
#: behaviour change: `1.10` and `1.1` would both reach the guard as `1.1`.
_CATEGORY_CODE: Final = re.compile(r"(?:[0-9]+(?:\.[0-9]+)*[a-z]*|skip)\Z")


@dataclass(frozen=True)
class SynonymEntry:
    """One closed equivalence class: a canonical name, its aliases, its classes.

    `families` is the **food-class allowlist** the tier-6 category-family guard
    filters candidates by, and it is mandatory. An entry without one would let
    any family through, which is the guard switched off while still looking like
    a guard — so the loader refuses rather than defaulting.

    `names` is `canonical` plus `match`, in that order, and is the only thing
    the matcher iterates. A name may appear in exactly **one** entry across the
    whole file: `Lemon↔柠檬` and `Lime↔青柠` being separate entries is a
    requirement, and a duplicate name would make that distinction unobservable.
    """

    canonical: str
    match: tuple[str, ...]
    families: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        """Every spelling in the class, canonical first. Never empty."""
        return (self.canonical, *self.match)

    def allows(self, family: str) -> bool:
        """True when a candidate in `family` may be adopted by this entry."""
        return family in self.families


@dataclass(frozen=True)
class StaplesLexicon:
    """The assumed-on-hand Seasonings, plus the class-suffix rule (§9.9).

    `class_suffixes` is what lets `调味酱油` resolve when `酱油` is the staple:
    any `调料` whose normalized name *ends with* one of these is treated as the
    class staple. It is a suffix rule and not a substring rule, because
    `酱油` appears inside `蒸鱼豉油` and inside `老抽` without those being
    anything other than soy — a substring rule would make every name containing
    a condiment character a staple, which is a class assumption, not a match.
    """

    staples: frozenset[str]
    class_suffixes: tuple[str, ...]

    def is_staple(self, name: str) -> bool:
        """True when `name` is on the list, or is a class-suffix form of one.

        The comparison is on the NFKC-normalized, casefolded name, so a
        `dashi` written `Dashi` or `Ｄａｓｈｉ` is the same staple. The list is
        matched whole — never as a substring — so `芝麻` is not a staple because
        `芝麻油` is, which is the distinction the live data turns on: `芝麻油` is
        assumed on hand, plain `芝麻` is not.
        """
        key = _fold(name)
        if key in self.staples:
            return True
        return any(key.endswith(_fold(suffix)) for suffix in self.class_suffixes)


def load_synonyms(source: Traversable | None = None) -> tuple[SynonymEntry, ...]:
    """Return the synonym classes declared by `source`, or raise.

    Refused, all as `ConfigurationError`: a missing file, unreadable bytes,
    unparseable YAML, a non-mapping document, an unknown top-level key, a
    missing or unrecognised `version`, a missing / non-list / empty `synonyms`,
    an entry that is not a mapping or carries an unknown key, a missing or empty
    `canonical` / `match` / `families`, a blank / padded / control-character /
    over-long / all-digits name, a `families` entry that is not a string or is
    not a Pantry Category code, a duplicate name across the file, and a
    duplicate `canonical`. An empty result is never a valid answer.
    """
    resolved = SYNONYMS_SOURCE if source is None else source
    if not resolved.is_file():
        raise ConfigurationError("missing_synonym_lexicon")
    try:
        document = yaml.load(resolved.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError("unreadable_synonym_lexicon") from exc
    if not isinstance(document, dict):
        raise ConfigurationError("invalid_synonym_lexicon_document")
    unknown = sorted(str(key) for key in set(document) - _KNOWN_SYNONYM_KEYS)
    if unknown:
        raise ConfigurationError(f"unknown_synonym_lexicon_keys:{','.join(unknown)}")
    _check_version(document, "synonym_lexicon", SYNONYMS_VERSION)
    entries = document.get("synonyms")
    if not isinstance(entries, list):
        raise ConfigurationError("invalid_synonym_lexicon_synonyms")
    if not entries:
        raise ConfigurationError("empty_synonym_lexicon")

    parsed: list[SynonymEntry] = []
    seen_names: dict[str, str] = {}
    seen_canonicals: set[str] = set()
    for index, raw_entry in enumerate(entries):
        entry = _read_synonym_entry(raw_entry, index)
        folded_canonical = entry.canonical.casefold()
        if folded_canonical in seen_canonicals:
            raise ConfigurationError(f"duplicate_synonym_canonical:{entry.canonical}")
        seen_canonicals.add(folded_canonical)
        for name in entry.names:
            key = name.casefold()
            if key in seen_names:
                raise ConfigurationError(
                    f"ambiguous_synonym_name:{name}:already_in:{seen_names[key]}"
                )
            seen_names[key] = entry.canonical
        parsed.append(entry)
    return tuple(parsed)


def load_staples(source: Traversable | None = None) -> StaplesLexicon:
    """Return the Seasonings allowlist declared by `source`, or raise.

    Same refusal list as the synonym loader, with `class_suffixes` in place of
    the per-entry fields. `class_suffixes` is **required and non-empty** even
    though it is optional-looking behaviour: an empty list would leave §9.9's
    `X酱油 → 酱油` rule silently dead, and a rule that is dead but present is
    the shape of bug nobody notices. A Pantry Item id can never appear in
    either list, so an all-digits name is refused in both.
    """
    resolved = STAPLES_SOURCE if source is None else source
    if not resolved.is_file():
        raise ConfigurationError("missing_staples_lexicon")
    try:
        document = yaml.load(resolved.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError("unreadable_staples_lexicon") from exc
    if not isinstance(document, dict):
        raise ConfigurationError("invalid_staples_lexicon_document")
    unknown = sorted(str(key) for key in set(document) - _KNOWN_STAPLE_KEYS)
    if unknown:
        raise ConfigurationError(f"unknown_staples_lexicon_keys:{','.join(unknown)}")
    _check_version(document, "staples_lexicon", STAPLES_VERSION)

    staples = _read_names(document.get("staples"), "staples_lexicon_staples", "staples")
    if not staples:
        # An empty allowlist would leave every `调料` unresolved and look exactly
        # like a catalog with no condiments, which §9.9's 1.1c row count says is
        # the *permanent* state. Never a valid answer.
        raise ConfigurationError("empty_staples_lexicon")
    _refuse_duplicates(staples, "staples_lexicon_staples")
    suffixes = document.get("class_suffixes")
    if not isinstance(suffixes, list):
        raise ConfigurationError("invalid_staples_lexicon_class_suffixes")
    checked = _read_names(suffixes, "staples_lexicon_class_suffixes", "class_suffixes")
    if not checked:
        raise ConfigurationError("empty_staples_lexicon_class_suffixes")
    _refuse_duplicates(checked, "staples_lexicon_class_suffixes")
    return StaplesLexicon(staples=frozenset(staples), class_suffixes=checked)


def synonym_index(entries: tuple[SynonymEntry, ...]) -> dict[str, SynonymEntry]:
    """`folded name -> entry` for one lookup per Ingredient.

    Built once and cached beside the lexicon rather than rebuilt per resolve: a
    linear scan over ~29 entries times every Ingredient of every recipe is the
    kind of cost that makes people weaken a guard to make it fast.
    """
    return {name.casefold(): entry for entry in entries for name in entry.names}


class _UniqueKeyLoader(yaml.SafeLoader):
    """A `SafeLoader` that refuses a duplicate mapping key.

    `yaml.safe_load` keeps the **last** of two identical keys and says nothing,
    so copy-pasting a synonym entry would silently replace the one above it — and
    the replacement could easily be a wider `families` allowlist than the entry
    it replaced, which is a guard change with no diff in the file.
    """

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[object, object]:
        seen: set[object] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise yaml.YAMLError(f"duplicate key: {key!r}")
                seen.add(key)
            except TypeError:  # an unhashable key; the shape check names it
                continue
        return super().construct_mapping(node, deep=deep)


def _refuse_duplicates(names: tuple[str, ...], label: str) -> None:
    """A case-insensitive duplicate in a list-valued lexicon field is refused.

    `油` and `DASHI` and `dashi` in one list is a typo, and a typo here is not
    visible: the list still resolves, still covers the same value, and the
    duplicate is dead weight that reads as a deliberate entry. `brands.yaml`'s
    loader refuses the same shape, and the two should behave identically.
    """
    folded: set[str] = set()
    for name in names:
        key = name.casefold()
        if key in folded:
            raise ConfigurationError(f"duplicate_{label}:{name}")
        folded.add(key)


def _check_version(document: dict[object, object], label: str, expected: int) -> None:
    if "version" not in document:
        raise ConfigurationError(f"missing_{label}_version")
    version = document["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != expected:
        raise ConfigurationError(f"unsupported_{label}_version")


def _read_synonym_entry(raw_entry: object, index: int) -> SynonymEntry:
    if not isinstance(raw_entry, dict):
        raise ConfigurationError(f"invalid_synonym_lexicon_entry:{index}")
    unknown = sorted(str(key) for key in set(raw_entry) - _KNOWN_ENTRY_KEYS)
    if unknown:
        raise ConfigurationError(f"unknown_synonym_lexicon_entry_keys:{index}:{','.join(unknown)}")
    for required in _KNOWN_ENTRY_KEYS:
        if required not in raw_entry:
            raise ConfigurationError(f"missing_synonym_lexicon_entry_field:{index}:{required}")
    canonical = _read_name(raw_entry["canonical"], index, "canonical")
    matches = _read_names(
        raw_entry["match"], f"synonym_lexicon_entry:{index}:match", "match"
    )
    if not matches:
        raise ConfigurationError(f"empty_synonym_lexicon_entry_match:{index}")
    raw_families = _read_names(
        raw_entry["families"],
        f"synonym_lexicon_entry:{index}:families",
        "families",
        rejects_ids=False,
    )
    if not raw_families:
        # Mandatory and non-empty. An entry with no allowlist would let every
        # Pantry Category family through, which is the category-family guard
        # switched off while still reading as a guard in review.
        raise ConfigurationError(f"empty_synonym_lexicon_entry_families:{index}")
    families: list[str] = []
    for family in raw_families:
        if _CATEGORY_CODE.match(family) is None:
            raise ConfigurationError(
                f"synonym_lexicon_family_not_a_category_code:{index}:{family}"
            )
        families.append(family)
    return SynonymEntry(canonical=canonical, match=matches, families=tuple(families))


def _read_names(
    raw: object, label: str, what: str, *, rejects_ids: bool = True
) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ConfigurationError(f"invalid_{label}")
    return tuple(
        _read_name(entry, index, f"{what}[{index}]", rejects_ids=rejects_ids)
        for index, entry in enumerate(raw)
    )


def _read_name(value: object, index: int, what: str, *, rejects_ids: bool = True) -> str:
    """A value a lexicon entry may be written as. Fails closed on every defect.

    `rejects_ids` is False only for the `families` allowlist, whose values are
    Pantry Category *codes* and are digits by nature. Every other position in
    both files is a name, and an all-digits name there is always a Pantry Item
    id somebody pinned into a lexicon — which §9.8 forbids outright and which
    would break silently on the first producer re-import.
    """
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"invalid_{what}:{index}")
    if value != value.strip():
        raise ConfigurationError(f"padded_{what}:{index}")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ConfigurationError(f"control_character_{what}:{index}")
    if len(value) > MAX_NAME_CHARS:
        raise ConfigurationError(f"overlong_{what}:{index}")
    if rejects_ids and value.isdigit():
        raise ConfigurationError(f"pantry_item_id_in_{what}:{index}")
    return value


def _fold(value: str) -> str:
    """NFKC + casefold: the comparison form every lexicon lookup shares.

    The same folding `app/pantry/catalog.py:fold_name` applies to its index
    keys, so a name written `Ｄａｓｈｉ` in the vault and `dashi` in the lexicon
    are one name rather than two. Case is preserved by
    `normalize_ingredient()` on purpose; folding happens on top of it, here and
    in the catalog, never inside the shared normalizer.
    """
    return unicodedata.normalize("NFKC", value).casefold()
