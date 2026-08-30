# 战报修改副本投影必须同时替换人物与弧盘的完整养成状态。
from __future__ import annotations

import unittest

from src.services.battle_build_edit_projection_service import (
    apply_battle_build_edit,
)


class BattleBuildEditProjectionServiceTests(unittest.TestCase):
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
