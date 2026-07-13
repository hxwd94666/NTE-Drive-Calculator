# nanoka 角色白值同步逻辑的单元测试。
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.features.settings.nanoka_base_stats import (
    extract_level_base_stats,
    merge_nanoka_base_stats_into_model,
    resolve_character_id,
    sync_nanoka_base_stats,
)


def _fake_character(*, character_id: str = "1004", hp=None, atk=None, defense=None) -> dict:
    hp = hp or [1000 + i for i in range(80)]
    atk = atk or [50 + i for i in range(80)]
    defense = defense or [60 + i for i in range(80)]
    return {
        "id": character_id,
        "name": "Lacrimosa",
        "stats": [
            {"id_stats": "HPMaxBase", "values": hp},
            {"id_stats": "AtkBase", "values": atk},
            {"id_stats": "DefBase", "values": defense},
            {"id_stats": "CritBase", "values": [5] * 80},
            {"id_stats": "CritDamageBase", "values": [50] * 80},
        ],
    }


class NanokaBaseStatsTests(unittest.TestCase):
    def test_extract_level_base_stats_uses_one_based_levels(self):
        character = _fake_character()
        levels = extract_level_base_stats(character, levels=(1, 80))
        self.assertEqual(levels["1"]["生命白值"], 1000.0)
        self.assertEqual(levels["1"]["攻击力白值"], 50.0)
        self.assertEqual(levels["80"]["生命白值"], 1079.0)
        self.assertEqual(levels["80"]["攻击力白值"], 129.0)
        self.assertEqual(levels["80"]["暴击率%"], 5.0)
        self.assertEqual(levels["80"]["暴击伤害%"], 50.0)

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

    def test_resolve_character_id_falls_back_to_zh_name(self):
        character_index = {"1019": {"zh": "薄荷"}}
        self.assertEqual(
            resolve_character_id(
                "薄荷",
                roles_meta={},
                character_index=character_index,
            ),
            "1019",
        )

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
                "src.features.settings.nanoka_base_stats.fetch_character_index",
                return_value={"1004": {"zh": "安魂曲"}},
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
            saved = json.loads((config_dir / "my_roles_model.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["安魂曲"]["level_sub_stats"]["80"]["攻击力白值"], 2.0)


if __name__ == "__main__":
    unittest.main()
