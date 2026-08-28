# 验证环合等级曲线与原始包缺标签逐击的窄证据补全。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction
from src.services.battle_skill_damage_evidence_service import (
    BattleSkillDamageEvidenceService,
    _co_timed_damage_ids,
    _reaction_level_multiplier,
)


def _hit(
    event_id: str,
    *,
    damage_id: str,
    character_id: int = 1003,
    relative_time_us: int = 1_000_000,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=int(event_id.split(":", 1)[0]),
        relative_time_us=relative_time_us,
        character_id=character_id,
        character_name="早雾",
        skill_name="技能" if damage_id else "未识别技能",
        damage_name="伤害" if damage_id else "未识别伤害",
        damage_component="skill",
        attack_type="E技能",
        damage_attribute="incantation",
        target_id="target",
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        gameplay_effect_id=damage_id,
    )


class BattleSkillDamageEvidenceServiceTests(unittest.TestCase):
    def test_kuhara_effect_two_doubles_only_attachment_damage(self) -> None:
        class Dao:
            @staticmethod
            def get_combat_curve(_table_path: str, _curve_id: str):
                return None

            @staticmethod
            def get_skill_damage(_damage_id: str):
                return {
                    "ability_id": "GA_Kuhara_Melee",
                    "damage_type": "nature",
                    "damage_source_category": "NORMAL",
                    "fixed_crit_rate": 0.0,
                    "atk_rate_base": (0.15,),
                    "def_rate_base": (),
                    "hp_rate_base": (),
                }

            @staticmethod
            def get_reaction_damage_curve(_damage_id: str):
                return None

            @staticmethod
            def list_character_awaken_effects(_character_id: int):
                return ()

        analysis = SimpleNamespace(
            hits=(
                _hit(
                    "1:primary",
                    damage_id="GE_Player_Kuhara_Seed_Damage",
                    character_id=1055,
                ),
                _hit(
                    "2:primary",
                    damage_id="GE_Player_Kuhara_SeedReaction_Damage",
                    character_id=1055,
                ),
                _hit(
                    "3:primary",
                    damage_id="GE_Player_Kuhara_BudBoom_Damage",
                    character_id=1055,
                ),
            ),
            inferred_actions=(),
            time_stop_intervals=(),
        )
        character = {
            "character_id": 1055,
            "character_level": 80,
            "skills": [{"skill_id": "GA_Kuhara_Melee", "skill_level": 1}],
            "profile": {
                "awakening_selection_initialized": True,
                "selected_awaken_effect_ids": ["Effect2"],
            },
        }

        evidence = BattleSkillDamageEvidenceService.load(
            Dao(), analysis, {"characters": [character]}
        )

        self.assertEqual(2.0, evidence[0].multiplier_coefficient)
        self.assertIn("致命玫约伤害额外提升 100%", evidence[0].evidence_basis)
        self.assertEqual(1.0, evidence[1].multiplier_coefficient)
        self.assertEqual(1.0, evidence[2].multiplier_coefficient)
        character["profile"]["selected_awaken_effect_ids"] = []
        disabled = BattleSkillDamageEvidenceService.load(
            Dao(), analysis, {"characters": [character]}
        )
        self.assertEqual(1.0, disabled[0].multiplier_coefficient)

    def test_kuhara_q_settlement_uses_formal_state_coefficient(self) -> None:
        class Dao:
            @staticmethod
            def get_combat_curve(_table_path: str, curve_id: str):
                if curve_id == "Kuhara_BudBoom_CoefAddUltraSkill":
                    return {"points": ({"value": 4.0},)}
                return None

            @staticmethod
            def get_skill_damage(_damage_id: str):
                return {
                    "ability_id": "GA_Kuhara_UltraSkill",
                    "damage_type": "nature",
                    "damage_source_category": "NORMAL",
                    "fixed_crit_rate": 0.0,
                    "atk_rate_base": (1.0,),
                    "def_rate_base": (),
                    "hp_rate_base": (),
                }

            @staticmethod
            def get_reaction_damage_curve(_damage_id: str):
                return None

            @staticmethod
            def list_character_awaken_effects(_character_id: int):
                return ()

        hit = _hit(
            "2:primary",
            damage_id="GE_Player_Kuhara_BudBoom_Damage",
            character_id=1055,
            relative_time_us=1_500_000,
        )
        action = BattleInferredAction(
            action_id="action:1055:1:1",
            character_id=1055,
            character_name="九原",
            action_name="Q技能",
            input_kind="Q",
            input_sequence="Q4",
            start_us=1_000_000,
            end_us=1_400_000,
            hits=1,
            damage=10.0,
            identity_confidence="中",
            timing_confidence="中",
            inference_basis="正式 Q 逐击",
            evidence_event_ids=("1:primary",),
            gameplay_effect_ids=("GE_Player_Kuhara_UltraSkill4_Damage",),
        )
        analysis = SimpleNamespace(
            hits=(hit,),
            inferred_actions=(action,),
            time_stop_intervals=(),
        )
        build = {"characters": [{
            "character_id": 1055,
            "character_level": 80,
            "skills": [{
                "skill_id": "GA_Kuhara_UltraSkill",
                "skill_level": 1,
            }],
            "profile": {},
        }]}

        evidence = BattleSkillDamageEvidenceService.load(
            Dao(), analysis, build
        )[0]

        self.assertEqual(4.0, evidence.multiplier_coefficient)
        self.assertIn("Kuhara_BudBoom_CoefAddUltraSkill=4", evidence.evidence_basis)

    def test_kuhara_non_q_settlement_does_not_use_q_curve(self) -> None:
        class Dao:
            @staticmethod
            def get_combat_curve(_table_path: str, _curve_id: str):
                return {"points": ({"value": 4.0},)}

            @staticmethod
            def get_skill_damage(_damage_id: str):
                return {
                    "ability_id": "GA_Kuhara_Melee",
                    "damage_type": "nature",
                    "damage_source_category": "NORMAL",
                    "fixed_crit_rate": 0.0,
                    "atk_rate_base": (1.0,),
                    "def_rate_base": (),
                    "hp_rate_base": (),
                }

            @staticmethod
            def get_reaction_damage_curve(_damage_id: str):
                return None

            @staticmethod
            def list_character_awaken_effects(_character_id: int):
                return ()

        hit = _hit(
            "2:primary",
            damage_id="GE_Player_Kuhara_BudBoom_Damage",
            character_id=1055,
            relative_time_us=3_000_000,
        )
        analysis = SimpleNamespace(
            hits=(hit,),
            inferred_actions=(),
            time_stop_intervals=(),
        )
        build = {"characters": [{
            "character_id": 1055,
            "character_level": 80,
            "skills": [{"skill_id": "GA_Kuhara_Melee", "skill_level": 1}],
            "profile": {},
        }]}

        evidence = BattleSkillDamageEvidenceService.load(
            Dao(), analysis, build
        )[0]

        self.assertEqual(1.0, evidence.multiplier_coefficient)
    def test_derived_damage_uses_formal_parent_skill_level(self) -> None:
        class Dao:
            @staticmethod
            def get_combat_curve(_table_path: str, _curve_id: str):
                return {"points": ({"value": 1.0},)}

            @staticmethod
            def get_skill_damage(_damage_id: str):
                return {
                    "ability_id": "GA_Cang_SkillA",
                    "damage_type": "incantation",
                    "damage_source_category": "NORMAL",
                    "fixed_crit_rate": 0.0,
                    "atk_rate_base": (1.0, 2.0, 3.0),
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
                return ["GA_Cang_Skill"]

        hit = _hit(
            "1:primary",
            damage_id="GE_Player_Cang_SkillA_Damage",
            character_id=1023,
        )
        analysis = SimpleNamespace(hits=(hit,), time_stop_intervals=())
        build = {"characters": [{
            "character_id": 1023,
            "character_level": 80,
            "skills": [{"skill_id": "GA_Cang_Skill", "skill_level": 3}],
            "profile": {},
        }]}

        evidence = BattleSkillDamageEvidenceService.load(Dao(), analysis, build)[0]

        self.assertEqual("GA_Cang_Skill", evidence.ability_id)
        self.assertEqual(3, evidence.effective_skill_level)
        self.assertEqual(3.0, evidence.scaling_multiplier)
        self.assertIn("正式技能等级提示", evidence.evidence_basis)

    def test_derived_1071_damage_is_noncritical_and_has_no_q_level(self) -> None:
        class Dao:
            @staticmethod
            def get_combat_curve(_table_path: str, _curve_id: str):
                return {"points": ({"value": 1.0},)}

            @staticmethod
            def get_skill_damage(_damage_id: str):
                return {
                    "ability_id": "GA_Dragon_Created",
                    "damage_type": "cosmos",
                    "damage_source_category": "NORMAL",
                    "fixed_crit_rate": 0.0,
                    "atk_rate_base": (8.0,),
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
                return ["GA_Dragon_UltraSkill"]

        hit = _hit(
            "1:primary",
            damage_id="GE_Reaction_3_new_1071_Damage",
            character_id=1071,
        )
        analysis = SimpleNamespace(hits=(hit,), time_stop_intervals=())
        build = {"characters": [{
            "character_id": 1071,
            "character_level": 80,
            "skills": [{"skill_id": "GA_Dragon_UltraSkill", "skill_level": 10}],
            "profile": {},
        }]}

        evidence = BattleSkillDamageEvidenceService.load(Dao(), analysis, build)[0]

        self.assertEqual("", evidence.ability_id)
        self.assertEqual(80, evidence.effective_skill_level)
        self.assertEqual(8.0, evidence.scaling_multiplier)
        self.assertEqual("disabled", evidence.critical_policy)
        self.assertIn("不读取 Q", evidence.evidence_basis)

    def test_true_damage_critical_policy_stays_unknown(self) -> None:
        class Dao:
            @staticmethod
            def get_combat_curve(_table_path: str, _curve_id: str):
                return {"points": ({"value": 1.0},)}

            @staticmethod
            def get_skill_damage(_damage_id: str):
                return {
                    "ability_id": "GA_Daffodill_Passive",
                    "damage_type": "TRUE",
                    "damage_source_category": "TRUE",
                    "fixed_crit_rate": 0.0,
                    "atk_rate_base": (2.0,),
                    "def_rate_base": (),
                    "hp_rate_base": (),
                }

            @staticmethod
            def get_reaction_damage_curve(_damage_id: str):
                return None

        hit = _hit(
            "1:primary",
            damage_id="GE_Player_Daffodill_ExtraUnbalance_Damage",
            character_id=1054,
        )
        analysis = SimpleNamespace(hits=(hit,), time_stop_intervals=())
        build = {"characters": [{
            "character_id": 1054,
            "character_level": 80,
            "skills": [{"skill_id": "GA_Daffodill_Passive", "skill_level": 1}],
            "profile": {},
        }]}

        evidence = BattleSkillDamageEvidenceService.load(Dao(), analysis, build)[0]

        self.assertEqual("unknown", evidence.critical_policy)

    def test_resonance_skill_level_uses_formal_ability_allowlist(self) -> None:
        class Dao:
            @staticmethod
            def get_combat_curve(_table_path: str, _curve_id: str):
                return {"points": ({"value": 1.0},)}

            @staticmethod
            def get_skill_damage(damage_id: str):
                ability_id = {
                    "GE_E": "GA_Sagiri_Skill",
                    "GE_QTE": "GA_Sagiri_QTE",
                }[damage_id]
                return {
                    "ability_id": ability_id,
                    "damage_type": "incantation",
                    "damage_source_category": "NORMAL",
                    "fixed_crit_rate": 0.0,
                    "atk_rate_base": (1.0, 2.0),
                    "def_rate_base": (),
                    "hp_rate_base": (),
                }

            @staticmethod
            def get_reaction_damage_curve(_damage_id: str):
                return None

            @staticmethod
            def list_character_awaken_effects(_character_id: int):
                return [
                    *(
                        {
                            "effect_id": f"Effect{ordinal}",
                            "awaken_type": "Awaken_Effect",
                            "skill_level_bonuses": [],
                        }
                        for ordinal in range(1, 4)
                    ),
                    {
                        "effect_id": "resonance_3",
                        "awaken_type": "Awaken_Resonance",
                        "skill_level_bonuses": [{
                            "skill_id": "GA_Sagiri_Skill",
                            "level_delta": 1,
                        }],
                    },
                ]

        analysis = SimpleNamespace(
            hits=(
                _hit("1:primary", damage_id="GE_E"),
                _hit("2:primary", damage_id="GE_QTE"),
            ),
            time_stop_intervals=(),
        )
        build = {"characters": [{
            "character_id": 1003,
            "character_level": 80,
            "skills": [
                {"skill_id": "GA_Sagiri_Skill", "skill_level": 1},
                {"skill_id": "GA_Sagiri_QTE", "skill_level": 1},
            ],
            "profile": {
                "awakening_level": 3,
                "awakening_selection_initialized": True,
                "selected_awaken_effect_ids": ["Effect1", "Effect2", "Effect3"],
            },
        }]}

        evidence = BattleSkillDamageEvidenceService.load(Dao(), analysis, build)

        self.assertEqual(2, evidence[0].effective_skill_level)
        self.assertEqual(2.0, evidence[0].scaling_multiplier)
        self.assertEqual(1, evidence[1].effective_skill_level)
        self.assertEqual(1.0, evidence[1].scaling_multiplier)

    def test_ft_attack_rate_coefficient_does_not_modify_hp_damage_multiplier(
        self,
    ) -> None:
        class Dao:
            @staticmethod
            def get_combat_curve(_table_path: str, _curve_id: str):
                return {"points": ({"value": 1.0},)}

            @staticmethod
            def get_skill_damage(_damage_id: str):
                return {
                    "ability_id": "GA_Oneiroi_Branch5",
                    "damage_type": "nature",
                    "damage_source_category": "NORMAL",
                    "fixed_crit_rate": 0.0,
                    "atk_rate_base": (0.439,),
                    "def_rate_base": (),
                    "hp_rate_base": (),
                    "modifier_atk_rate_base_coefficient": 0.9,
                }

            @staticmethod
            def get_reaction_damage_curve(_damage_id: str):
                return None

        hit = _hit(
            "1:primary",
            damage_id="GE_Player_Oneiroi_Branch5_Damage",
            character_id=1075,
        )
        analysis = SimpleNamespace(hits=(hit,), time_stop_intervals=())
        build = {
            "characters": [{
                "character_id": 1075,
                "character_level": 80,
                "awakening_level": 0,
                "skills": [{
                    "skill_id": "GA_Oneiroi_Branch5",
                    "skill_level": 1,
                }],
                "profile": {},
            }]
        }

        evidence = BattleSkillDamageEvidenceService.load(
            Dao(),
            analysis,
            build,
        )[0]

        self.assertEqual(0.439, evidence.scaling_multiplier)
        self.assertEqual(1.0, evidence.multiplier_coefficient)
        self.assertIn("原始倍率数组", evidence.evidence_basis)

    def test_zankou_scorch_uses_zankou_as_formula_source(self) -> None:
        class Dao:
            @staticmethod
            def get_combat_curve(_table_path: str, _curve_id: str):
                return {"points": tuple({"value": 5.0} for _ in range(11))}

            @staticmethod
            def get_skill_damage(_damage_id: str):
                return {
                    "ability_id": "",
                    "damage_type": "incantation",
                    "damage_source_category": "R",
                    "fixed_crit_rate": 0.5,
                    "atk_rate_base": (1.5,),
                    "def_rate_base": (),
                    "hp_rate_base": (),
                }

            @staticmethod
            def get_reaction_damage_curve(_damage_id: str):
                return {"points": tuple({"value": value} for value in range(16))}

            @staticmethod
            def list_character_awaken_effects(_character_id: int):
                return ()

        hit = _hit(
            "1:primary",
            damage_id="Buff_Reaction_5_new_1036",
            character_id=1003,
        )
        analysis = SimpleNamespace(hits=(hit,), time_stop_intervals=())
        build = {"characters": [
            {"character_id": 1003, "character_level": 1, "profile": {}},
            {
                "character_id": 1036,
                "character_level": 80,
                "breakthrough_stage": 2,
                "profile": {},
            },
        ]}

        evidence = BattleSkillDamageEvidenceService.load(Dao(), analysis, build)[0]

        self.assertEqual(1036, evidence.source_character_id)
        self.assertEqual(80, evidence.effective_skill_level)
        self.assertIn("统一使用残虹", evidence.evidence_basis)

    def test_formal_skill_owner_conflict_never_uses_core_session_owner_panel(self) -> None:
        class Dao:
            @staticmethod
            def get_combat_curve(_table_path: str, _curve_id: str):
                return None

            @staticmethod
            def get_skill_damage(_damage_id: str):
                return {
                    "ability_id": "GA_Chaos071_Melee",
                    "damage_type": "chaos",
                    "damage_source_category": "NORMAL",
                    "fixed_crit_rate": 0.0,
                    "atk_rate_base": (1.0,),
                    "def_rate_base": (),
                    "hp_rate_base": (),
                }

            @staticmethod
            def list_skill_damage_owner_character_ids(_damage_id: str):
                return [1071]

            @staticmethod
            def get_reaction_damage_curve(_damage_id: str):
                return None

            @staticmethod
            def list_character_awaken_effects(_character_id: int):
                return ()

        hit = _hit(
            "1:primary",
            damage_id="GE_Player_Chaos071_Melee1_Damage",
            character_id=1077,
        )
        analysis = SimpleNamespace(hits=(hit,), time_stop_intervals=())
        core_owner_only = {"characters": [{
            "character_id": 1077,
            "character_level": 80,
            "profile": {},
        }]}

        self.assertEqual(
            (),
            BattleSkillDamageEvidenceService.load(Dao(), analysis, core_owner_only),
        )

        formal_owner = {"characters": [{
            "character_id": 1071,
            "character_level": 80,
            "skills": [{"skill_id": "GA_Chaos071_Melee", "skill_level": 1}],
            "profile": {},
        }]}
        evidence = BattleSkillDamageEvidenceService.load(
            Dao(), analysis, formal_owner
        )[0]
        self.assertEqual(1071, evidence.source_character_id)
        self.assertIn("不采用 Core 会话归属角色 1077", evidence.evidence_basis)

    def test_creation_uses_the_same_official_16_tier_lookup_as_scorch(self) -> None:
        class Dao:
            @staticmethod
            def get_reaction_damage_curve(effect_id: str):
                self.assertEqual("GE_ActorReaction_1_Damage", effect_id)
                return {"points": tuple({"value": value} for value in range(16))}

        value, basis = _reaction_level_multiplier(
            Dao(),
            "GE_ActorReaction_1_Damage",
            80,
        )

        self.assertEqual(15.0, value)
        self.assertEqual("reaction_damage[15]=15", basis)

    def test_missing_ge_uses_one_unique_same_microsecond_sibling(self) -> None:
        known = _hit("1:primary", damage_id="GE_Player_Sagiri_Skill3_Damage")
        missing = _hit("2:primary", damage_id="")
        analysis = SimpleNamespace(hits=(known, missing))

        inferred = _co_timed_damage_ids(analysis)

        self.assertEqual(
            "GE_Player_Sagiri_Skill3_Damage",
            inferred[missing.event_id][0],
        )

    def test_missing_ge_stays_unknown_when_same_microsecond_has_two_candidates(self) -> None:
        analysis = SimpleNamespace(
            hits=(
                _hit("1:primary", damage_id="GE_A"),
                _hit("2:primary", damage_id="GE_B"),
                _hit("3:primary", damage_id=""),
            )
        )

        self.assertNotIn("3:primary", _co_timed_damage_ids(analysis))


if __name__ == "__main__":
    unittest.main()
