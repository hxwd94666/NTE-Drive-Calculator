# 战报修改副本投影必须同时替换人物与弧盘的完整养成状态。
from __future__ import annotations

import unittest
from copy import deepcopy

from src.domain.battle_report.build.inputs import world_bonus_edit_stats

from src.services.battle_report.build.edit_projection import (
    apply_battle_build_edit,
)


class BattleBuildEditProjectionServiceTests(unittest.TestCase):
    def test_explicit_world_bonus_recovery_keeps_original_and_inactive_edit(self) -> None:
        original = {"characters": [{"character_id": 1042, "stats": [
            {"source_group": "world_bonus", "property_id": "AtkAdd", "value": 20.0},
        ]}]}
        edited = {"character_id": 1042, "character_level": 80, "breakthrough_stage": 6,
                  "awakening_level": 2, "profile": {
                      "battle_world_bonus": {"AtkAdd": 14, "CritDamageBase": 0.04}}}
        value = deepcopy(original)
        apply_battle_build_edit(value, {"is_active": True, "characters": [edited]})
        self.assertEqual(14, value["characters"][0]["stats"][0]["value"])
        self.assertEqual(20, original["characters"][0]["stats"][0]["value"])
        value = deepcopy(original)
        apply_battle_build_edit(value, {"is_active": False, "characters": [edited]})
        self.assertEqual(original["characters"], value["characters"])
        for invalid in ({"AtkAdd": 14}, {"AtkAdd": 21, "CritDamageBase": 0.04},
                        {"AtkAdd": True, "CritDamageBase": 0.04},
                        {"AtkAdd": float("nan"), "CritDamageBase": 0.04}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                world_bonus_edit_stats(invalid)

    def test_active_edit_projects_fork_breakthrough_stage(self) -> None:
        build = {
            "characters": [{
                "character_id": 1004,
                "stats": [],
                "profile": {"fork_breakthrough_stage": 5},
            }],
        }
        edited_profile = {
            "character_id": 1004,
            "fork_id": "fork_Rose",
            "fork_level": 70,
            "fork_breakthrough_stage": 6,
        }
        build_edit = {
            "is_active": True,
            "characters": [{
                "character_id": 1004,
                "character_level": 70,
                "breakthrough_stage": 6,
                "awakening_level": 0,
                "fork_id": "fork_Rose",
                "fork_level": 70,
                "fork_breakthrough_stage": 6,
                "fork_refinement_level": 1,
                "selected_skill_id": None,
                "profile": edited_profile,
                "skills": [],
            }],
        }

        apply_battle_build_edit(build, build_edit)

        projected = build["characters"][0]
        self.assertEqual(6, projected["fork_breakthrough_stage"])
        self.assertEqual(6, projected["profile"]["fork_breakthrough_stage"])


if __name__ == "__main__":
    unittest.main()
