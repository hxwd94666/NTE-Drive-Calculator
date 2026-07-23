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
        self.assertLessEqual(sum(path.stat().st_size for path in pngs), 8 * 1024 * 1024)
        for path in pngs:
            with Image.open(path) as image:
                self.assertLessEqual(max(image.size), 256, path.name)

    def test_all_static_core_items_have_an_official_item_id_mapping(self) -> None:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        with StaticGameDataDao(PROJECT_ROOT / "data" / "game_static.sqlite3") as dao:
            core_ids = {
                str(row["item_id"])
                for row in dao.list_equipment_items()
                if row["kind"] == "core"
            }
        self.assertEqual(core_ids, set(manifest["equipment_items"]))

    def test_all_static_modules_and_forks_have_official_id_mappings(self) -> None:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        with StaticGameDataDao(PROJECT_ROOT / "data" / "game_static.sqlite3") as dao:
            module_ids = {
                str(row["item_id"])
                for row in dao.list_equipment_items()
                if row["kind"] == "module"
            }
            fork_ids = {str(row["fork_id"]) for row in dao.list_forks()}
        self.assertEqual(module_ids, set(manifest["equipment_modules"]))
        self.assertEqual(fork_ids, set(manifest["fork_items"]))
        self.assertGreater(len(manifest["monster_icons"]), 0)

    def test_catalog_resolves_ids_and_rejects_missing_keys(self) -> None:
        catalog = GameUiAssetCatalog(ASSET_ROOT)
        self.assertTrue(catalog.character_icon(1003).is_file())
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
