"""`DailyNotePathPolicy`: the browser sends a date, never a path.

The invariant under test is the Server-Owned Root one: from a date alone, the
server derives exactly one file, and no request field can redirect the write
anywhere else. Every refusal here is a refusal *before* any filesystem access —
a path this module could not re-derive from the date alone is a path the daily
note reader would later be unable to name, so the policy is where it has to die.

The `Settings`-built policy is covered too, because `daily_notes_root` and
`daily_notes_year_policy` are the only two server-owned inputs and a policy
assembled from `os.environ` instead would be an unvalidated second copy.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.config import Settings
from app.vault.daily_paths import DailyNotePathError, DailyNotePathPolicy

NOTES_ROOT = "日记"


def test_a_date_derives_the_year_and_the_file() -> None:
    """The whole point: `2026-09-27` names one file, and the year is derived."""
    assert (
        DailyNotePathPolicy(NOTES_ROOT).resolve("2026-09-27") == "日记/2026/2026-09-27.md"
    )


def test_the_path_is_vault_relative_and_never_absolute() -> None:
    """`/health` leaks no path, so the derived path must not be an absolute one."""
    resolved = DailyNotePathPolicy(NOTES_ROOT).resolve("2026-01-01")
    assert not resolved.startswith("/")
    assert resolved == "日记/2026/2026-01-01.md"


@pytest.mark.parametrize(
    "requested",
    [
        "2026-9-27",  # not ISO-round-tripping, and not even parseable
        "20260927",  # basic format: `fromisoformat` accepts it, and it would
        # otherwise build `2026-09-27.md` from a string the reader cannot re-parse
        "2026-W40-7",  # ISO week date: same, and it is 2026-10-04
        "2026-09-31",  # not a real day
        "../x",  # a traversal, not a date
        "/2026-09-27",  # an absolute path, not a date
        "2026-09-27\n2026-09-28",  # two dates, one field
        "not-a-date",
        "",
    ],
)
def test_a_date_that_is_not_exactly_one_iso_date_is_refused(requested: str) -> None:
    with pytest.raises(DailyNotePathError) as raised:
        DailyNotePathPolicy(NOTES_ROOT).resolve(requested)
    assert str(raised.value) == "invalid_daily_note_date"


@pytest.mark.parametrize(
    "root",
    ["/日记", "../日记", "日记/.hidden", "日记//notes", "", ".", "..", "日记/../x"],
)
def test_a_root_that_is_not_a_canonical_relative_folder_is_refused(root: str) -> None:
    with pytest.raises(DailyNotePathError):
        DailyNotePathPolicy(root)


def test_a_year_outside_the_configured_scope_is_refused_by_name() -> None:
    """`date_outside_configured_scope` is a distinct code: the date was valid,
    and "your policy excludes it" is a different answer from "that is not a date"."""
    policy = DailyNotePathPolicy(NOTES_ROOT, (2020, 2026))
    assert policy.resolve("2020-01-01") == "日记/2020/2020-01-01.md"
    assert policy.resolve("2026-12-31") == "日记/2026/2026-12-31.md"
    for outside in ("2019-12-31", "2027-01-01"):
        with pytest.raises(DailyNotePathError) as raised:
            policy.resolve(outside)
        assert str(raised.value) == "date_outside_configured_scope"


def test_no_configured_policy_means_the_layout_is_the_only_bound() -> None:
    """`None` is a real configured state, not "unvalidated"."""
    policy = DailyNotePathPolicy(NOTES_ROOT)
    assert policy.resolve("1999-01-01") == "日记/1999/1999-01-01.md"


def test_an_inverted_year_range_is_a_refused_policy() -> None:
    with pytest.raises(DailyNotePathError) as raised:
        DailyNotePathPolicy(NOTES_ROOT, (2026, 2020))
    assert str(raised.value) == "invalid_daily_notes_year_policy"


def test_the_policy_is_built_from_the_fail_closed_settings_surface(settings: Settings) -> None:
    assert DailyNotePathPolicy.from_settings(settings) == DailyNotePathPolicy(NOTES_ROOT, None)


def test_a_configured_year_policy_flows_from_settings_into_the_refusal(
    settings: Settings,
) -> None:
    scoped = Settings.from_mapping(
        {
            "OBSIDIAN_VAULT_PATH": str(settings.vault_path),
            "APP_DATA_DIR": str(settings.app_data_dir),
            "PANTRY_ITEMS_DB": str(settings.app_data_dir.parent / "pantry_items.db"),
            "PUBLIC_ORIGIN": "https://recipes.test.invalid",
            "TAILSCALE_OWNER_LOGIN": "owner@test.invalid",
            "DAILY_NOTES_YEAR_POLICY": "2026-2026",
        }
    )
    policy = DailyNotePathPolicy.from_settings(scoped)
    assert policy.resolve("2026-09-27") == "日记/2026/2026-09-27.md"
    with pytest.raises(DailyNotePathError) as raised:
        policy.resolve("2025-09-27")
    assert str(raised.value) == "date_outside_configured_scope"


def test_the_policy_is_frozen(settings: Settings) -> None:
    """A policy shared across requests cannot be re-pointed at another root."""
    policy = DailyNotePathPolicy.from_settings(settings)
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.root = "elsewhere"  # type: ignore[misc]
