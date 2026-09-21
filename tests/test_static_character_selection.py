# 验证完整库和独立目录共享角色完整性筛选并保留来源事实。
import json
from pathlib import Path
import tempfile
import unittest

from tools.game_data.build_role_catalog import RoleCatalogBuilder
from tools.game_data.build_static_database import StaticDatabaseBuilder

NTE_TEST_TIER = "core"


class StaticCharacterSelectionTests(unittest.TestCase):
    def test_full_and_role_catalog_preserve_sources_and_exclude_incomplete_roles(self):
        with tempfile.TemporaryDirectory() as temporary:
            overrides = Path(temporary) / "overrides.json"
            overrides.write_text(json.dumps({"character_overrides": {
                "2042": {"classification": "combat_transformation"}
            }}), encoding="utf-8")
            valid = {"ItemName": {"LocalizedString": "黑羽"}, "ElementData": {
                "PropModifyID": "blackbird_base", "UpgradeModifyPackId": "BlackBird_lv"
            }}
            sources = {"1042": valid, "1999": {**valid, "ItemName": {}},
                       "3000": {**valid, "ElementData": {
                           "PropModifyID": "blackbird_base", "UpgradeModifyPackId": "other_lv"
                       }}, "3001": valid,
                       "2042": {"ItemName": {"LocalizedString": "黑羽形态"}}}
            before = json.dumps(sources, sort_keys=True)
            for builder_type in (StaticDatabaseBuilder, RoleCatalogBuilder):
                with self.subTest(builder=builder_type.__name__):
                    builder = object.__new__(builder_type)
                    builder.overrides_path = overrides
                    builder.rows = {"character": sources, "character_abilities": {
                        "1042": {"CharacterAbilityList": ["skill"]}},
                        "equipment_plans": {"1042": {}, "1999": {}},
                        "cultivation_guides": {"1042": {}, "1999": {}}}
                    builder.source_row_ids = {("character", key): n for n, key in enumerate(sources)}
                    builder._select_role_rows()
                    self.assertEqual({"1042", "2042"}, set(builder.rows["character"]))
                    self.assertEqual({"1042"}, set(builder.rows["equipment_plans"]))
                    self.assertEqual({"1042"}, set(builder.rows["cultivation_guides"]))
                    self.assertEqual({"missing_official_name", "base_and_growth_identity_conflict",
                                      "missing_skill_catalog"},
                                     {row["reason"] for row in builder.excluded_roles})
                    self.assertIn(("character", "1999"), builder.source_row_ids)
                    entries = builder._character_catalog_entries({"characters": [
                        {"character_id": key} for key in sources]})
                    self.assertEqual({"1042", "2042"}, {entry["character_id"] for entry in entries})
                    self.assertEqual(before, json.dumps(sources, sort_keys=True))
