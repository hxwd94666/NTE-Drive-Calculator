# 验证目标身份未知默认受控，正式 Boss/免控证据阻止默认成功。
from __future__ import annotations

import unittest

from src.services.battle_target_control_policy_service import (
    BattleTargetControlPolicyService,
    CONTROL_BLOCKED_BOSS,
    CONTROL_BLOCKED_FORMAL_IMMUNITY,
    CONTROL_ELIGIBLE_DEFAULT,
)


class _StaticMonsterTypes:
    def __init__(self, values: dict[str, str | None]) -> None:
        self._values = values

    def get_monster_enemy_type_by_formal_id(self, formal_id: str) -> str | None:
        return self._values.get(formal_id)


class BattleTargetControlPolicyServiceTests(unittest.TestCase):
    def test_unknown_or_non_boss_identity_defaults_to_controlled(self) -> None:
        dao = _StaticMonsterTypes({"mon_1": "Normal"})
        self.assertEqual(
            CONTROL_ELIGIBLE_DEFAULT,
            BattleTargetControlPolicyService.resolve_formal_policy(dao, ()),
        )
        self.assertEqual(
            CONTROL_ELIGIBLE_DEFAULT,
            BattleTargetControlPolicyService.resolve_formal_policy(
                dao,
                ("mon_1",),
            ),
        )

    def test_only_formally_resolved_boss_or_immunity_blocks_default(self) -> None:
        dao = _StaticMonsterTypes({"boss-template": "WeeklyBoss"})
        self.assertEqual(
            CONTROL_BLOCKED_BOSS,
            BattleTargetControlPolicyService.resolve_formal_policy(
                dao,
                ("boss-template",),
            ),
        )
        self.assertEqual(
            CONTROL_BLOCKED_FORMAL_IMMUNITY,
            BattleTargetControlPolicyService.resolve_formal_policy(
                dao,
                ("mon-unknown",),
                formal_control_immunity=True,
            ),
        )


if __name__ == "__main__":
    unittest.main()
