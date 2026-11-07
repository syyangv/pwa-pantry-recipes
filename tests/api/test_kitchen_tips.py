"""End-to-end API projection and pantry matching for the second recipe root."""

from pathlib import Path

from app.config import Settings
from tests.api.conftest import client_for, make_settings, seed_catalog, write_pantry_note

TIPS_ROOT = "Hobbies/做饭/厨房技巧"
PRODUCT = "Trader Joe's 4 Chocolate Croissants"
BODY = "\n- 提前一晚上室温解冻\n- 350F烤20-25min（25会糊），凉10min\n"


def configured_tips(runtime_root: Path) -> Settings:
    settings = make_settings(runtime_root, KITCHEN_TIPS_ROOT=TIPS_ROOT)
    (settings.vault_path / settings.recipes_root).mkdir(parents=True, exist_ok=True)
    root = settings.vault_path / TIPS_ROOT
    root.mkdir(parents=True)
    (root / "预制croissant.md").write_text(
        f'---\ntags: [早餐, 预制菜]\n材料: ["{PRODUCT}"]\n厨具: 烤箱\n---' + BODY
    )
    seed_catalog(settings.pantry_items_db, ((57, PRODUCT, "1.2s", "[]", None),))
    write_pantry_note(
        settings.vault_path, f"# 3 早餐\n- [ ] {PRODUCT} ✍️ 2026-05-24 ➕ 2026-02-01\n".encode()
    )
    return settings


def test_tip_is_listed_and_detail_matches_the_croissant(runtime_root: Path) -> None:
    settings = configured_tips(runtime_root)
    original = (settings.vault_path / TIPS_ROOT / "预制croissant.md").read_bytes()
    with client_for(settings) as client:
        response = client.get("/api/recipes")
        assert response.status_code == 200
        assert [n["noteName"] for n in response.json()["recipes"]] == ["预制croissant"]
        response = client.get("/api/recipes/预制croissant")
        assert response.status_code == 200
        note = response.json()["recipe"]
        assert note["notePath"] == f"{TIPS_ROOT}/预制croissant.md"
        assert note["steps"] == BODY
        assert note["ingredients"][0]["pantryItemId"] == 57
        assert note["ingredients"][0]["inStock"] is True
    assert (settings.vault_path / TIPS_ROOT / "预制croissant.md").read_bytes() == original


def test_duplicate_names_return_honest_503(runtime_root: Path) -> None:
    settings = configured_tips(runtime_root)
    (settings.vault_path / settings.recipes_root / "预制croissant.md").write_text(
        "---\n材料: []\n---\n"
    )
    with client_for(settings) as client:
        response = client.get("/api/recipes")
        assert response.status_code == 503
        assert "duplicate_recipe_note_name" in response.text
