# 薄荷三觉消费全队创生/覆纹命中，五觉只消费自身第一段 E。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_buff_inference_service import BattleBuffInferenceService


class _StaticDao:
    def list_forks(self):
        return []

    def get_suit(self, _suit_id):
        return None

    def get_equipment_modify_pack(self, _modify_pack_id):
        return None

    def get_equipment_buff_curve(self, _curve_id):
        return None

    def list_combat_effect_definitions(self, **filters):
        if (
            filters.get("owner_kind") == "character_awaken"
            and filters.get("owner_id") in {"1019:Effect3", "1019:Effect5"}
        ):
            owner_id = str(filters["owner_id"])
            return [{
                "effect_definition_id": f"character_awaken:{owner_id}",
                "parameters": {},
            }]
        return []

    def list_combat_effect_buff_links(self, _effect_definition_id):
        return []

    def list_character_bound_modifier_effects(self, character_id):
        if character_id != 1019:
            return []
        return [{
            "binding_kind": "active",
            "input_id": "ESkillInputIDType::InputID_Skill",
            "ability_id": "GA_Mint019_Skill",
            "ability_asset_path": "/Game/GA_Mint019_Skill",
            "event_tag": "Event.Montage.Player.Display.1",
            "effect_asset_path": (
                "/Game/Blueprints/Abilities/Player/Ability_019_Mint/"
                "Upgrade/Level5/Buff_Mint019_Level5_1"
            ),
            "effect_id": "Buff_Mint019_Level5_1",
            "target_type_asset_path": "",
        }]

    def get_buff_definition(self, asset_path):
        if not asset_path.casefold().endswith("buff_mint019_level5_1"):
            return None
        return {
            "definition_id": "Buff_Mint019_Level5_1",
            "duration_policy": "HasDuration",
            "duration_magnitude": {"ScalableFloatMagnitude": {"Value": 6.0}},
            "stack_limit_count": 1,
            "modifiers": [{
                "property_id": "CritDamageBase",
                "modifier_operation": "Additive",
                "magnitude_kind": "ScalableFloat",
                "magnitude_value": 0.25,
                "calculation_asset_path": None,
            }],
            "triggers": [],
        }


class BattleMintAwakeningBuffTests(unittest.TestCase):
    @staticmethod
    def _hit(
        event_id: str,
        time_us: int,
        *,
        character_id: int,
        gameplay_effect_id: str = "",
        damage_name: str = "伤害",
    ) -> BattleAnalysisHit:
        return BattleAnalysisHit(
            event_id=event_id,
            sequence=time_us,
            relative_time_us=time_us,
            character_id=character_id,
            character_name=str(character_id),
            skill_name="技能",
            damage_name=damage_name,
            damage_component="skill",
            attack_type="skill",
            damage_attribute="lakshana",
            target_id="target",
            target_name="目标",
            damage=1000,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
            gameplay_effect_id=gameplay_effect_id,
        )

    def test_selected_effect_starts_after_first_e_hit(self) -> None:
        build = {"characters": [{
            "character_id": 1019,
            "observed_name": "薄荷",
            "profile": {
                "selected_awaken_effect_ids": ["Effect5"],
                "awakening_selection_initialized": True,
            },
            "equipment": [],
        }]}
        rules = BattleBuffInferenceService.load_rules(_StaticDao(), build)
        effect_rules = tuple(
            row for row in rules
            if row.source_effect_definition_id == "character_awaken:1019:Effect5"
        )
        trigger = BattleAnalysisHit(
            event_id="mint-e-first",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1019,
            character_name="薄荷",
            skill_name="E",
            damage_name="E 第一段",
            damage_component="skill",
            attack_type="skill",
            damage_attribute="lakshana",
            target_id="target",
            target_name="目标",
            damage=1000,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
            gameplay_effect_id="GE_Player_Mint_Skill1_Damage_New",
        )

        intervals = BattleBuffInferenceService.infer(
            effect_rules,
            actions=(),
            hits=(trigger,),
            battle_end_us=10_000_000,
        )

        self.assertEqual(1, len(effect_rules))
        self.assertEqual(1_000_001, intervals[0].start_us)
        self.assertEqual(0.25, intervals[0].modifiers[0].magnitude_value)

    def test_effect3_uses_any_teammate_creation_or_weave_hit_and_refreshes(
        self,
    ) -> None:
        build = {"characters": [{
            "character_id": 1019,
            "observed_name": "薄荷",
            "profile": {
                "selected_awaken_effect_ids": ["Effect3"],
                "awakening_selection_initialized": True,
            },
            "equipment": [],
        }]}
        rules = BattleBuffInferenceService.load_rules(_StaticDao(), build)
        effect_rules = tuple(
            row for row in rules
            if row.source_effect_definition_id == "character_awaken:1019:Effect3"
        )
        creation = self._hit(
            "creation-by-teammate",
            1_000_000,
            character_id=1051,
            gameplay_effect_id="GE_ActorReaction_1_Damage",
        )
        weave = self._hit(
            "weave-by-other-teammate",
            5_000_000,
            character_id=1072,
            damage_name="覆纹",
        )

        intervals = BattleBuffInferenceService.infer(
            effect_rules,
            actions=(),
            hits=(creation, weave),
            battle_end_us=30_000_000,
        )

        self.assertEqual(1, len(effect_rules))
        self.assertEqual("self", effect_rules[0].target_scope)
        self.assertEqual(1, effect_rules[0].stack_limit_count)
        self.assertEqual(1, len(intervals))
        self.assertEqual((1_000_001, 20_000_001), (
            intervals[0].start_us,
            intervals[0].end_us,
        ))
        self.assertEqual(
            ("creation-by-teammate", "weave-by-other-teammate"),
            intervals[0].evidence_event_ids,
        )
        self.assertEqual("AtkUp", intervals[0].modifiers[0].property_id)
        self.assertEqual(0.15, intervals[0].modifiers[0].magnitude_value)


if __name__ == "__main__":
    unittest.main()
