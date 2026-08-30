# 验证通用技能 Buff 只在正式目标语义明确时投影为队伍、自身或敌方。
from __future__ import annotations

import unittest

from src.services.battle_buff_target_scope_service import (
    BattleBuffTargetScopeService,
)


class BattleBuffTargetScopeServiceTests(unittest.TestCase):
    def test_explicit_team_marker_projects_team_scope(self) -> None:
        scope = BattleBuffTargetScopeService.for_skill_binding({
            "binding_kind": "active",
            "target_type_asset_path": "/Script/NTE.TargetType_AllPlayer",
        })

        self.assertEqual("team", scope)

    def test_empty_or_generic_target_type_does_not_default_to_self(self) -> None:
        self.assertEqual(
            "unknown",
            BattleBuffTargetScopeService.for_skill_binding({
                "binding_kind": "active",
                "target_type_asset_path": "",
            }),
        )
        self.assertEqual(
            "unknown",
            BattleBuffTargetScopeService.for_skill_binding({
                "binding_kind": "active",
                "target_type_asset_path": "/Script/NTE.DamageRangeTargetType",
            }),
        )

    def test_explicit_self_enemy_and_passive_binding_remain_distinct(self) -> None:
        self.assertEqual(
            "self",
            BattleBuffTargetScopeService.for_skill_binding({
                "binding_kind": "active",
                "target_type_asset_path": "/Script/NTE.TargetType_Owner",
            }),
        )
        self.assertEqual(
            "target",
            BattleBuffTargetScopeService.for_skill_binding({
                "binding_kind": "active",
                "target_type_asset_path": "/Script/NTE.TargetType_Enemy",
            }),
        )
        self.assertEqual(
            "self",
            BattleBuffTargetScopeService.for_skill_binding({
                "binding_kind": "passive_buff",
                "target_type_asset_path": "",
            }),
        )


if __name__ == "__main__":
    unittest.main()
