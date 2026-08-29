# 验证创生被动只移除有正式事件证据的伤害，缺失机制状态始终留为未量化。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
)
from src.services.battle_creation_passive_counterfactual_service import (
    BattleCreationPassiveCounterfactualService,
)
from src.services.battle_creation_passive_evaluation_service import (
    BattleCreationPassiveEvidence,
    BattleCreationPassiveEvaluationService,
)


def _hit(
    event_id: str,
    damage: float,
    *,
    character_id: int,
    character_name: str,
    ability_id: str = "",
    gameplay_effect_id: str = "",
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=int(event_id.split(":", 1)[0]),
        relative_time_us=int(event_id.split(":", 1)[0]) * 1_000_000,
        character_id=character_id,
        character_name=character_name,
        skill_name="创生被动审计",
        damage_name="创生花",
        damage_component="创生花",
        attack_type="创生",
        damage_attribute="spirit",
        target_id="target",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification="reaction_creation",
        ability_id=ability_id,
        gameplay_effect_id=gameplay_effect_id,
    )


def _snapshot(
    hits: tuple[BattleAnalysisHit, ...],
    *,
    baselines: tuple[BattleCharacterBaseline, ...] = (),
    time_stop_intervals: tuple[tuple[int | None, int | None], ...] = (),
    axis_complete: bool = True,
) -> BattleAnalysisSnapshot:
    total_damage = sum(float(hit.damage) for hit in hits)
    return BattleAnalysisSnapshot(
        battle_record_id=36,
        capability_level="formal_hit",
        axis_complete=axis_complete,
        formula_model_version="fixture",
        name_mapping_version="fixture",
        action_inference_version="fixture",
        timeline_projection_version="fixture",
        battle_start_us=0,
        battle_end_us=20_000_000,
        timeline_end_us=20_000_000,
        range_start_us=0,
        range_end_us=20_000_000,
        duration_seconds=20.0,
        total_damage=total_damage,
        total_dps=total_damage / 20.0,
        timeline_hits=hits,
        inferred_actions=(),
        inferred_inputs=(),
        timeline_damage_groups=(),
        hits=hits,
        roles=(),
        skills=(),
        targets=(),
        baselines=baselines,
        time_stop_intervals=time_stop_intervals,
        effective_damage=total_damage,
        effective_dps=total_damage / 20.0,
    )


def _baseline(
    character_id: int,
    character_name: str,
    *,
    enabled_team_passive_ids: tuple[str, ...] = (),
) -> BattleCharacterBaseline:
    return BattleCharacterBaseline(
        character_id=character_id,
        character_name=character_name,
        source="fixture",
        stats=(),
        enabled_team_passive_ids=enabled_team_passive_ids,
    )


def _character(character_id: int, character_name: str) -> dict[str, object]:
    return {
        "character_id": character_id,
        "observed_name": character_name,
        "breakthrough_stage": 2,
        "profile": {},
    }


class BattleCreationPassiveCounterfactualServiceTests(unittest.TestCase):
    def test_explicit_passive_hits_keep_source_provider_and_evidence_separate(
        self,
    ) -> None:
        hits = (
            _hit(
                "1:nanally",
                35_064.0,
                character_id=1010,
                character_name="娜娜莉",
                ability_id="GA_Nanally_Passive_2",
            ),
            _hit(
                "2:kuhara",
                209_035.0,
                character_id=1055,
                character_name="九原",
                gameplay_effect_id="GE_Player_Kuhara_SeedReaction_Damage",
            ),
            _hit(
                "3:oneiroi",
                484_698.0,
                character_id=1051,
                character_name="「零」",
                ability_id="GA_Oneiroi_Passive_1",
                gameplay_effect_id="GE_ActorReaction_1_1019_Damage",
            ),
            _hit(
                "4:ordinary",
                1_000_000.0,
                character_id=1051,
                character_name="「零」",
                gameplay_effect_id="GE_ActorReaction_1_Damage",
            ),
        )
        analysis = _snapshot(
            hits,
            baselines=(
                _baseline(1010, "娜娜莉"),
                _baseline(1055, "九原"),
                _baseline(1075, "伊洛伊"),
            ),
        )

        results = BattleCreationPassiveCounterfactualService.calculate(analysis)
        by_source = {row.source_character_id: row for row in results}

        self.assertEqual({1010, 1055, 1075}, set(by_source))
        self.assertEqual(("1:nanally",), by_source[1010].evidence_event_ids)
        self.assertEqual(("2:kuhara",), by_source[1055].evidence_event_ids)
        self.assertEqual(("3:oneiroi",), by_source[1075].evidence_event_ids)
        self.assertEqual(35_064.0, by_source[1010].quantified_damage_gain)
        self.assertEqual(209_035.0, by_source[1055].quantified_damage_gain)
        self.assertEqual(484_698.0, by_source[1075].quantified_damage_gain)
        self.assertEqual(
            sum(hit.damage for hit in hits),
            by_source[1010].damage_coverage.basis_damage,
        )
        self.assertEqual(35_064.0, by_source[1010].damage_coverage.covered_damage)
        self.assertEqual(1010, by_source[1010].beneficiaries[0].character_id)
        self.assertEqual(
            100.0,
            by_source[1010].beneficiaries[0].damage_coverage.covered_percent,
        )
        self.assertEqual(1055, by_source[1055].beneficiaries[0].character_id)

        replica = by_source[1075]
        self.assertEqual("partial", replica.quantification.status)
        self.assertIsNone(replica.damage_gain)
        self.assertIsNone(replica.without_buff_damage)
        self.assertEqual(1, len(replica.beneficiaries))
        self.assertEqual(1051, replica.beneficiaries[0].character_id)
        self.assertEqual(484_698.0, replica.beneficiaries[0].quantified_damage_gain)
        self.assertIsNone(replica.beneficiaries[0].damage_gain)
        self.assertGreater(
            replica.beneficiaries[0].quantification.unavailable_damage,
            0.0,
        )
        self.assertEqual(
            replica.quantified_damage_gain,
            sum(
                row.quantified_damage_gain or 0.0
                for row in replica.beneficiaries
            ) + replica.quantified_unattributed_damage_gain,
        )

    def test_unknown_downstream_is_not_exposed_as_complete_zero(self) -> None:
        analysis = _snapshot((
            _hit(
                "1:nanally",
                100.0,
                character_id=1010,
                character_name="娜娜莉",
                gameplay_effect_id="GE_Nanally010_Lv1_Damage",
            ),
            _hit(
                "2:ordinary",
                900.0,
                character_id=1010,
                character_name="娜娜莉",
                gameplay_effect_id="GE_ActorReaction_1_Damage",
            ),
        ))

        result = BattleCreationPassiveCounterfactualService.calculate(analysis)[0]

        self.assertEqual("partial", result.quantification.status)
        self.assertEqual(100.0, result.quantification.quantified_increment)
        self.assertEqual(900.0, result.quantification.unavailable_damage)
        self.assertTrue(result.quantification.gaps)
        self.assertIsNone(result.damage_gain)
        self.assertIsNone(result.gain_percent)

    def test_unlocked_passive_without_event_keeps_complete_and_incomplete_apart(
        self,
    ) -> None:
        ordinary = _hit(
            "1:ordinary",
            1_000.0,
            character_id=1051,
            character_name="「零」",
            gameplay_effect_id="GE_ActorReaction_1_Damage",
        )
        baseline = _baseline(
            1075,
            "伊洛伊",
            enabled_team_passive_ids=("PASSIVE-1075-GA_Oneiroi_Passive_1",),
        )

        complete = BattleCreationPassiveCounterfactualService.calculate(
            _snapshot((ordinary,), baselines=(baseline,)),
        )[0]
        incomplete = BattleCreationPassiveCounterfactualService.calculate(
            _snapshot(
                (ordinary,),
                baselines=(baseline,),
                axis_complete=False,
            ),
        )[0]

        self.assertEqual("not_applicable", complete.quantification.status)
        self.assertEqual(0.0, complete.damage_gain)
        self.assertEqual((), complete.evidence_event_ids)
        self.assertEqual("unavailable", incomplete.quantification.status)
        self.assertIsNone(incomplete.damage_gain)
        self.assertIsNone(incomplete.quantified_damage_gain)
        self.assertTrue(incomplete.quantification.gaps)


class BattleCreationPassiveEvaluationServiceTests(unittest.TestCase):
    def test_nanally_p1_half_damage_policy_is_only_a_partial_approximation(
        self,
    ) -> None:
        first_flower = _hit(
            "1:flower",
            600.0,
            character_id=1051,
            character_name="「零」",
            gameplay_effect_id="GE_ActorReaction_1_Damage",
        )
        second_flower = _hit(
            "2:flower",
            400.0,
            character_id=1051,
            character_name="「零」",
            gameplay_effect_id="GE_ActorReaction_1_Damage",
        )
        replica_flower = _hit(
            "3:replica",
            200.0,
            character_id=1075,
            character_name="伊洛伊",
            gameplay_effect_id="GE_ActorReaction_1_1019_Damage",
        )
        ordinary = replace(
            _hit(
                "4:ordinary",
                500.0,
                character_id=1051,
                character_name="「零」",
            ),
            skill_name="普通攻击",
            damage_name="普通伤害",
            damage_component="direct",
            attack_type="skill",
            classification="direct",
        )

        (result,) = BattleCreationPassiveEvaluationService.calculate(
            _snapshot((first_flower, second_flower, replica_flower, ordinary)),
            {"characters": [_character(1010, "娜娜莉")]},
        )

        self.assertEqual("partial", result.quantification.status)
        self.assertEqual(
            "approximate_nanally_creation_count_halving",
            result.method,
        )
        self.assertEqual(600.0, result.quantified_damage_gain)
        self.assertEqual(1_100.0, result.without_quantified_effect_damage)
        self.assertIsNone(result.damage_gain)
        self.assertIsNone(result.without_buff_damage)
        self.assertEqual("低", result.confidence)
        self.assertEqual(
            ("1:flower", "2:flower", "3:replica"),
            result.evidence_event_ids,
        )
        self.assertEqual([1051, 1075], [row.character_id for row in result.beneficiaries])
        self.assertEqual(
            [500.0, 100.0],
            [row.quantified_damage_gain for row in result.beneficiaries],
        )
        self.assertEqual(0.0, result.quantified_unattributed_damage_gain)
        self.assertEqual(1_700.0, result.damage_coverage.basis_damage)
        self.assertEqual(1_200.0, result.damage_coverage.covered_damage)
        self.assertEqual(0.0, result.damage_coverage.unresolved_damage)
        self.assertEqual(500.0, result.quantification.proven_unchanged_damage)
        self.assertTrue(any(
            gap.code == "nanally_fire_interval_unmodeled"
            for gap in result.quantification.gaps
        ))

    def test_lifecycle_spatial_and_resource_passives_preserve_unknown_state(
        self,
    ) -> None:
        creation_hit = _hit(
            "1:creation",
            1_000.0,
            character_id=1051,
            character_name="「零」",
            gameplay_effect_id="GE_ActorReaction_1_Damage",
        )
        build = {"characters": [
            _character(1010, "娜娜莉"),
            _character(1019, "薄荷"),
            _character(1021, "埃德嘉"),
            _character(1052, "浔"),
            _character(1055, "九原"),
        ]}

        results = BattleCreationPassiveEvaluationService.calculate(
            _snapshot((creation_hit,)),
            build,
            evidence=BattleCreationPassiveEvidence(time_stop_axis_complete=True),
        )
        by_source = {row.source_character_id: row for row in results}

        self.assertEqual(
            {1010, 1019, 1021, 1052, 1055},
            set(by_source),
        )
        nanally = by_source[1010]
        self.assertEqual("partial", nanally.quantification.status)
        self.assertEqual(500.0, nanally.quantified_damage_gain)
        for character_id in (1019, 1021, 1055):
            row = by_source[character_id]
            self.assertEqual("unavailable", row.quantification.status)
            self.assertIsNone(row.quantified_damage_gain)
            self.assertTrue(row.quantification.gaps)
        self.assertEqual(
            100.0,
            by_source[1021].damage_coverage.unresolved_percent,
        )
        self.assertEqual(
            "not_applicable",
            by_source[1052].quantification.status,
        )
        self.assertEqual(
            0.0,
            by_source[1052].quantified_damage_gain,
        )

    def test_confirmed_single_target_makes_radius_not_applicable(self) -> None:
        analysis = _snapshot((
            _hit(
                "1:creation",
                1_000.0,
                character_id=1051,
                character_name="「零」",
                gameplay_effect_id="GE_ActorReaction_1_Damage",
            ),
        ))

        result = BattleCreationPassiveEvaluationService.calculate(
            analysis,
            {"characters": [_character(1019, "薄荷")]},
            evidence=BattleCreationPassiveEvidence(single_target_confirmed=True),
        )[0]

        self.assertEqual(1019, result.source_character_id)
        self.assertEqual("not_applicable", result.quantification.status)
        self.assertEqual(0.0, result.quantified_damage_gain)
        self.assertFalse(result.quantification.gaps)


if __name__ == "__main__":
    unittest.main()
