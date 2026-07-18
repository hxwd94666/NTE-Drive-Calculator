# 测试角色数据源清单对特殊技能配置的筛选规则。
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tools.game_data.catalog_characters import build_catalog, write_reports


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def datatable(name: str, rows: dict) -> list[dict]:
    return [{"Type": "DataTable", "Name": name, "Rows": rows}]


def character(name: str, prop_modify_id: str, *, show_date: tuple[int, int, int] | None = None):
    element = {
        "PropModifyID": prop_modify_id,
        "CharacterActorClass": {"AssetPathName": f"/Game/{name}"},
    }
    if show_date:
        year, month, day = show_date
        element.update(
            {
                "bCheckShowTime": True,
                "ShowTime": {
                    "MainlandTime": {"Year": year, "Month": month, "Day": day}
                },
            }
        )
    return {
        "ItemName": {"SourceString": name, "Key": f"name_{name}"},
        "ItemIcon": {"AssetPathName": f"/Game/{name}.png"},
        "ElementData": element,
    }


def ability(prefix: str, marker: str = "normal") -> dict:
    return {
        "CharacterAbilityList": [
            {"Key": f"{prefix}_Melee", "marker": marker},
            {"Key": f"{prefix}_Skill"},
            {"Key": f"{prefix}_UltraSkill"},
            {"Key": f"{prefix}_QTE"},
        ],
        "PassiveAbilityList": [
            {"Key": f"{prefix}_Passive_1"},
            {"Key": f"{prefix}_Passive_2"},
        ],
    }


class CharacterDataCatalogTests(unittest.TestCase):
    def test_special_ability_profile_is_not_counted_as_a_character(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = root / "Content"
            write_json(
                content / "DataTable/Character/DT_Character.json",
                datatable(
                    "DT_Character",
                    {
                        "1076": character("真红", "shinku_base"),
                        "1075": character("伊洛伊", "oneiroi_base", show_date=(2026, 7, 23)),
                    },
                ),
            )
            write_json(
                content / "DataTable/Character/DT_CharacterAbilityConfig.json",
                datatable(
                    "DT_CharacterAbilityConfig",
                    {
                        "1076": ability("GA_Shinku"),
                        "1075": ability("GA_Oneiroi"),
                        "ft_character_1076": ability("GA_Shinku", marker="minigame"),
                    },
                ),
            )
            write_json(
                content / "DataTable/PackData/DT_PlayerPackData.json",
                datatable("DT_PlayerPackData", {"shinku_base": {}, "oneiroi_base": {}}),
            )
            model_path = root / "my_roles_model.json"
            write_json(model_path, {"真红": {}})
            overrides_path = root / "overrides.json"
            write_json(
                overrides_path,
                {
                    "ability_profile_overrides": {
                        "ft_character_1076": {
                            "character_id": "1076",
                            "logical_character_key": "character:1076",
                            "display_name": "真红",
                            "profile_kind": "minigame_variant",
                            "scope": "999_nights",
                            "included_by_default": False,
                            "confidence": "confirmed",
                        }
                    }
                },
            )

            catalog = build_catalog(
                root,
                model_path,
                overrides_path,
                as_of=date(2026, 7, 18),
            )

            self.assertEqual(2, catalog["counts"]["character_rows"])
            self.assertEqual(2, catalog["counts"]["normal_ability_profiles"])
            self.assertEqual(1, catalog["counts"]["minigame_ability_profiles"])
            by_id = {item["character_id"]: item for item in catalog["characters"]}
            self.assertEqual("available_character", by_id["1076"]["classification"])
            self.assertEqual("scheduled_character", by_id["1075"]["classification"])
            profile = catalog["minigame_ability_profiles"][0]
            self.assertEqual(4, profile["profile"]["proactive_count"])
            self.assertEqual(2, profile["profile"]["passive_count"])
            self.assertFalse(profile["included_by_default"])
            self.assertFalse(profile["identical_to_normal_profile"])

            json_path, markdown_path = write_reports(catalog, root / "reports")
            self.assertTrue(json_path.is_file())
            self.assertIn("999夜技能配置", markdown_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
