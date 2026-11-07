"""Kitchen tips share recipe routes without moving or rewriting vault notes."""

from dataclasses import replace

import pytest

from app.config import ConfigurationError, Settings
from app.recipes.reader import RecipeIndex, RecipeIndexError

TIPS_ROOT = "Hobbies/做饭/厨房技巧"
BODY = "\n- 提前一晚上室温解冻\n- 350F烤20-25min（25会糊），凉10min\n"


def seed(settings: Settings, name: str = "预制croissant", fields: str = "") -> Settings:
    (settings.vault_path / settings.recipes_root).mkdir(parents=True, exist_ok=True)
    root = settings.vault_path / TIPS_ROOT
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text("---\ntags: [早餐, 预制菜]\n" + fields + "---" + BODY)
    return replace(settings, kitchen_tips_root=TIPS_ROOT)


def test_tip_appears_with_original_path_body_and_product(settings: Settings) -> None:
    settings = seed(
        settings, fields="材料:\n  - Trader Joe's 4 Chocolate Croissants\n厨具: 烤箱, 空气炸锅\n"
    )
    (note,) = RecipeIndex(settings).notes()
    assert note.note_name == "预制croissant"
    assert note.note_path == f"{TIPS_ROOT}/预制croissant.md"
    assert note.ingredients[0].raw == "Trader Joe's 4 Chocolate Croissants"
    assert note.steps == BODY
    assert note.tools == ("烤箱", "空气炸锅")


def test_tips_without_ingredients_appear_but_index_page_does_not(settings: Settings) -> None:
    settings = seed(settings)
    seed(settings, name="厨房技巧")
    assert [n.note_name for n in RecipeIndex(settings).notes()] == ["预制croissant"]


def test_regular_recipe_admission_and_step_rules_stay_unchanged(settings: Settings) -> None:
    settings = seed(settings)
    root = settings.vault_path / settings.recipes_root
    (root / "not-a-recipe.md").write_text("---\ntags: [早餐]\n---\ntext")
    (root / "普通菜.md").write_text("---\n材料: [鸡蛋]\n---\nno step heading")
    notes = {n.note_name: n for n in RecipeIndex(settings).notes()}
    assert set(notes) == {"预制croissant", "普通菜"}
    assert notes["普通菜"].steps == ""


def test_tip_with_steps_heading_uses_existing_step_rule(settings: Settings) -> None:
    settings = seed(settings)
    p = settings.vault_path / TIPS_ROOT / "预制croissant.md"
    p.write_text("---\n烹饪工具: 烤箱\n厨具: 空气炸锅\n---\nintro\n# 4 步骤\noriginal steps\n")
    (note,) = RecipeIndex(settings).notes()
    assert note.steps == "\noriginal steps\n"
    assert note.tools == ("烤箱",)


def test_unconfigured_tips_root_is_not_scanned(settings: Settings) -> None:
    seed(settings)
    assert RecipeIndex(settings).notes() == ()


def test_configured_missing_tips_root_fails_closed(settings: Settings) -> None:
    (settings.vault_path / settings.recipes_root).mkdir(parents=True, exist_ok=True)
    with pytest.raises(ConfigurationError, match="missing_recipes_root"):
        RecipeIndex(replace(settings, kitchen_tips_root=TIPS_ROOT)).notes()


def test_duplicate_note_names_across_roots_fail_closed(settings: Settings) -> None:
    settings = seed(settings)
    (settings.vault_path / settings.recipes_root / "预制croissant.md").write_text(
        "---\n材料: [鸡蛋]\n---\n"
    )
    with pytest.raises(RecipeIndexError, match="duplicate_recipe_note_name"):
        RecipeIndex(settings).notes()


def test_global_directory_limit_covers_both_roots(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = seed(settings)
    (settings.vault_path / settings.recipes_root / "普通菜.md").write_text("---\n材料: []\n---\n")
    monkeypatch.setattr("app.recipes.reader.MAX_RECIPE_FILES", 1)
    with pytest.raises(RecipeIndexError, match="recipe_directory_limit"):
        RecipeIndex(settings).notes()


def test_symlinked_tip_file_is_not_read(settings: Settings) -> None:
    settings = seed(settings)
    (settings.vault_path / TIPS_ROOT / "symlink.md").symlink_to(
        settings.vault_path / TIPS_ROOT / "预制croissant.md"
    )
    assert len(RecipeIndex(settings).notes()) == 1
