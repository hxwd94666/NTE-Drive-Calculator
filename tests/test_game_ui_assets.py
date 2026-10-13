# 验证正式 ID 公共素材完整性及构建预算，不执行游戏输入。
# 测试轻量游戏界面资源的 ID 映射、尺寸和总容量预算。
from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from PIL import Image

from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from tools.game_assets.build_ui_assets import build_assets
from tools.game_assets import build_ui_assets as ui_assets


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "data" / "role_catalog" / "game_ui"


class GameUiAssetTests(unittest.TestCase):
    def test_all_static_characters_have_an_official_id_mapping(self) -> None:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        with StaticGameDataDao(PROJECT_ROOT / "data" / "role_catalog" / "game_static.sqlite3") as dao:
            character_ids = {str(row["character_id"]) for row in dao.list_characters()}
        self.assertEqual(character_ids, set(manifest["characters"]))

    def test_generated_pngs_stay_inside_dimension_and_size_budgets(self) -> None:
        pngs = sorted(ASSET_ROOT.rglob("*.png"))
        self.assertGreater(len(pngs), 0)
        self.assertLessEqual(sum(path.stat().st_size for path in pngs), 32 * 1024 * 1024)
        for path in pngs:
            with Image.open(path) as image:
                expected_max = 512 if "character_arts" in path.parts else 256
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
                1036, 1039, 1042, 1046, 1051, 1052, 1054, 1055, 1057, 1070, 1071, 1072,
                1073, 1075, 1076,
            )
        }
        self.assertEqual(expected, set(manifest["character_arts"]))
        catalog = GameUiAssetCatalog(ASSET_ROOT)
        self.assertTrue(all(catalog.character_art(int(key)).is_file() for key in expected))
        self.assertIsNone(catalog.character_art(1056))

    def test_all_static_core_items_have_an_official_item_id_mapping(self) -> None:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        with StaticGameDataDao(PROJECT_ROOT / "data" / "role_catalog" / "game_static.sqlite3") as dao:
            core_ids = {str(row["item_id"]) for row in dao.list_equipment_items() if row["kind"] == "core"}
        self.assertEqual(core_ids, set(manifest["equipment_items"]))

    def test_all_static_modules_and_forks_have_official_id_mappings(self) -> None:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        with StaticGameDataDao(PROJECT_ROOT / "data" / "role_catalog" / "game_static.sqlite3") as dao:
            module_ids = {str(row["item_id"]) for row in dao.list_equipment_items() if row["kind"] == "module"}
            fork_ids = {str(row["fork_id"]) for row in dao.list_forks()}
        self.assertEqual(module_ids, set(manifest["equipment_modules"]))
        self.assertEqual(fork_ids, set(manifest["fork_items"]))
        self.assertGreater(len(manifest["monster_icons"]), 0)

    def test_new_fork_exports_resolve_to_official_icons(self) -> None:
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest.get("unresolved_assets"))
        catalog = GameUiAssetCatalog(ASSET_ROOT)
        self.assertTrue(catalog.fork_icon("fork_DemonBlade").is_file())
        self.assertTrue(catalog.fork_icon("fork_GoldRecord").is_file())

    def test_fork_progression_materials_resolve_to_packaged_icons(self) -> None:
        manifest = json.loads(
            (ASSET_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        with StaticGameDataDao(
            PROJECT_ROOT / "data" / "role_catalog" / "game_static.sqlite3"
        ) as dao:
            material_ids = {
                str(row["item_id"])
                for row in dao.list_progression_items(tuple(
                    manifest.get("progression_items", {})
                ))
                if str(row["item_id"]).startswith((
                    "WeaponUpMaterial_",
                    "WeaponBreakMaterial_",
                    "OrdinaryMonMaterial_",
                ))
            }
        expected_exp = {
            "WeaponUpMaterial_lv1",
            "WeaponUpMaterial_lv2",
            "WeaponUpMaterial_lv3",
        }
        self.assertTrue(expected_exp <= material_ids)
        catalog = GameUiAssetCatalog(ASSET_ROOT)
        self.assertTrue(all(
            catalog.progression_item_icon(item_id).is_file()
            for item_id in material_ids
        ))

    def test_catalog_resolves_ids_and_rejects_missing_keys(self) -> None:
        catalog = GameUiAssetCatalog(ASSET_ROOT)
        self.assertTrue(catalog.character_icon(1003).is_file())
        self.assertTrue(catalog.character_art(1003).is_file())
        self.assertTrue(catalog.character_icon(1036).is_file())
        self.assertTrue(catalog.character_icon(1072).is_file())
        for identity in (1004, 1091, 1042):
            self.assertTrue(catalog.character_icon(identity).is_file())
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


    def test_shared_output_bindings_validate_before_rendering(self) -> None:
        """只碰合成来源字节；不渲染图片、不读静态库，冲突和越界在任何输出前拒绝。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = root / "Content"
            directory = content / "UI"
            directory.mkdir(parents=True)
            for name, data in (("A", b"fixture-a"), ("B", b"fixture-b"), ("C", b"fixture-a")):
                (directory / f"{name}.png").write_bytes(data)
            output = root / "candidate"

            def groups(second, dimension=256):
                return [
                    ("first", "key", [{"key": "one", "source_asset_path": "/Game/UI/A.A",
                                       "output": "characters/shared.png"}], 256),
                    ("second", "key", [{"key": "two", "source_asset_path": second,
                                        "output": "characters/shared.png"}], dimension),
                ]

            for label, second in (("same_source_alias", "/Game/UI/A"), ("same_bytes", "/Game/UI/C.C")):
                with self.subTest(label=label):
                    ui_assets._validate_output_bindings(content, output, groups(second))
            for label, second, size, message in (
                ("different_bytes", "/Game/UI/B.B", 256, "不同来源内容"),
                ("different_dimensions", "/Game/UI/A.A", 128, "渲染尺寸冲突"),
            ):
                with self.subTest(label=label):
                    with self.assertRaisesRegex(ValueError, message):
                        ui_assets._validate_output_bindings(content, output, groups(second, size))

            for second in ("/Game/../Outside", "/Game//absolute", "/Game/C:/absolute", "/Game/..\\Outside"):
                with self.subTest(invalid_source=second):
                    with patch.object(ui_assets, "_sha256", side_effect=AssertionError("根外来源不得读取")):
                        with self.assertRaisesRegex(ValueError, "越过 Content"):
                            ui_assets._validate_output_bindings(content, output, groups(second))

            original_resolve = Path.resolve
            def redirected(path, *args, **kwargs):
                if path == directory / "Link.png":
                    return root / "Outside.png"
                return original_resolve(path, *args, **kwargs)

            with patch.object(Path, "resolve", autospec=True, side_effect=redirected):
                with patch.object(ui_assets, "_sha256", side_effect=AssertionError("重解析越界不得读取")):
                    with self.assertRaisesRegex(ValueError, "越过 Content"):
                        ui_assets._validate_output_bindings(content, output, groups("/Game/UI/Link.Link"))

            with self.assertRaisesRegex(ValueError, "输出路径越过目标"):
                ui_assets._validate_output_bindings(content, output, [
                    ("outside", "key", [{"key": "one", "source_asset_path": "/Game/UI/A.A",
                                        "output": "../outside.png"}], 256),
                ])
            self.assertFalse(output.exists())
            self.assertFalse((output / "manifest.json").exists())


    def test_monster_producer_reuses_original_alias_render_parameters(self) -> None:
        """从正式纯组装入口生成group，再交绑定门禁；合法128别名和独立160来源不能互伤。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = root / "Content"
            (content / "UI").mkdir(parents=True)
            for name in ("M", "O", "E"):
                (content / "UI" / f"{name}.png").write_bytes(("fixture-" + name).encode())
            output = root / "candidate"
            monster = [{"key": "monster_static_big_world:mon_01_BP",
                        "source_asset_path": "/Game/UI/M.M", "output": "monsters/world/M.png"}]

            def family_rows(groups):
                return {entry["key"]: (entry["output"], dimension)
                        for group, _identity, entries, dimension in groups
                        if group == "monster_family_icons" for entry in entries}

            with self.subTest(case="normal_derived_alias"):
                groups = ui_assets._monster_asset_groups({}, monster, [], [])
                rows = family_rows(groups)
                self.assertEqual(("monsters/world/M.png", 128), rows["mon_01"])
                self.assertEqual(rows["mon_01"], rows["mon_1"])
                ui_assets._validate_output_bindings(content, output, groups)

            override = {"key": "mon_03", "source_asset_path": "/Game/UI/O.O",
                        "output": "monsters/family/O.png"}
            encounter = [{"key": "/Game/UI/E.E", "source_asset_path": "/Game/UI/E.E",
                          "output": "monsters/resources/E.png"}]
            database_family = [{"key": "boss_09", "source_asset_path": "/Game/UI/E.E",
                                "output": "monsters/resources/E.png", "priority": "1"}]
            with self.subTest(case="independent_override_and_database_alias"):
                groups = ui_assets._monster_asset_groups(
                    {"monster_family_icons": [override], "encounter_icons_from_database": True},
                    monster, encounter, database_family,
                )
                rows = family_rows(groups)
                self.assertEqual(("monsters/world/M.png", 128), rows["mon_01"])
                self.assertEqual(("monsters/family/O.png", 160), rows["mon_03"])
                self.assertEqual(rows["mon_03"], rows["mon_3"])
                self.assertEqual(("monsters/resources/E.png", 160), rows["boss_09"])
                self.assertEqual(rows["boss_09"], rows["boss_9"])
                ui_assets._validate_output_bindings(content, output, groups)
                self.assertEqual(3, len({entry["output"] for _group, _identity, entries, _size in groups
                                        for entry in entries}))

            with self.subTest(case="override_replaces_family_not_original_monster"):
                groups = ui_assets._monster_asset_groups(
                    {"monster_family_icons": [{**override, "key": "mon_01"}]}, monster, [], [],
                )
                self.assertEqual(("monsters/family/O.png", 160), family_rows(groups)["mon_01"])
                self.assertEqual(("monster_icons", "key", monster, 128), groups[0])
                ui_assets._validate_output_bindings(content, output, groups)

            with self.subTest(case="arbitrary_parameter_conflict_still_rejected"):
                conflict = groups + [("unrelated", "key", [
                    {"key": "x", "source_asset_path": "/Game/UI/M.M", "output": "monsters/world/M.png"},
                ], 160)]
                with self.assertRaisesRegex(ValueError, "渲染尺寸冲突"):
                    ui_assets._validate_output_bindings(content, output, conflict)
            self.assertFalse(output.exists())
            self.assertFalse((output / "manifest.json").exists())

if __name__ == "__main__":
    unittest.main()
