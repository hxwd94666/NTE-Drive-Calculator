# nanoka 武器基础属性同步测试。
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.features.settings.nanoka_weapon_stats import (
    extract_weapon_level_stats,
    merge_nanoka_weapon_stats,
    resolve_local_weapon_key,
    sync_nanoka_weapon_stats,
)
from tests.nanoka_fixtures import fake_weapon, write_json


def _weapon_config() -> dict:
    return {
        "穿过胭红蜃景": {
            "name": "穿过胭红蜃景",
            "level": 80,
            "level_sub_stats": {"80": {"攻击力白值": 1.0, "攻击力%": 24.0}},
            "sub_stats": {"攻击力白值": 1.0, "攻击力%": 24.0},
        }
    }


class NanokaWeaponStatsTests(unittest.TestCase):
    def test_extract_weapon_level_stats(self):
        levels = extract_weapon_level_stats(fake_weapon(), levels=(1, 80))
        self.assertEqual(levels["1"]["攻击力白值"], 37.0)
        self.assertEqual(levels["80"]["攻击力白值"], 116.0)
        self.assertEqual(levels["80"]["暴击率%"], 9.6)

    def test_resolve_local_weapon_key_strips_quotes(self):
        weapons = {"倾世之雨": {"name": "倾世之雨"}}
        self.assertEqual(
            resolve_local_weapon_key("「倾世之雨」", weapons=weapons),
            "倾世之雨",
        )

    def test_resolve_local_weapon_key_rejects_normalized_collision(self):
        weapons = {
            "倾世之雨": {"name": "倾世之雨"},
            "「倾世之雨」": {"name": "「倾世之雨」"},
        }
        with self.assertRaisesRegex(RuntimeError, "本地武器名称冲突"):
            resolve_local_weapon_key("倾世之雨", weapons=weapons)

    def test_merge_updates_current_stats_and_removes_stale_stat(self):
        remote = {
            "穿过胭红蜃景": {
                "80": {"攻击力白值": 570.0, "暴击率%": 24.0},
            }
        }
        merged, summary = merge_nanoka_weapon_stats(_weapon_config(), remote)
        self.assertEqual(summary["updated_count"], 1)
        self.assertEqual(merged["穿过胭红蜃景"]["sub_stats"]["攻击力白值"], 570.0)
        self.assertNotIn("攻击力%", merged["穿过胭红蜃景"]["sub_stats"])
        self.assertNotIn("攻击力%", merged["穿过胭红蜃景"]["level_sub_stats"]["80"])

    def test_sync_weapons_dry_run(self):
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_json(config_dir / "weapons.json", _weapon_config())
            with mock.patch(
                "src.features.settings.nanoka_weapon_stats.resolve_version",
                return_value="1.2",
            ), mock.patch(
                "src.features.settings.nanoka_weapon_stats.fetch_weapon_index",
                return_value={"fork_LunarPhase": {"zh": "穿过胭红蜃景"}},
            ), mock.patch(
                "src.features.settings.nanoka_weapon_stats.fetch_weapon_detail",
                return_value=fake_weapon(),
            ):
                summary = sync_nanoka_weapon_stats(config_dir, dry_run=True, levels=(80,))

            self.assertFalse(summary["wrote"])
            saved = json.loads((config_dir / "weapons.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["穿过胭红蜃景"]["sub_stats"]["攻击力白值"], 1.0)

    def test_sync_fetch_error_blocks_write(self):
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_json(config_dir / "weapons.json", _weapon_config())
            before = (config_dir / "weapons.json").read_bytes()
            with mock.patch(
                "src.features.settings.nanoka_weapon_stats.resolve_version",
                return_value="1.2",
            ), mock.patch(
                "src.features.settings.nanoka_weapon_stats.fetch_weapon_index",
                return_value={"fork_LunarPhase": {"zh": "穿过胭红蜃景"}},
            ), mock.patch(
                "src.features.settings.nanoka_weapon_stats.fetch_weapon_detail",
                side_effect=RuntimeError("network"),
            ):
                summary = sync_nanoka_weapon_stats(config_dir)

            self.assertTrue(summary["partial"])
            self.assertFalse(summary["wrote"])
            self.assertEqual((config_dir / "weapons.json").read_bytes(), before)

    def test_sync_add_missing_weapon(self):
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_json(config_dir / "weapons.json", {})
            with mock.patch(
                "src.features.settings.nanoka_weapon_stats.resolve_version",
                return_value="1.2",
            ), mock.patch(
                "src.features.settings.nanoka_weapon_stats.fetch_weapon_index",
                return_value={"fork_LunarPhase": {"zh": "穿过胭红蜃景"}},
            ), mock.patch(
                "src.features.settings.nanoka_weapon_stats.fetch_weapon_detail",
                return_value=fake_weapon(),
            ):
                summary = sync_nanoka_weapon_stats(
                    config_dir,
                    add_missing=True,
                    levels=(80,),
                )

            saved = json.loads((config_dir / "weapons.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["added_weapons"], ["穿过胭红蜃景"])
            self.assertEqual(saved["穿过胭红蜃景"]["type"], "聚合")


if __name__ == "__main__":
    unittest.main()
