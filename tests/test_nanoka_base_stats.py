# nanoka 角色基础属性同步测试。
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.features.settings.nanoka_base_stats import (
    _write_role_configs,
    board_matrix_from_equip_slots,
    extract_level_base_stats,
    local_role_name_for_remote,
    merge_nanoka_base_stats_into_model,
    resolve_character_id,
    sync_nanoka_base_stats,
)
from tests.nanoka_fixtures import fake_character, write_json


def _role_model() -> dict:
    stats = {
        "生命白值": 1.0,
        "攻击力白值": 2.0,
        "防御力白值": 3.0,
        "暴击率%": 5.0,
        "暴击伤害%": 50.0,
    }
    return {
        "安魂曲": {
            "level": 80,
            "level_sub_stats": {"80": dict(stats)},
            "sub_stats": {**stats, "自定义": 9.0},
        }
    }


class NanokaBaseStatsTests(unittest.TestCase):
    def test_extract_level_base_stats_uses_one_based_levels(self):
        levels = extract_level_base_stats(fake_character(), levels=(1, 80))
        self.assertEqual(levels["1"]["生命白值"], 1000.0)
        self.assertEqual(levels["80"]["攻击力白值"], 129.0)
        self.assertEqual(levels["80"]["暴击率%"], 5.0)
        self.assertEqual(levels["80"]["暴击伤害%"], 50.0)

    def test_resolve_character_id_prefers_primary_workshop_id(self):
        roles_meta = {
            "主角": {
                "workshop_item_id": "1046",
                "workshop_item_ids": ["1046", "1051"],
            }
        }
        character_index = {"1046": {"zh": "零"}, "1051": {"zh": "「零」"}}
        self.assertEqual(
            resolve_character_id(
                "主角",
                roles_meta=roles_meta,
                character_index=character_index,
            ),
            "1046",
        )

    def test_resolve_character_id_falls_back_to_unique_name(self):
        self.assertEqual(
            resolve_character_id(
                "薄荷",
                roles_meta={},
                character_index={"1019": {"zh": "薄荷"}},
            ),
            "1019",
        )

    def test_resolve_character_id_rejects_ambiguous_name(self):
        with self.assertRaisesRegex(RuntimeError, "多个远端角色 ID"):
            resolve_character_id(
                "主角",
                roles_meta={},
                character_index={"1046": {"zh": "零"}, "1051": {"zh": "「零」"}},
            )

    def test_local_role_name_aliases(self):
        self.assertEqual(local_role_name_for_remote("「零」"), "主角")
        self.assertEqual(local_role_name_for_remote("法帝娅"), "法蒂娅")

    def test_board_matrix_crops_center_and_tolerates_bad_values(self):
        slots = fake_character()["equip_slots"]
        slots["slots"][3][3] = None
        matrix = board_matrix_from_equip_slots(slots)
        self.assertEqual(matrix[0], [0, 0, 0, 0, 0])
        self.assertEqual(matrix[2], [0, 0, 0, 0, 0])

    def test_merge_updates_level_and_current_sub_stats(self):
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
        merged, summary = merge_nanoka_base_stats_into_model(_role_model(), remote)
        self.assertEqual(summary["updated_count"], 1)
        self.assertEqual(merged["安魂曲"]["level_sub_stats"]["80"]["攻击力白值"], 636.0)
        self.assertEqual(merged["安魂曲"]["sub_stats"]["攻击力白值"], 636.0)
        self.assertEqual(merged["安魂曲"]["sub_stats"]["自定义"], 9.0)

    def test_sync_dry_run_does_not_write(self):
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_json(config_dir / "my_roles_model.json", _role_model())
            write_json(config_dir / "roles.json", {"安魂曲": {"workshop_item_id": "1004"}})
            with mock.patch(
                "src.features.settings.nanoka_base_stats.resolve_version",
                return_value="1.2",
            ), mock.patch(
                "src.features.settings.nanoka_base_stats.fetch_character_index",
                return_value={"1004": {"zh": "安魂曲"}, "1075": {"zh": "伊洛伊"}},
            ), mock.patch(
                "src.features.settings.nanoka_base_stats.fetch_character_detail",
                return_value=fake_character(
                    hp=[0] * 79 + [15998],
                    atk=[0] * 79 + [636],
                    defense=[0] * 79 + [909],
                ),
            ):
                summary = sync_nanoka_base_stats(config_dir, dry_run=True, levels=(80,))

            self.assertEqual(summary["updated_count"], 1)
            self.assertIn("伊洛伊", summary["missing_remote_roles"])
            saved = json.loads((config_dir / "my_roles_model.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["安魂曲"]["level_sub_stats"]["80"]["攻击力白值"], 2.0)

    def test_sync_fetch_error_blocks_all_writes(self):
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_json(config_dir / "my_roles_model.json", _role_model())
            write_json(config_dir / "roles.json", {"安魂曲": {"workshop_item_id": "1004"}})
            before = (config_dir / "my_roles_model.json").read_bytes()
            with mock.patch(
                "src.features.settings.nanoka_base_stats.resolve_version",
                return_value="1.2",
            ), mock.patch(
                "src.features.settings.nanoka_base_stats.fetch_character_index",
                return_value={"1004": {"zh": "安魂曲"}},
            ), mock.patch(
                "src.features.settings.nanoka_base_stats.fetch_character_detail",
                side_effect=RuntimeError("network"),
            ):
                summary = sync_nanoka_base_stats(config_dir)

            self.assertTrue(summary["partial"])
            self.assertFalse(summary["wrote"])
            self.assertEqual((config_dir / "my_roles_model.json").read_bytes(), before)

    def test_sync_add_missing_character_stub(self):
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_json(config_dir / "my_roles_model.json", {})
            write_json(config_dir / "roles.json", {})
            with mock.patch(
                "src.features.settings.nanoka_base_stats.resolve_version",
                return_value="1.2",
            ), mock.patch(
                "src.features.settings.nanoka_base_stats.fetch_character_index",
                return_value={"1075": {"zh": "伊洛伊"}},
            ), mock.patch(
                "src.features.settings.nanoka_base_stats.fetch_character_detail",
                return_value=fake_character(character_id="1075", element="Nature"),
            ):
                summary = sync_nanoka_base_stats(
                    config_dir,
                    add_missing=True,
                    levels=(80,),
                )

            model = json.loads((config_dir / "my_roles_model.json").read_text(encoding="utf-8"))
            roles = json.loads((config_dir / "roles.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["added_roles"], ["伊洛伊"])
            self.assertEqual(model["伊洛伊"]["atk_type"], "灵")
            self.assertEqual(roles["伊洛伊"]["workshop_item_id"], "1075")

    def test_role_config_write_rolls_back_both_files(self):
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            model_path = config_dir / "my_roles_model.json"
            roles_path = config_dir / "roles.json"
            write_json(model_path, {"old": "model"})
            write_json(roles_path, {"old": "roles"})
            originals = model_path.read_bytes(), roles_path.read_bytes()
            real_write = __import__(
                "src.features.settings.nanoka_base_stats",
                fromlist=["write_json_atomic"],
            ).write_json_atomic
            calls = 0

            def fail_second_write(path, data, indent=2):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("disk")
                real_write(path, data, indent=indent)

            with mock.patch(
                "src.features.settings.nanoka_base_stats.write_json_atomic",
                side_effect=fail_second_write,
            ):
                with self.assertRaisesRegex(RuntimeError, "disk"):
                    _write_role_configs(
                        model_path,
                        {"new": "model"},
                        roles_path,
                        {"new": "roles"},
                        write_model=True,
                        write_roles=True,
                    )

            self.assertEqual(model_path.read_bytes(), originals[0])
            self.assertEqual(roles_path.read_bytes(), originals[1])


if __name__ == "__main__":
    unittest.main()
