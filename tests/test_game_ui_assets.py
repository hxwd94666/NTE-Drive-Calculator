# 测试轻量游戏界面资源的 ID 映射、尺寸和总容量预算。
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from tools.game_assets.build_ui_assets import build_assets


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "assets" / "game_ui"


class GameUiAssetTests(unittest.TestCase):
    def test_all_static_characters_have_an_official_id_mapping(self) -> None:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        with StaticGameDataDao(PROJECT_ROOT / "data" / "game_static.sqlite3") as dao:
            character_ids = {str(row["character_id"]) for row in dao.list_characters()}
        self.assertEqual(character_ids, set(manifest["characters"]))

    def test_generated_pngs_stay_inside_dimension_and_size_budgets(self) -> None:
        pngs = sorted(ASSET_ROOT.rglob("*.png"))
        self.assertGreater(len(pngs), 0)
        self.assertLessEqual(sum(path.stat().st_size for path in pngs), 32 * 1024 * 1024)
        for path in pngs:
            with Image.open(path) as image:
                expected_max = 512 if "characters/art" in path.as_posix() else 256
                self.assertLessEqual(max(image.size), expected_max, path.name)

    def test_catalog_characters_have_formal_default_appearance_art(self) -> None:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            "/Game/DataTable/Character/Appearance/DT_AppearanceData",
            manifest["source_appearance_table"],
        )
        expected = {
            str(character_id)
            for character_id in (
                1003, 1004, 1008, 1010, 1019, 1020, 1021, 1023, 1025, 1033,
                1036, 1039, 1046, 1051, 1052, 1054, 1055, 1070, 1071, 1072,
                1073, 1075, 1076,
            )
        }
        self.assertEqual(expected, set(manifest["character_arts"]))
        catalog = GameUiAssetCatalog(ASSET_ROOT)
        self.assertTrue(all(catalog.character_art(int(key)).is_file() for key in expected))
        self.assertIsNone(catalog.character_art(1056))

    def test_all_static_core_items_have_an_official_item_id_mapping(self) -> None:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        with StaticGameDataDao(PROJECT_ROOT / "data" / "game_static.sqlite3") as dao:
            core_ids = {str(row["item_id"]) for row in dao.list_equipment_items() if row["kind"] == "core"}
        self.assertEqual(core_ids, set(manifest["equipment_items"]))

    def test_all_static_modules_and_forks_have_official_id_mappings(self) -> None:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        with StaticGameDataDao(PROJECT_ROOT / "data" / "game_static.sqlite3") as dao:
            module_ids = {str(row["item_id"]) for row in dao.list_equipment_items() if row["kind"] == "module"}
            fork_ids = {str(row["fork_id"]) for row in dao.list_forks()}
        self.assertEqual(module_ids, set(manifest["equipment_modules"]))
        self.assertEqual(fork_ids, set(manifest["fork_items"]))
        self.assertGreater(len(manifest["monster_icons"]), 0)

    def test_new_fork_exports_resolve_to_official_icons(self) -> None:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("unresolved_assets", manifest)
        catalog = GameUiAssetCatalog(ASSET_ROOT)
        self.assertTrue(catalog.fork_icon("fork_DemonBlade").is_file())
        self.assertTrue(catalog.fork_icon("fork_GoldRecord").is_file())

    def test_catalog_resolves_ids_and_rejects_missing_keys(self) -> None:
        catalog = GameUiAssetCatalog(ASSET_ROOT)
        self.assertTrue(catalog.character_icon(1003).is_file())
        self.assertTrue(catalog.character_art(1003).is_file())
        self.assertEqual("player_canhong_256.png", catalog.character_icon(1036).name)
        self.assertEqual("player_lingke_256.png", catalog.character_icon(1072).name)
        self.assertEqual(catalog.character_icon(1004), catalog.character_icon(1091))
        self.assertTrue(catalog.attribute_icon("crit_rate").is_file())
        self.assertTrue(catalog.equipment_icon("Lakshana_orange").is_file())
        self.assertTrue(catalog.module_icon("cell3_style1_1_Orange").is_file())
        self.assertEqual(
            catalog.equipment_icon("Lakshana_orange"),
            catalog.inventory_item_icon("core", "Lakshana_orange"),
        )
        self.assertEqual(
            catalog.module_icon("cell3_style1_1_Orange"),
            catalog.inventory_item_icon("module", "cell3_style1_1_Orange"),
        )
        self.assertIsNone(catalog.inventory_item_icon("unknown", "Lakshana_orange"))
        self.assertTrue(catalog.fork_icon("fork_yuren").is_file())
        self.assertIsNone(catalog.monster_icon("monster_static_big_world", "unknown"))
        feast_icon = catalog.encounter_icon(
            "/Game/UI/UI/DiyBoss/boss_04_icon.boss_04_icon"
        )
        self.assertTrue(feast_icon.is_file())
        self.assertTrue(catalog.monster_family_icon("mon_14_BP_Abyss").is_file())
        self.assertTrue(catalog.monster_family_icon("boss_09_ChallengeLv1_BP").is_file())
        self.assertTrue(catalog.monster_family_icon("Boss_06_BP_Abyss").is_file())
        self.assertTrue(catalog.monster_family_icon("Boss_016_BP_Abyss").is_file())
        self.assertTrue(catalog.monster_family_icon("Boss_017_BP_Abyss").is_file())
        self.assertTrue(catalog.monster_family_icon("boss_18_BP_Abyss").is_file())
        self.assertTrue(catalog.monster_family_icon("mon_08_BP_Abyss").is_file())
        self.assertTrue(catalog.monster_family_icon("mon_022_BP_Abyss").is_file())
        self.assertTrue(catalog.monster_family_icon("mon_39_2_BP_Abyss").is_file())
        self.assertTrue(catalog.monster_family_icon("mon_051_BP_Abyss").is_file())
        self.assertIsNone(catalog.monster_family_icon("mon_140_BP_Abyss"))
        self.assertIsNone(catalog.character_icon(999999))

    def test_builder_resizes_and_deduplicates_shared_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = root / "Content"
            source = content / "UI" / "Avatar" / "Shared.png"
            source.parent.mkdir(parents=True)
            Image.new("RGBA", (512, 384), (255, 0, 0, 128)).save(source)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "manifest_version": 1,
                        "source_data_table": "/Game/Test",
                        "characters": [
                            {
                                "character_id": 1,
                                "source_asset_path": "/Game/UI/Avatar/Shared.Shared",
                                "output": "characters/shared.png",
                            },
                            {
                                "character_id": 2,
                                "source_asset_path": "/Game/UI/Avatar/Shared.Shared",
                                "output": "characters/shared.png",
                            },
                        ],
                        "attributes": [],
                        "equipment_items": [],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "output"
            result = build_assets(
                content,
                manifest_path,
                output,
                root / "external-static.sqlite3",
            )

            self.assertEqual(1, result["total_files"])
            self.assertEqual("characters/shared.png", result["characters"]["1"])
            self.assertEqual("characters/shared.png", result["characters"]["2"])
            with Image.open(output / "characters" / "shared.png") as image:
                self.assertLessEqual(max(image.size), 256)


if __name__ == "__main__":
    unittest.main()
