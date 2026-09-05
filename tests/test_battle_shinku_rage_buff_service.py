# 验证真红升腾增伤和六觉倍率只消费正式逐击身份与冻结养成。
from __future__ import annotations

from dataclasses import replace
import unittest

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_character_awakening_hit_service import (
    character_awakening_damage_multiplier,
)
from src.services.battle_shinku_rage_buff_service import (
    BattleShinkuRageBuffService,
    BattleShinkuRageConfig,
)


_AWAKENINGS = tuple(
    {"effect_id": f"Effect{ordinal}", "awaken_type": "Awaken_Effect"}
    for ordinal in range(1, 7)
) + ({"effect_id": "resonance_3", "awaken_type": "Awaken_Resonance"},)
_CONFIG = BattleShinkuRageConfig(0.3, 0.3, _AWAKENINGS)


def _build(*selected: str) -> dict:
    return {"characters": [{
        "character_id": 1076,
        "awakening_level": len(selected),
        "profile": {
            "awakening_selection_initialized": True,
            "selected_awaken_effect_ids": selected,
        },
    }]}


def _hit(damage_id: str = "GE_Player_Shinku_Skill2_Rage_Damage") -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id="rage-hit", sequence=1, relative_time_us=100,
        character_id=1076, character_name="真红", skill_name="天降赤锋",
        damage_name="天降赤锋", damage_component="skill", attack_type="E技能",
        damage_attribute="light", target_id="target-a", target_name="目标",
        damage=100, direction="outgoing", is_follow_up=False,
        classification="direct", ability_id="GA_Shinku_Skill",
        gameplay_effect_id=damage_id,
    )


def _projection_value(hit: BattleAnalysisHit, intervals: tuple) -> dict:
    return {
        row.property_id: row.additive_value
        for row in BattleBuffAttributeProjectionService.project_hit(hit, intervals).modifiers
    }


class BattleShinkuRageBuffServiceTests(unittest.TestCase):
    def test_three_selected_effects_add_resonance_in_damage_up_bucket(self) -> None:
        intervals = BattleShinkuRageBuffService.infer(
            build=_build("Effect3", "Effect4", "Effect6"), hits=(_hit(),), config=_CONFIG,
        )
        self.assertEqual(len(intervals), 1)
        self.assertEqual(_projection_value(_hit(), intervals), {"DamageUpGeneralBase": 0.6})
        self.assertEqual((intervals[0].start_us, intervals[0].end_us), (100, 101))

    def test_effect_three_alone_does_not_enable_count_gated_resonance(self) -> None:
        intervals = BattleShinkuRageBuffService.infer(
            build=_build("Effect3"), hits=(_hit(),), config=_CONFIG,
        )
        self.assertEqual(_projection_value(_hit(), intervals), {"DamageUpGeneralBase": 0.3})

    def test_same_time_targets_do_not_duplicate_source_buff_or_leak_to_other_hits(self) -> None:
        second = replace(_hit(), event_id="second-target", target_id="target-b")
        intervals = BattleShinkuRageBuffService.infer(
            build=_build(), hits=(_hit(), second), config=_CONFIG,
        )
        self.assertEqual(len(intervals), 1)
        self.assertEqual(len(intervals[0].evidence_event_ids), 2)
        self.assertEqual(_projection_value(second, intervals), {"DamageUpGeneralBase": 0.3})
        for unrelated in (
            _hit("GE_Player_Shinku_Watch_Damage"),
            _hit("GE_Player_Shinku_ReactionAOE_Damage"),
            replace(_hit(), character_id=1072),
            replace(_hit(), relative_time_us=101),
        ):
            with self.subTest(hit=unrelated):
                self.assertEqual(_projection_value(unrelated, intervals), {})

    def test_missing_static_config_retains_unknown_modifier(self) -> None:
        intervals = BattleShinkuRageBuffService.infer(
            build=_build(), hits=(_hit(),), config=None,
        )
        self.assertIsNone(intervals[0].modifiers[0].magnitude_value)
        self.assertEqual(intervals[0].value_confidence, "未解析")
        projection = BattleBuffAttributeProjectionService.project_hit(_hit(), intervals)
        self.assertFalse(projection.modifiers)
        self.assertTrue(any("尚未解析" in reason for reason in projection.exclusion_reasons))

    def test_effect_six_changes_only_five_formally_bound_rage_damage_ids(self) -> None:
        character = _build("Effect6")["characters"][0]
        for name in ("Skill1", "Skill2", "UltraSkillPre", "UltraSkill", "UltraSkill2"):
            result, _ = character_awakening_damage_multiplier(
                character, damage_id=f"GE_Player_Shinku_{name}_Rage_Damage",
                shinku_rage_skill_coefficient=0.3,
            )
            self.assertEqual(result, 1.3)
        for damage_id in (
            "GE_Player_Shinku_Melee1_Rage_Damage", "GE_Player_Shinku_Watch_Damage",
            "GE_Player_Shinku_Skill2_Damage",
        ):
            result, _ = character_awakening_damage_multiplier(
                character, damage_id=damage_id, shinku_rage_skill_coefficient=0.3,
            )
            self.assertEqual(result, 1.0)

    def test_effect_six_is_selected_identity_not_numeric_level(self) -> None:
        character = _build("Effect1", "Effect2", "Effect3")["characters"][0]
        result, _ = character_awakening_damage_multiplier(
            character, damage_id=_hit().gameplay_effect_id, shinku_rage_skill_coefficient=0.3,
        )
        self.assertEqual(result, 1.0)

    def test_missing_effect_six_curve_is_unknown_and_does_not_fall_back(self) -> None:
        result, basis = character_awakening_damage_multiplier(
            _build("Effect6")["characters"][0], damage_id=_hit().gameplay_effect_id,
        )
        self.assertIsNone(result)
        self.assertIn("缺少正式", basis)
