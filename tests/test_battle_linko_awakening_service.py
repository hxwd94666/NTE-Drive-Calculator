# 验证灵可觉醒的固定轴公式替换、同频暴击率与领域共鸣投影。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_inference_service import BattleBuffInferenceService
from src.services.battle_character_awakening_hit_service import (
    character_awakening_damage_id,
)
from src.services.battle_skill_damage_evidence_service import (
    BattleSkillDamageEvidenceService,
)


class _StaticDao:
    @staticmethod
    def list_forks():
        return []

    @staticmethod
    def list_combat_effect_definitions(**_filters):
        return []

    @staticmethod
    def get_suit(_suit_id):
        return None

    @staticmethod
    def get_equipment_modify_pack(_modify_pack_id):
        return None

    @staticmethod
    def list_combat_effect_buff_links(_effect_definition_id):
        return []

    @staticmethod
    def list_character_bound_modifier_effects(_character_id):
        return []


class _SkillDamageDao:
    @staticmethod
    def get_skill_damage(damage_id: str):
        values = {
            "GE_Player_Radio072_UltraSkill3_Damage": 1.0,
            "GE_Player_Radio072_UltraSkill3_Damage_level2": 1.3,
        }
        if damage_id not in values:
            return None
        return {
            "ability_id": "GA_Radio072_UltraSkill",
            "damage_type": "nature",
            "damage_source_category": "NORMAL",
            "fixed_crit_rate": 0.0,
            "atk_rate_base": (values[damage_id],),
            "def_rate_base": (),
            "hp_rate_base": (),
        }

    @staticmethod
    def list_skill_damage_owner_character_ids(_damage_id: str):
        return [1072]

    @staticmethod
    def list_character_awaken_effects(_character_id: int):
        return tuple(
            {
                "effect_id": f"Effect{i}",
                "awaken_type": "Awaken_Effect",
                "skill_level_bonuses": [],
            }
            for i in range(1, 7)
        )

    @staticmethod
    def get_reaction_damage_curve(_damage_id: str):
        return None

    @staticmethod
    def get_combat_curve(_table_path: str, _curve_id: str):
        return None


def _linko(*effects: str) -> dict:
    return {
        "character_id": 1072,
        "observed_name": "灵可",
        "awakening_level": len(effects),
        "profile": {
            "awakening_selection_initialized": True,
            "selected_awaken_effect_ids": list(effects),
        },
        "equipment": [],
    }


def _hit(*, formula_context_kind: str) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id="1:primary",
        sequence=1,
        relative_time_us=2_000_000,
        character_id=1036,
        character_name="残虹",
        skill_name="同频合击",
        damage_name="同频伤害",
        damage_component="skill",
        attack_type="QTE",
        damage_attribute="incantation",
        target_id="target",
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        formula_context_kind=formula_context_kind,
    )


def _q_action() -> BattleInferredAction:
    return BattleInferredAction(
        action_id="linko-q:1",
        character_id=1072,
        character_name="灵可",
        action_name="超负荷共鸣",
        input_kind="Q",
        input_sequence="Q",
        start_us=1_000_000,
        end_us=2_000_000,
        hits=1,
        damage=100.0,
        identity_confidence="中",
        timing_confidence="中",
        inference_basis="fixture",
        evidence_event_ids=("1:primary",),
        gameplay_effect_ids=("GE_Player_Radio072_UltraSkill3_Damage",),
    )


class BattleLinkoAwakeningServiceTests(unittest.TestCase):
    def test_effect_two_switches_the_formal_ultimate_explosion_variant(self) -> None:
        base = "GE_Player_Radio072_UltraSkill3_Damage"
        awakened = f"{base}_level2"

        self.assertEqual(
            base,
            character_awakening_damage_id(_linko(), damage_id=awakened),
        )
        self.assertEqual(
            awakened,
            character_awakening_damage_id(
                _linko("Effect2"), damage_id=base
            ),
        )

    def test_effect_two_variant_is_consumed_by_skill_damage_evidence(self) -> None:
        base = "GE_Player_Radio072_UltraSkill3_Damage"
        awakened = f"{base}_level2"

        def load(raw_damage_id: str, *effects: str):
            hit = replace(
                _hit(formula_context_kind=""),
                character_id=1072,
                character_name="灵可",
                attack_type="Q技能",
                ability_id="GA_Radio072_UltraSkill",
                gameplay_effect_id=raw_damage_id,
            )
            character = _linko(*effects)
            character.update({
                "character_level": 80,
                "skills": [{
                    "skill_id": "GA_Radio072_UltraSkill",
                    "skill_level": 1,
                }],
            })
            return BattleSkillDamageEvidenceService.load(
                _SkillDamageDao(),
                SimpleNamespace(hits=(hit,), time_stop_intervals=()),
                {"characters": [character]},
            )[0]

        added = load(base, "Effect2")
        removed = load(awakened)

        self.assertEqual(awakened, added.damage_id)
        self.assertEqual(1.3, added.scaling_multiplier)
        self.assertEqual(base, removed.damage_id)
        self.assertEqual(1.0, removed.scaling_multiplier)

    def test_effect_six_only_projects_critical_rate_to_linko_coattack(self) -> None:
        no_rules = BattleBuffInferenceService.load_rules(
            _StaticDao(), {"characters": [_linko()]}
        )
        rules = BattleBuffInferenceService.load_rules(
            _StaticDao(), {"characters": [_linko("Effect6")]}
        )

        self.assertFalse(any(
            row.source_effect_definition_id == "character_awaken:1072:Effect6"
            for row in no_rules
        ))
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(),
            hits=(),
            battle_end_us=10_000_000,
        )
        matching = BattleBuffAttributeProjectionService.project_hit(
            _hit(formula_context_kind="linko_coattack:skill"), intervals
        )
        ordinary = BattleBuffAttributeProjectionService.project_hit(
            _hit(formula_context_kind=""), intervals
        )

        self.assertEqual(0.25, matching.modifiers[0].additive_value)
        self.assertEqual((), ordinary.modifiers)

    def test_resonance_six_projects_thirteen_active_seconds_from_q(self) -> None:
        rules = BattleBuffInferenceService.load_rules(
            _StaticDao(),
            {"characters": [_linko(*(f"Effect{i}" for i in range(1, 7)))]},
        )
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(_q_action(),),
            hits=(),
            battle_end_us=30_000_000,
            time_stop_intervals=((5_000_000, 10_000_000),),
        )
        resonance = next(
            row for row in intervals
            if row.source_effect_definition_id == "character_awaken:1072:resonance_6"
        )

        self.assertEqual((1_000_000, 19_000_000), (
            resonance.start_us,
            resonance.end_us,
        ))
        self.assertEqual(
            {"DamageUpNatureBase": 0.30, "DamageUpIncantationBase": 0.30},
            {row.property_id: row.magnitude_value for row in resonance.modifiers},
        )


if __name__ == "__main__":
    unittest.main()
