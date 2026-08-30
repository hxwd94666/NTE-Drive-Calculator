# 验证静态 GA、GE 与 Montage Notify 只生成可解释的动作窗口候选。
from __future__ import annotations

import unittest

from src.services.battle_animation_window_service import (
    BattleAnimationWindowService,
)


class _StaticDao:
    def list_character_combat_bindings(self, character_id: int):
        if character_id != 1001:
            return []
        return [
            {
                "ability_id": "GA_Test_Skill",
                "ability_asset_path": "/Game/Ability/GA_Test_Skill",
            }
        ]

    def get_combat_ability_graph(self, asset_path: str):
        if asset_path != "/Game/Ability/GA_Test_Skill":
            return None
        return {
            "montages": [
                {
                    "selector_key": "Skill1",
                    "montage_asset_path": "/Game/Animation/Test_Skill",
                },
                {
                    "selector_key": "DissolveMontage",
                    "montage_asset_path": "/Game/Animation/Test_Dissolve",
                },
            ],
            "effects": [
                {
                    "event_tag": "Event.Montage.Player.Skill.1",
                    "effect_id": "GE_Player_Test_Skill1_Damage",
                }
            ],
        }

    def get_combat_montage(self, asset_path: str):
        if asset_path == "/Game/Animation/Test_Dissolve":
            raise AssertionError("辅助 Montage 不应被加载")
        if asset_path != "/Game/Animation/Test_Skill":
            return None
        return {
            "duration_seconds": 5.8,
            "sections": [
                {"start_seconds": 0.0, "end_seconds": 5.8},
            ],
            "notifies": [
                {
                    "notify_name": "BP_SendGamePlayEvent_C",
                    "start_seconds": 0.25,
                    "event_tag": "Event.Montage.Player.Skill.1",
                },
                {
                    "notify_name": "BP_TriggerEndAbilityEffect_C",
                    "start_seconds": 0.9,
                    "event_tag": None,
                },
            ],
        }


class BattleAnimationWindowServiceTests(unittest.TestCase):
    def test_builds_exact_candidate_and_ignores_auxiliary_montage(self) -> None:
        candidates = BattleAnimationWindowService.load_candidates(
            _StaticDao(),
            character_ids=(1001,),
            ability_ids=("GA_Test_Skill",),
        )

        self.assertEqual(1, len(candidates))
        candidate = candidates[0]
        self.assertEqual("Skill1", candidate.selector_key)
        self.assertEqual(
            (("GE_Player_Test_Skill1_Damage", (250_000,)),),
            candidate.effect_hit_offsets_us,
        )
        self.assertEqual((900_000,), candidate.trigger_end_offsets_us)
        self.assertEqual(5_800_000, candidate.duration_us)

    def test_classifies_hold_prelude_followed_by_release_damage(self) -> None:
        class HoldReleaseDao(_StaticDao):
            def get_combat_ability_graph(self, _asset_path: str):
                return {
                    "references": [],
                    "montages": [
                        {
                            "selector_key": "Hold",
                            "montage_asset_path": "/Game/Animation/Hold",
                        },
                        {
                            "selector_key": "Branch1",
                            "montage_asset_path": "/Game/Animation/Branch1",
                        },
                    ],
                    "effects": [
                        {
                            "event_tag": "Event.Montage.Player.Branch.1",
                            "effect_id": "GE_Test_Branch1_Damage",
                        }
                    ],
                }

            def get_combat_montage(self, asset_path: str):
                if asset_path == "/Game/Animation/Hold":
                    return {
                        "duration_seconds": 3.0,
                        "sections": [
                            {
                                "section_name": "Hold",
                                "start_seconds": 0.0,
                                "end_seconds": 3.0,
                            }
                        ],
                        "notifies": [],
                    }
                return {
                    "duration_seconds": 1.0,
                    "sections": [
                        {
                            "section_name": "Branch1",
                            "start_seconds": 0.0,
                            "end_seconds": 1.0,
                        }
                    ],
                    "notifies": [
                        {
                            "notify_name": "BP_SendGamePlayEvent_C",
                            "start_seconds": 0.3,
                            "event_tag": "Event.Montage.Player.Branch.1",
                        },
                        {
                            "notify_name": "BP_TriggerEndAbilityEffect_C",
                            "start_seconds": 0.8,
                            "event_tag": None,
                        },
                    ],
                }

        candidates = BattleAnimationWindowService.load_candidates(
            HoldReleaseDao(),
            character_ids=(1001,),
            ability_ids=("GA_Test_Skill",),
        )

        branch = candidates[0]
        self.assertEqual("after_hold", branch.hold_damage_mode)
        self.assertEqual(3_000_000, branch.hold_prelude_us)

    def test_classifies_loop_damage_as_damage_during_hold(self) -> None:
        class LoopDamageDao(_StaticDao):
            def get_combat_ability_graph(self, _asset_path: str):
                return {
                    "references": [],
                    "montages": [
                        {
                            "selector_key": "Begin",
                            "montage_asset_path": "/Game/Animation/BeginLoop",
                        }
                    ],
                    "effects": [
                        {
                            "event_tag": "Event.Montage.Player.Skill.5",
                            "effect_id": "GE_Test_HoldTick_Damage",
                        }
                    ],
                }

            def get_combat_montage(self, _asset_path: str):
                return {
                    "duration_seconds": 2.0,
                    "sections": [
                        {
                            "section_name": "Begin",
                            "next_section_name": "Loop",
                            "start_seconds": 0.0,
                            "end_seconds": 0.5,
                        },
                        {
                            "section_name": "Loop",
                            "next_section_name": "End",
                            "start_seconds": 0.5,
                            "end_seconds": 1.7,
                        },
                        {
                            "section_name": "End",
                            "start_seconds": 1.7,
                            "end_seconds": 2.0,
                        },
                    ],
                    "notifies": [
                        *(
                            {
                                "notify_name": "BP_SendGamePlayEvent_C",
                                "start_seconds": value,
                                "event_tag": "Event.Montage.Player.Skill.5",
                            }
                            for value in (0.6, 0.8, 1.0, 1.2, 1.4, 1.6)
                        ),
                        {
                            "notify_name": "BP_TriggerEndAbilityEffect_C",
                            "start_seconds": 1.9,
                            "event_tag": None,
                        },
                    ],
                }

        candidate = BattleAnimationWindowService.load_candidates(
            LoopDamageDao(),
            character_ids=(1001,),
            ability_ids=("GA_Test_Skill",),
        )[0]

        self.assertEqual("during_hold", candidate.hold_damage_mode)
        self.assertEqual(0, candidate.hold_prelude_us)

    def test_shared_hold_montage_is_scoped_to_the_selected_section_chain(self) -> None:
        class SharedHoldMontageDao(_StaticDao):
            def get_combat_ability_graph(self, _asset_path: str):
                return {
                    "references": [],
                    "montages": [
                        {
                            "selector_key": "Skill2Hold",
                            "montage_asset_path": "/Game/Animation/SharedHold",
                        },
                        {
                            "selector_key": "LoopHold",
                            "montage_asset_path": "/Game/Animation/SharedHold",
                        },
                    ],
                    "effects": [
                        {
                            "event_tag": "Event.Montage.Player.Skill.2_1",
                            "effect_id": "GE_Test_Skill2Hold_Damage",
                        },
                        {
                            "event_tag": "Event.Montage.Player.Skill.2",
                            "effect_id": "GE_Test_LoopHold_Damage",
                        },
                    ],
                }

            def get_combat_montage(self, _asset_path: str):
                return {
                    "duration_seconds": 4.0,
                    "sections": [
                        {
                            "section_name": "Skill2Hold",
                            "next_section_name": "End",
                            "start_seconds": 0.0,
                            "end_seconds": 1.0,
                        },
                        {
                            "section_name": "End",
                            "start_seconds": 1.0,
                            "end_seconds": 2.0,
                        },
                        {
                            "section_name": "LoopHold",
                            "start_seconds": 2.0,
                            "end_seconds": 4.0,
                        },
                    ],
                    "notifies": [
                        {
                            "notify_name": "BP_SendGamePlayEvent_C",
                            "start_seconds": 0.4,
                            "event_tag": "Event.Montage.Player.Skill.2_1",
                        },
                        {
                            "notify_name": "BP_TriggerEndAbilityEffect_C",
                            "start_seconds": 1.5,
                            "event_tag": None,
                        },
                        *(
                            {
                                "notify_name": "BP_SendGamePlayEvent_C",
                                "start_seconds": value,
                                "event_tag": "Event.Montage.Player.Skill.2",
                            }
                            for value in (2.2, 2.4, 2.6, 2.8, 3.0)
                        ),
                        {
                            "notify_name": "BP_TriggerEndAbilityEffect_C",
                            "start_seconds": 3.5,
                            "event_tag": None,
                        },
                    ],
                }

        candidates = BattleAnimationWindowService.load_candidates(
            SharedHoldMontageDao(),
            character_ids=(1001,),
            ability_ids=("GA_Test_Skill",),
        )
        by_selector = {candidate.selector_key: candidate for candidate in candidates}

        skill2 = by_selector["Skill2Hold"]
        self.assertEqual(
            (("GE_Test_Skill2Hold_Damage", (400_000,)),),
            skill2.effect_hit_offsets_us,
        )
        self.assertEqual((1_500_000,), skill2.trigger_end_offsets_us)
        self.assertEqual(2_000_000, skill2.duration_us)
        self.assertEqual("during_hold", skill2.hold_damage_mode)

        loop = by_selector["LoopHold"]
        self.assertEqual(
            (
                (
                    "GE_Test_LoopHold_Damage",
                    (200_000, 400_000, 600_000, 800_000, 1_000_000),
                ),
            ),
            loop.effect_hit_offsets_us,
        )
        self.assertEqual((1_500_000,), loop.trigger_end_offsets_us)
        self.assertEqual(2_000_000, loop.duration_us)
        self.assertEqual("during_hold", loop.hold_damage_mode)

    def test_classifies_hold_trigger_target_as_release_damage(self) -> None:
        class HoldTriggerDao(_StaticDao):
            def get_combat_ability_graph(self, _asset_path: str):
                return {
                    "references": [],
                    "semantic_properties": [
                        {
                            "property_path": "$[10].Properties.HoldTimeTrigger",
                            "property_name": "HoldTimeTrigger",
                            "value": 0.2,
                        },
                        {
                            "property_path": "$[10].Properties.NextSectionName",
                            "property_name": "NextSectionName",
                            "value": "Skill2",
                        },
                    ],
                    "montages": [
                        {
                            "selector_key": "Skill1",
                            "montage_asset_path": "/Game/Animation/Skill1",
                        },
                        {
                            "selector_key": "Skill2",
                            "montage_asset_path": "/Game/Animation/Skill2",
                        },
                    ],
                    "effects": [
                        {
                            "event_tag": "Event.Montage.Player.Skill.1",
                            "effect_id": "GE_Test_Skill1_Damage",
                        },
                        {
                            "event_tag": "Event.Montage.Player.Skill.3",
                            "effect_id": "GE_Test_Skill3_Damage",
                        },
                    ],
                }

            def get_combat_montage(self, asset_path: str):
                event = (
                    "Event.Montage.Player.Skill.1"
                    if asset_path.endswith("Skill1")
                    else "Event.Montage.Player.Skill.3"
                )
                return {
                    "duration_seconds": 1.0,
                    "sections": [
                        {
                            "section_name": asset_path.rsplit("/", 1)[-1],
                            "start_seconds": 0.0,
                            "end_seconds": 1.0,
                        }
                    ],
                    "notifies": [
                        {
                            "notify_name": "BP_SendGamePlayEvent_C",
                            "start_seconds": 0.3,
                            "event_tag": event,
                        },
                        {
                            "notify_name": "BP_TriggerEndAbilityEffect_C",
                            "start_seconds": 0.8,
                            "event_tag": None,
                        },
                    ],
                }

        candidates = BattleAnimationWindowService.load_candidates(
            HoldTriggerDao(),
            character_ids=(1001,),
            ability_ids=("GA_Test_Skill",),
        )
        by_selector = {row.selector_key: row for row in candidates}

        self.assertEqual("none", by_selector["Skill1"].hold_damage_mode)
        self.assertEqual("after_hold", by_selector["Skill2"].hold_damage_mode)
        self.assertEqual(200_000, by_selector["Skill2"].hold_prelude_us)

    def test_branch_repeat_follows_one_actor_reference_and_keeps_charge_prelude(self) -> None:
        class BranchActorDao(_StaticDao):
            def get_combat_ability_graph(self, _asset_path: str):
                return {
                    "semantic_properties": [],
                    "references": [
                        {
                            "target_asset_path": "/Game/Actor/Branch5Bullet",
                        }
                    ],
                    "montages": [
                        {
                            "selector_key": "Branch",
                            "montage_asset_path": "/Game/Animation/BranchCharge",
                        },
                        {
                            "selector_key": "Branch5",
                            "montage_asset_path": "/Game/Animation/Branch5",
                        },
                    ],
                    "effects": [],
                }

            def get_combat_blueprint_asset(self, asset_path: str):
                if asset_path != "/Game/Actor/Branch5Bullet":
                    return None
                return {
                    "references": [
                        {
                            "target_asset_path": "/Game/Damage/GE_Test_Branch5_Damage",
                        }
                    ]
                }

            def get_combat_montage(self, asset_path: str):
                if asset_path.endswith("BranchCharge"):
                    return {
                        "duration_seconds": 3.0,
                        "sections": [
                            {
                                "section_name": "Branch",
                                "start_seconds": 0.0,
                                "end_seconds": 1.0,
                            },
                            {
                                "section_name": "Atk",
                                "start_seconds": 2.0,
                                "end_seconds": 3.0,
                            },
                        ],
                        "notifies": [],
                    }
                return {
                    "duration_seconds": 2.0,
                    "sections": [
                        {
                            "section_name": "Branch5",
                            "start_seconds": 0.0,
                            "end_seconds": 2.0,
                        }
                    ],
                    "notifies": [
                        *(
                            {
                                "notify_name": "BP_SendGamePlayEvent_C",
                                "start_seconds": value,
                                "event_tag": "Event.Montage.Player.Branch.5",
                            }
                            for value in (0.6, 0.8, 1.0, 1.2, 1.4, 1.6)
                        ),
                        {
                            "notify_name": "BP_TriggerEndAbilityEffect_C",
                            "start_seconds": 1.8,
                            "event_tag": None,
                        },
                    ],
                }

        candidate = BattleAnimationWindowService.load_candidates(
            BranchActorDao(),
            character_ids=(1001,),
            ability_ids=("GA_Test_Skill",),
        )[0]

        self.assertEqual("Branch5", candidate.selector_key)
        self.assertEqual("during_hold", candidate.hold_damage_mode)
        self.assertEqual(2_000_000, candidate.hold_prelude_us)
        self.assertEqual(
            "GE_Test_Branch5_Damage",
            candidate.effect_hit_offsets_us[0][0],
        )


if __name__ == "__main__":
    unittest.main()
