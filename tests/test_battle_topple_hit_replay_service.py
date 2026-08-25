# 验证倾陷事件按每个出场角色的独立属性格子计算后求和。
from __future__ import annotations

import unittest
from dataclasses import replace
from math import ceil

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleTargetCondition,
)
from src.services.battle_hit_replay_explanation_service import (
    BattleHitReplayExplanationService,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_topple_hit_replay_service import (
    BattleToppleCharacterConfig,
    BattleToppleHitReplayService,
)


def _baseline(
    character_id: int,
    name: str,
    strength: float,
    def_ignore: float = 0.0,
) -> BattleCharacterBaseline:
    return BattleCharacterBaseline(
        character_id=character_id,
        character_name=name,
        source="frozen_fixture",
        character_level=80.0,
        stats=(
            BattleCharacterStat(
                "UnbalIntensityBase",
                "倾陷强度",
                strength,
                False,
            ),
            BattleCharacterStat("DefIgnore", "防御穿透", def_ignore, True),
        ),
    )


class BattleToppleHitReplayServiceTests(unittest.TestCase):
    def test_team_topple_sums_each_character_cell(self) -> None:
        hit = BattleAnalysisHit(
            event_id="316:primary",
            sequence=316,
            relative_time_us=77_652_616,
            character_id=1036,
            character_name="残虹",
            skill_name="倾陷伤害",
            damage_name="倾陷伤害",
            damage_component="unknown",
            attack_type="倾陷伤害",
            damage_attribute="true",
            target_id="boss",
            target_name="墨菲斯托",
            damage=191_670.0,
            direction="outgoing",
            is_follow_up=False,
            classification="topple",
            gameplay_effect_id="Buff_Tenacity_damage",
        )
        baselines = (
            _baseline(1003, "早雾", 348.0),
            _baseline(1004, "安魂曲", 78.0),
            _baseline(1036, "残虹", 0.0),
            _baseline(1039, "法帝娅", 96.0),
        )
        analysis = BattleAnalysisSnapshot(
            battle_record_id=7,
            capability_level="formal_hit",
            axis_complete=True,
            formula_model_version="fixture",
            name_mapping_version="fixture",
            action_inference_version="fixture",
            timeline_projection_version="fixture",
            battle_start_us=0,
            battle_end_us=80_000_000,
            timeline_end_us=80_000_000,
            range_start_us=0,
            range_end_us=80_000_000,
            duration_seconds=80.0,
            total_damage=hit.damage,
            total_dps=hit.damage / 80.0,
            timeline_hits=(hit,),
            inferred_actions=(),
            inferred_inputs=(),
            timeline_damage_groups=(),
            hits=(hit,),
            roles=(),
            skills=(),
            targets=(),
            baselines=baselines,
            target_condition=BattleTargetCondition(
                target_name="墨菲斯托",
                enemy_level=90.0,
                scene="outer_realm",
                defense_reduction=0.0,
                vulnerability=0.0,
                resistances=(
                    ("chaos", 0.20),
                    ("incantation", 0.20),
                    ("psyche", 0.50),
                ),
                enemy_defense_base=1050.0,
                enemy_topple_limit=70.0,
                environment_kind="feast",
            ),
        )
        attributes = {
            1003: "incantation",
            1004: "chaos",
            1036: "incantation",
            1039: "psyche",
        }
        configs = {
            character_id: BattleToppleCharacterConfig(
                character_id=character_id,
                damage_attribute=attribute,
                level_multiplier=3603.0,
            )
            for character_id, attribute in attributes.items()
        }

        result = BattleHitReplayService.replay(
            analysis,
            (),
            topple_character_configs=configs,
        )[0]

        self.assertEqual(191_640.0, result.selected_damage)
        self.assertAlmostEqual(
            (191_640.0 - 191_670.0) / 191_670.0 * 100.0,
            result.signed_error_percent,
        )
        cells = tuple(
            row
            for row in result.factors
            if row.factor_id.startswith("topple_character:")
        )
        self.assertEqual(4, len(cells))
        self.assertEqual(
            result.selected_damage,
            float(ceil(sum(row.value for row in cells))),
        )
        self.assertEqual("not_applicable", result.critical_state)
        detail = BattleHitReplayExplanationService.build(hit, result)
        self.assertIn("团队倾陷伤害 = 早雾倾陷贡献 + 安魂曲倾陷贡献", detail)
        self.assertIn("ceil(191,639.002817) = 191,640.00", detail)

    def test_split_topple_uses_only_the_complete_same_half_roster(self) -> None:
        hit = BattleAnalysisHit(
            event_id="454:primary",
            sequence=454,
            relative_time_us=55_858_101,
            character_id=1051,
            character_name="「零」",
            skill_name="倾陷伤害",
            damage_name="倾陷伤害",
            damage_component="unknown",
            attack_type="倾陷伤害",
            damage_attribute="true",
            target_id="boss",
            target_name="胶卷-MANISH",
            damage=119_156.0,
            direction="outgoing",
            is_follow_up=False,
            classification="topple",
            gameplay_effect_id="Buff_Tenacity_damage",
            scope_half="upper",
        )
        roles = {
            1003: ("早雾", "lower", 348.0, "incantation", 0.0),
            1004: ("安魂曲", "lower", 78.0, "chaos", 0.0),
            1036: ("残虹", "lower", 0.0, "incantation", 0.0),
            1039: ("法帝娅", "lower", 96.0, "psyche", 0.0),
            1010: ("娜娜莉", "upper", 60.0, "nature", 0.0),
            1051: ("「零」", "upper", 0.0, "cosmos", 0.25),
            1055: ("九原", "upper", 0.0, "nature", 0.0),
            1075: ("伊洛伊", "upper", 300.0, "nature", 0.0),
        }
        timeline_hits = tuple(
            replace(
                hit,
                event_id=f"{character_id}:observed",
                sequence=character_id,
                character_id=character_id,
                character_name=name,
                scope_half=scope_half,
            )
            for character_id, (name, scope_half, _, _, _) in roles.items()
        )
        baselines = tuple(
            _baseline(character_id, name, strength, def_ignore)
            for character_id, (name, _, strength, _, def_ignore) in roles.items()
        )
        analysis = BattleAnalysisSnapshot(
            battle_record_id=20,
            capability_level="formal_hit",
            axis_complete=True,
            formula_model_version="fixture",
            name_mapping_version="fixture",
            action_inference_version="fixture",
            timeline_projection_version="fixture",
            battle_start_us=0,
            battle_end_us=60_000_000,
            timeline_end_us=60_000_000,
            range_start_us=0,
            range_end_us=60_000_000,
            duration_seconds=60.0,
            total_damage=hit.damage,
            total_dps=hit.damage / 60.0,
            timeline_hits=timeline_hits,
            inferred_actions=(),
            inferred_inputs=(),
            timeline_damage_groups=(),
            hits=(hit,),
            roles=(),
            skills=(),
            targets=(),
            baselines=baselines,
            target_condition=BattleTargetCondition(
                target_name="胶卷-MANISH",
                enemy_level=80.0,
                scene="outer_realm",
                defense_reduction=0.0,
                vulnerability=0.0,
                resistances=(
                    ("chaos", 0.30),
                    ("cosmos", 0.10),
                    ("incantation", 0.30),
                    ("nature", 0.30),
                    ("psyche", 0.30),
                ),
                enemy_defense_base=1080.0,
                enemy_topple_limit=50.0,
            ),
        )
        configs = {
            character_id: BattleToppleCharacterConfig(
                character_id=character_id,
                damage_attribute=attribute,
                level_multiplier=3603.0,
            )
            for character_id, (_, _, _, attribute, _) in roles.items()
        }

        result = BattleToppleHitReplayService.replay(
            hit=hit,
            analysis=analysis,
            character_configs=configs,
        )

        cells = tuple(
            row
            for row in result.factors
            if row.factor_id.startswith("topple_character:")
        )
        self.assertEqual(
            {1010, 1051, 1055, 1075},
            {int(row.factor_id.rsplit(":", 1)[1]) for row in cells},
        )
        self.assertAlmostEqual(50 / 3, result.factors[0].value)
        self.assertEqual(119_157.0, result.selected_damage)
        self.assertAlmostEqual(
            (119_157.0 - 119_156.0) / 119_156.0 * 100.0,
            result.signed_error_percent,
        )

        incomplete = BattleToppleHitReplayService.replay(
            hit=hit,
            analysis=replace(analysis, timeline_hits=(hit,)),
            character_configs=configs,
        )
        self.assertEqual("unreplayable", incomplete.critical_state)
        self.assertIn("1 名同半场角色（预期 4 名）", incomplete.missing_evidence[0])

    def test_topple_does_not_fall_back_to_trigger_character_skill_evidence(self) -> None:
        hit = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1,
            character_id=1036,
            character_name="残虹",
            skill_name="倾陷伤害",
            damage_name="倾陷伤害",
            damage_component="unknown",
            attack_type="倾陷伤害",
            damage_attribute="true",
            target_id="boss",
            target_name="目标",
            damage=1.0,
            direction="outgoing",
            is_follow_up=False,
            classification="topple",
            gameplay_effect_id="Buff_Tenacity_damage",
        )
        analysis = BattleAnalysisSnapshot(
            battle_record_id=1,
            capability_level="formal_hit",
            axis_complete=True,
            formula_model_version="fixture",
            name_mapping_version="fixture",
            action_inference_version="fixture",
            timeline_projection_version="fixture",
            battle_start_us=0,
            battle_end_us=2,
            timeline_end_us=2,
            range_start_us=0,
            range_end_us=2,
            duration_seconds=1.0,
            total_damage=1.0,
            total_dps=1.0,
            timeline_hits=(hit,),
            inferred_actions=(),
            inferred_inputs=(),
            timeline_damage_groups=(),
            hits=(hit,),
            roles=(),
            skills=(),
            targets=(),
            baselines=(_baseline(1036, "残虹", 0.0),),
            target_condition=BattleTargetCondition(
                target_name="目标",
                enemy_level=1.0,
                scene="open_world",
                defense_reduction=0.0,
                vulnerability=0.0,
                resistances=(("incantation", 0.20),),
                enemy_defense_base=6.0,
                enemy_topple_limit=2.0,
            ),
        )

        result = BattleHitReplayService.replay(analysis, ())[0]

        self.assertEqual("unreplayable", result.critical_state)
        self.assertIn("静态属性或倾陷等级曲线", result.missing_evidence[0])


if __name__ == "__main__":
    unittest.main()
