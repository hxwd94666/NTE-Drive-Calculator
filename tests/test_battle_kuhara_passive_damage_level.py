# 验证九原二被动追加清算不误用普通攻击技能等级。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_skill_damage_evidence_service import (
    BattleSkillDamageEvidenceService,
)


class _StaticDao:
    @staticmethod
    def get_combat_curve(_table_path: str, _curve_id: str):
        return None

    @staticmethod
    def get_skill_damage(_damage_id: str):
        return {
            "ability_id": "GA_Kuhara_Melee",
            "damage_type": "nature",
            "damage_source_category": "A",
            "fixed_crit_rate": 0.0,
            "atk_rate_base": tuple(0.15 for _ in range(13)),
            "def_rate_base": (),
            "hp_rate_base": (),
        }

    @staticmethod
    def get_reaction_damage_curve(_damage_id: str):
        return None

    @staticmethod
    def list_character_awaken_effects(_character_id: int):
        return ()

    @staticmethod
    def list_skill_level_ability_candidates(
        _character_id: int,
        _damage_id: str,
    ):
        return ["GA_Kuhara_Melee"]


class BattleKuharaPassiveDamageLevelTests(unittest.TestCase):
    def test_passive_settlement_does_not_use_melee_skill_level(self) -> None:
        hit = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1055,
            character_name="九原",
            skill_name="风声为我所用",
            damage_name="追加清算",
            damage_component="Additional Settlement",
            attack_type="Passive Damage",
            damage_attribute="unknown",
            target_id="target",
            target_name="目标",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
            ability_id="GA_Kuhara_Passive_2",
            gameplay_effect_id="GE_Player_Kuhara_SeedReaction_Damage",
        )
        analysis = SimpleNamespace(hits=(hit,), time_stop_intervals=())
        build = {"characters": [{
            "character_id": 1055,
            "character_level": 80,
            "skills": [{"skill_id": "GA_Kuhara_Melee", "skill_level": 6}],
            "profile": {},
        }]}

        evidence = BattleSkillDamageEvidenceService.load(
            _StaticDao(), analysis, build
        )[0]

        self.assertEqual("", evidence.ability_id)
        self.assertEqual(80, evidence.effective_skill_level)
        self.assertEqual(0.15, evidence.scaling_multiplier)
        self.assertEqual("A", evidence.damage_source_category)
        self.assertIn("不读取 A 或其他技能等级", evidence.evidence_basis)


if __name__ == "__main__":
    unittest.main()
