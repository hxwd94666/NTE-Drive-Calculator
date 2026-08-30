# 验证普通浊燃与残虹浊燃不会在战报公式证据中被合并。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_dot_stack_state_service import (
    reconstruct_dot_stack_states,
)
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)
from src.services.battle_skill_damage_evidence_service import (
    BattleSkillDamageEvidenceService,
)
from src.services.battle_special_hit_replay_service import (
    BattleSpecialHitReplayService,
)


def _hit(damage_id: str, *, attribute: str = "nature") -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id="scorch:1",
        sequence=1,
        relative_time_us=1_000_000,
        character_id=1003,
        character_name="早雾",
        skill_name="环合",
        damage_name="浊燃",
        damage_component="reaction",
        attack_type="环合·浊燃",
        damage_attribute=attribute,
        target_id="target",
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification="reaction",
        gameplay_effect_id=damage_id,
    )


class _Dao:
    @staticmethod
    def get_combat_curve(_table_path: str, _curve_id: str):
        return None

    @staticmethod
    def get_skill_damage(damage_id: str):
        return {
            "ability_id": "",
            "damage_type": (
                "incantation" if damage_id.endswith("_1036") else "normal"
            ),
            "damage_source_category": "R",
            "fixed_crit_rate": 0.5,
            "atk_rate_base": (1.5,),
            "def_rate_base": (),
            "hp_rate_base": (),
        }

    @staticmethod
    def get_reaction_damage_curve(_damage_id: str):
        return {"points": tuple({"value": 2700.0} for _ in range(16))}

    @staticmethod
    def list_character_awaken_effects(_character_id: int):
        return ()


def _build(*, zankou_stage: int | None) -> dict[str, object]:
    characters: list[dict[str, object]] = [
        {
            "character_id": 1003,
            "character_level": 40,
            "profile": {},
            "stats": ({
                "source_group": "resolved",
                "property_id": "MagBase",
                "value": 0.0,
            },),
        },
        {
            "character_id": 1004,
            "character_level": 80,
            "profile": {},
            "stats": ({
                "source_group": "resolved",
                "property_id": "MagBase",
                "value": 600.0,
            },),
        },
    ]
    if zankou_stage is not None:
        characters.append({
            "character_id": 1036,
            "character_level": 80,
            "breakthrough_stage": zankou_stage,
            "profile": {},
        })
    return {"characters": characters}


class BattleScorchFormulaSplitTests(unittest.TestCase):
    def test_ordinary_uses_core_owner_and_zankou_replacement_uses_zankou(
        self,
    ) -> None:
        ordinary_analysis = SimpleNamespace(
            hits=(_hit("Buff_Reaction_5_new"),),
            time_stop_intervals=(),
        )
        zankou_analysis = SimpleNamespace(
            hits=(
                _hit("Buff_Reaction_5_new"),
                replace(
                    _hit("Buff_Reaction_5_new_1036"),
                    event_id="scorch:2",
                    sequence=2,
                    character_id=1036,
                    character_name="残虹",
                ),
            ),
            time_stop_intervals=(),
        )

        ordinary = BattleSkillDamageEvidenceService.load(
            _Dao(), ordinary_analysis, _build(zankou_stage=1)
        )
        replaced = BattleSkillDamageEvidenceService.load(
            _Dao(), zankou_analysis, _build(zankou_stage=2)
        )

        self.assertEqual(1003, ordinary[0].source_character_id)
        self.assertEqual("Buff_Reaction_5_new", ordinary[0].damage_id)
        self.assertEqual(1.0, ordinary[0].state_multiplier)
        self.assertEqual((1036, 1036), tuple(
            row.source_character_id for row in replaced
        ))
        self.assertEqual(("Buff_Reaction_5_new_1036",) * 2, tuple(
            row.damage_id for row in replaced
        ))
        self.assertEqual((1.0, 1.0), tuple(
            row.state_multiplier for row in replaced
        ))

    def test_reused_core_ge_requires_frozen_variant_evidence(self) -> None:
        ambiguous = replace(
            _hit("GE_Player_Zankou_DotDamage"),
            damage_attribute="",
        )
        analysis = SimpleNamespace(hits=(ambiguous,), time_stop_intervals=())

        unresolved = BattleSkillDamageEvidenceService.load(
            _Dao(), analysis, _build(zankou_stage=None)
        )
        variant_known = BattleSkillDamageEvidenceService.load(
            _Dao(), analysis, _build(zankou_stage=2)
        )

        self.assertEqual((), unresolved)
        self.assertEqual(1036, variant_known[0].source_character_id)
        self.assertEqual(1.0, variant_known[0].state_multiplier)
        self.assertIn("本机历史战报残差回归", variant_known[0].state_multiplier_basis)

    def test_zankou_unknown_application_snapshot_is_unreplayable(self) -> None:
        hit = replace(
            _hit("Buff_Reaction_5_new_1036", attribute="incantation"),
            character_id=1036,
            character_name="残虹",
        )
        evidence = replace(
            self._ordinary_evidence(hit),
            damage_id="Buff_Reaction_5_new_1036",
            damage_attribute="incantation",
            source_character_id=1036,
            state_multiplier=0.0,
            state_multiplier_basis="缺少逐层施加事件及触发时点快照",
            state_confidence="未解析",
        )

        result = BattleSpecialHitReplayService.replay(
            channel_id="reaction_scorch",
            formula_label="浊燃",
            hit=hit,
            evidence=evidence,
            projection=self._projection(hit),
            values={"MagBase": 600.0, "CritDamageBase": 0.5},
            analysis=SimpleNamespace(),
        )

        assert result is not None
        self.assertEqual("unreplayable", result.critical_state)
        self.assertIsNone(result.non_critical_damage)
        self.assertIsNone(result.critical_damage)
        self.assertIsNone(result.selected_damage)
        self.assertIsNone(result.expected_damage)
        self.assertIn("周期结算 hit 不能替代施加事件", result.missing_evidence[0])

    def test_formal_ordinary_scorch_never_uses_zankou_three_layers(self) -> None:
        hit = _hit("Buff_Reaction_5_new")
        analysis = SimpleNamespace(hits=(hit,), time_stop_intervals=())

        state = reconstruct_dot_stack_states(
            analysis,
            _build(zankou_stage=1),
        )[hit.event_id]

        self.assertEqual(1, state.coefficient)
        self.assertIn("普通浊燃最多 1 层", state.evidence_basis)

    def test_ordinary_scorch_requires_per_hit_attribute(self) -> None:
        hit = _hit("Buff_Reaction_5_new", attribute="")
        result = BattleSpecialHitReplayService.replay(
            channel_id="reaction_scorch",
            formula_label="浊燃",
            hit=hit,
            evidence=self._ordinary_evidence(hit),
            projection=self._projection(hit),
            values={"MagBase": 0.0, "CritDamageBase": 0.5},
            analysis=SimpleNamespace(),
        )

        assert result is not None
        self.assertIsNone(result.selected_damage)
        self.assertEqual("unreplayable", result.critical_state)
        self.assertIn("不能猜测目标抗性", result.missing_evidence[0])

    def test_ordinary_scorch_uses_observed_attribute_in_replay_and_marginal(self) -> None:
        hit = _hit("Buff_Reaction_5_new", attribute="nature")
        result = BattleSpecialHitReplayService.replay(
            channel_id="reaction_scorch",
            formula_label="浊燃",
            hit=hit,
            evidence=self._ordinary_evidence(hit),
            projection=self._projection(hit),
            values={"MagBase": 0.0, "CritDamageBase": 0.5},
            analysis=SimpleNamespace(
                target_condition=BattleTargetCondition(
                    target_name="目标",
                    enemy_level=80.0,
                    scene="outer_realm",
                    defense_reduction=0.0,
                    vulnerability=0.0,
                    resistances=(("nature", 0.10), ("incantation", 0.90)),
                    enemy_defense_base=0.0,
                ),
                baselines=(BattleCharacterBaseline(
                    character_id=1003,
                    character_name="早雾",
                    source="fixture",
                    stats=(),
                    character_level=40.0,
                ),),
                hits=(hit,),
            ),
        )

        assert result is not None and result.selected_damage is not None
        self.assertEqual("nature", result.formula_damage_attribute)
        self.assertTrue(BattleMarginalCalculationService._supports(
            "DamagePenetrateNature", hit, replay=result, character_id=1003
        ))
        self.assertFalse(BattleMarginalCalculationService._supports(
            "DamagePenetrateIncantation", hit, replay=result, character_id=1003
        ))

    @staticmethod
    def _ordinary_evidence(hit: BattleAnalysisHit) -> BattleSkillDamageEvidence:
        return BattleSkillDamageEvidence(
            event_id=hit.event_id,
            damage_id="Buff_Reaction_5_new",
            ability_id="",
            damage_attribute="normal",
            damage_source_category="R",
            fixed_crit_rate=0.5,
            scaling_property_id="Atk",
            scaling_multiplier=1.5,
            multiplier_coefficient=1.0,
            effective_skill_level=40,
            evidence_basis="普通浊燃正式记录",
            source_character_id=1003,
            formula_kind="reaction",
            level_multiplier=2700.0,
            state_multiplier=1.0,
            state_multiplier_label="浊燃结算前层数",
            state_multiplier_basis="普通浊燃固定一层",
            state_confidence="高",
            critical_policy="fixed",
        )

    @staticmethod
    def _projection(hit: BattleAnalysisHit) -> BattleHitBuffProjection:
        return BattleHitBuffProjection(
            event_id=hit.event_id,
            modifiers=(),
            applied_interval_ids=(),
            excluded_interval_ids=(),
            exclusion_reasons=(),
            confidence="高",
        )


if __name__ == "__main__":
    unittest.main()
