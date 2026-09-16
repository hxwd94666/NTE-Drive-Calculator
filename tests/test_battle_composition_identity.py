# 验证伤害构成不会用冻结面板的数字占位名称覆盖正式角色名称。
import unittest
from dataclasses import replace

from src.domain.battle_report import BattleRangeRoleSummary
from src.services.battle_damage_composition_service import BattleDamageCompositionService
from tests.test_battle_hit_buff_detail import _hit


class CompositionIdentityTests(unittest.TestCase):
    def test_names_survive_numeric_and_empty_baseline_placeholders(self):
        hit = replace(_hit(), character_id=1004, character_name="安魂曲")
        for baseline_name in ("1004", "", " "):
            for roles in ((), (BattleRangeRoleSummary(
                character_id=1004, character_name="安魂曲", hits=1,
                damage=hit.damage, dps=0, share_percent=100,
            ),)):
                with self.subTest(baseline=baseline_name, has_roles=bool(roles)):
                    result = BattleDamageCompositionService.calculate_from_hits(
                        roles=roles, hits=(hit,), segment_total_damage=hit.damage,
                        role_identities=((1004, baseline_name),),
                    )
                    self.assertEqual("安魂曲", result.roles[0].character_name)
                    self.assertEqual(hit.damage, result.roles[0].total_damage)

    def test_unknown_name_keeps_identity_and_damage(self):
        hit = replace(_hit(), character_id=9999, character_name="")
        result = BattleDamageCompositionService.calculate_from_hits(
            roles=(), hits=(hit,), segment_total_damage=hit.damage,
            role_identities=((9999, "9999"),),
        )
        self.assertEqual("9999", result.roles[0].character_name)
        self.assertEqual(hit.damage, result.roles[0].total_damage)
