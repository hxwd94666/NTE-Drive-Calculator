# nanoka 角色/武器白值同步逻辑的单元测试。
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.features.settings.nanoka_base_stats import (
    board_matrix_from_equip_slots,
    extract_level_base_stats,
    local_role_name_for_remote,
    merge_nanoka_base_stats_into_model,
    resolve_character_id,
    sync_nanoka_base_stats,
)
from src.features.settings.nanoka_client import detect_live_version, parse_version_tuple, resolve_version
from src.features.settings.nanoka_weapon_stats import (
    extract_weapon_level_stats,
    merge_nanoka_weapon_stats,
    resolve_local_weapon_key,
    sync_nanoka_weapon_stats,
)


def _fake_character(*, character_id: str = "1004", hp=None, atk=None, defense=None) -> dict:
    hp = hp or [1000 + i for i in range(80)]
    atk = atk or [50 + i for i in range(80)]
    defense = defense or [60 + i for i in range(80)]
    return {
        "id": character_id,
        "name": "Lacrimosa",
        "desc": "desc",
        "element": "Chaos",
        "equip_slots": {
            "slots": [
                [-1] * 7,
                [-1, 0, 0, 0, 0, 0, -1],
                [-1, 0, 0, 0, 0, 0, -1],
                [-1, 0, 0, -1, 0, 0, -1],
                [-1, 0, 0, -1, -1, 0, -1],
                [-1, 0, 0, 0, 0, -1, -1],
                [-1] * 7,
            ]
        },
        "stats": [
            {"id_stats": "HPMaxBase", "values": hp},
            {"id_stats": "AtkBase", "values": atk},
            {"id_stats": "DefBase", "values": defense},
            {"id_stats": "CritBase", "values": [5] * 80},
            {"id_stats": "CritDamageBase", "values": [50] * 80},
        ],
    }


def _fake_weapon(*, weapon_id: str = "fork_LunarPhase") -> dict:
    return {
        "id": weapon_id,
        "name": "穿过胭红蜃景",
        "type_name": "聚合",
        "description": "desc",
        "stats": [
            {"id_stats": "AtkBase", "values": [37 + i for i in range(80)]},
            {"id_stats": "CritBase", "values": [9.6] * 80},
        ],
    }


class NanokaClientTests(unittest.TestCase):
    def test_parse_version_tuple(self):
        self.assertEqual(parse_version_tuple("1.2"), (1, 2))
        self.assertEqual(parse_version_tuple("1.2.14"), (1, 2, 14))

    def test_detect_live_version_from_embedded_static_urls(self):
        html = """
        <script data-url="https://static.nanoka.cc/nte/1.3/character.json"></script>
        <script data-url="https://static.nanoka.cc/nte/1.3/weapon.json"></script>
        <script data-url="https://static.nanoka.cc/nte/1.2.14/character.json"></script>
        """
        with mock.patch(
            "src.features.settings.nanoka_client.request_text",
            return_value=html,
        ):
            self.assertEqual(detect_live_version(), "1.3")

    def test_resolve_version_latest(self):
        with mock.patch(
            "src.features.settings.nanoka_client.detect_live_version",
            return_value="2.0",
        ):
            self.assertEqual(resolve_version("latest"), "2.0")
            self.assertEqual(resolve_version(None), "2.0")


class NanokaBaseStatsTests(unittest.TestCase):
    def test_extract_level_base_stats_uses_one_based_levels(self):
        character = _fake_character()
        levels = extract_level_base_stats(character, levels=(1, 80))
        self.assertEqual(levels["1"]["生命白值"], 1000.0)
        self.assertEqual(levels["1"]["攻击力白值"], 50.0)
        self.assertEqual(levels["80"]["生命白值"], 1079.0)
        self.assertEqual(levels["80"]["攻击力白值"], 129.0)

    def test_resolve_character_id_prefers_workshop_item_id(self):
        roles_meta = {"安魂曲": {"workshop_item_id": "1004"}}
        character_index = {
            "1004": {"zh": "安魂曲"},
            "9999": {"zh": "安魂曲"},
        }
        self.assertEqual(
            resolve_character_id(
                "安魂曲",
                roles_meta=roles_meta,
                character_index=character_index,
            ),
            "1004",
        )

    def test_local_role_name_aliases(self):
        self.assertEqual(local_role_name_for_remote("「零」"), "主角")
        self.assertEqual(local_role_name_for_remote("法帝娅"), "法蒂娅")

    def test_board_matrix_from_equip_slots_crops_center(self):
        matrix = board_matrix_from_equip_slots(_fake_character()["equip_slots"])
        self.assertEqual(matrix[0], [0, 0, 0, 0, 0])
        self.assertEqual(matrix[2], [0, 0, -1, 0, 0])

    def test_merge_updates_level_and_current_sub_stats(self):
        model = {
            "安魂曲": {
                "level": 80,
                "level_sub_stats": {
                    "80": {
                        "生命白值": 1.0,
                        "攻击力白值": 2.0,
                        "防御力白值": 3.0,
                        "暴击率%": 5.0,
                        "暴击伤害%": 50.0,
                    }
                },
                "sub_stats": {
                    "生命白值": 1.0,
                    "攻击力白值": 2.0,
                    "防御力白值": 3.0,
                    "暴击率%": 5.0,
                    "暴击伤害%": 50.0,
                    "自定义": 9.0,
                },
            }
        }
        remote = {
            "安魂曲": {
                "80": {
                    "生命白值": 15998.0,
                    "攻击力白值": 636.0,
                    "防御力白值": 909.0,
                    "暴击率%": 5.0,
                    "暴击伤害%": 50.0,
                }
            }
        }
        merged, summary = merge_nanoka_base_stats_into_model(model, remote)
        self.assertEqual(summary["updated_count"], 1)
        self.assertEqual(merged["安魂曲"]["level_sub_stats"]["80"]["攻击力白值"], 636.0)
        self.assertEqual(merged["安魂曲"]["sub_stats"]["攻击力白值"], 636.0)
        self.assertEqual(merged["安魂曲"]["sub_stats"]["自定义"], 9.0)

    def test_sync_dry_run_does_not_write(self):
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            model = {
                "安魂曲": {
                    "level": 80,
                    "level_sub_stats": {
                        "80": {
                            "生命白值": 1.0,
                            "攻击力白值": 2.0,
                            "防御力白值": 3.0,
                            "暴击率%": 5.0,
                            "暴击伤害%": 50.0,
                        }
                    },
                    "sub_stats": {
                        "生命白值": 1.0,
                        "攻击力白值": 2.0,
                        "防御力白值": 3.0,
                        "暴击率%": 5.0,
                        "暴击伤害%": 50.0,
                    },
                }
            }
            (config_dir / "my_roles_model.json").write_text(
                json.dumps(model, ensure_ascii=False),
                encoding="utf-8",
            )
            (config_dir / "roles.json").write_text(
                json.dumps({"安魂曲": {"workshop_item_id": "1004"}}, ensure_ascii=False),
                encoding="utf-8",
            )

            with mock.patch(
                "src.features.settings.nanoka_base_stats.resolve_version",
                return_value="1.2",
            ), mock.patch(
                "src.features.settings.nanoka_base_stats.fetch_character_index",
                return_value={"1004": {"zh": "安魂曲"}, "1075": {"zh": "伊洛伊"}},
            ), mock.patch(
                "src.features.settings.nanoka_base_stats.fetch_character_detail",
                return_value=_fake_character(
                    hp=[0] * 79 + [15998],
                    atk=[0] * 79 + [636],
                    defense=[0] * 79 + [909],
                ),
            ):
                summary = sync_nanoka_base_stats(config_dir, dry_run=True, levels=(80,))

            self.assertTrue(summary["dry_run"])
            self.assertFalse(summary["wrote"])
            self.assertEqual(summary["updated_count"], 1)
            self.assertIn("伊洛伊", summary["missing_remote_roles"])
            saved = json.loads((config_dir / "my_roles_model.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["安魂曲"]["level_sub_stats"]["80"]["攻击力白值"], 2.0)

    def test_sync_add_missing_character_stub(self):
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "my_roles_model.json").write_text("{}", encoding="utf-8")
            (config_dir / "roles.json").write_text("{}", encoding="utf-8")

            with mock.patch(
                "src.features.settings.nanoka_base_stats.resolve_version",
                return_value="1.2",
            ), mock.patch(
                "src.features.settings.nanoka_base_stats.fetch_character_index",
                return_value={"1075": {"zh": "伊洛伊", "element": "Nature"}},
            ), mock.patch(
                "src.features.settings.nanoka_base_stats.fetch_character_detail",
                return_value=_fake_character(character_id="1075"),
            ):
                summary = sync_nanoka_base_stats(
                    config_dir,
                    dry_run=False,
                    add_missing=True,
                    levels=(80,),
                )

            self.assertEqual(summary["added_roles"], ["伊洛伊"])
            model = json.loads((config_dir / "my_roles_model.json").read_text(encoding="utf-8"))
            roles = json.loads((config_dir / "roles.json").read_text(encoding="utf-8"))
            self.assertIn("伊洛伊", model)
            self.assertEqual(model["伊洛伊"]["atk_type"], "暗")  # fake detail uses Chaos
            self.assertEqual(roles["伊洛伊"]["workshop_item_id"], "1075")
            self.assertEqual(len(roles["伊洛伊"]["board_matrix"]), 5)


class NanokaWeaponStatsTests(unittest.TestCase):
    def test_extract_weapon_level_stats(self):
        levels = extract_weapon_level_stats(_fake_weapon(), levels=(1, 80))
        self.assertEqual(levels["1"]["攻击力白值"], 37.0)
        self.assertEqual(levels["80"]["攻击力白值"], 116.0)
        self.assertEqual(levels["80"]["暴击率%"], 9.6)

    def test_resolve_local_weapon_key_strips_quotes(self):
        weapons = {"倾世之雨": {"name": "倾世之雨"}}
        self.assertEqual(
            resolve_local_weapon_key("「倾世之雨」", weapons=weapons),
            "倾世之雨",
        )

    def test_merge_weapon_stats(self):
        weapons = {
            "穿过胭红蜃景": {
                "level": 80,
                "level_sub_stats": {"80": {"攻击力白值": 1.0, "暴击率%": 1.0}},
                "sub_stats": {"攻击力白值": 1.0, "暴击率%": 1.0},
            }
        }
        remote = {
            "穿过胭红蜃景": {
                "80": {"攻击力白值": 570.0, "暴击率%": 24.0},
            }
        }
        merged, summary = merge_nanoka_weapon_stats(weapons, remote)
        self.assertEqual(summary["updated_count"], 1)
        self.assertEqual(merged["穿过胭红蜃景"]["level_sub_stats"]["80"]["攻击力白值"], 570.0)

    def test_sync_weapons_dry_run(self):
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            weapons = {
                "穿过胭红蜃景": {
                    "name": "穿过胭红蜃景",
                    "level": 80,
                    "level_sub_stats": {"80": {"攻击力白值": 1.0, "暴击率%": 1.0}},
                    "sub_stats": {"攻击力白值": 1.0, "暴击率%": 1.0},
                }
            }
            (config_dir / "weapons.json").write_text(
                json.dumps(weapons, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch(
                "src.features.settings.nanoka_weapon_stats.resolve_version",
                return_value="1.2",
            ), mock.patch(
                "src.features.settings.nanoka_weapon_stats.fetch_weapon_index",
                return_value={"fork_LunarPhase": {"zh": "穿过胭红蜃景"}},
            ), mock.patch(
                "src.features.settings.nanoka_weapon_stats.fetch_weapon_detail",
                return_value=_fake_weapon(),
            ):
                summary = sync_nanoka_weapon_stats(config_dir, dry_run=True, levels=(80,))
            self.assertTrue(summary["dry_run"])
            self.assertFalse(summary["wrote"])
            saved = json.loads((config_dir / "weapons.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["穿过胭红蜃景"]["level_sub_stats"]["80"]["攻击力白值"], 1.0)


if __name__ == "__main__":
    unittest.main()
