# 验证严格候选使用未校正逐击预测选出可直接消费的敌方画像。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.domain.battle_encounter import (
    BattleEncounterCandidate,
    BattleEncounterCandidateMatch,
    BattleEncounterTargetPreset,
)
from src.domain.battle_target import BattleTargetInstanceResolution
from src.services.battle_encounter_fit_projection_service import (
    BattleEncounterFitProjectionService,
)
from src.services.battle_inferred_target_condition_service import (
    BattleInferredEncounter,
    BattleInferredTargetConditionService,
)


def _candidate(ref: str, resistance: float) -> BattleEncounterCandidate:
    target = BattleEncounterTargetPreset(
        target_id=f"target-{ref}",
        target_name=ref,
        monster_class_path="",
        monster_count=1,
        max_hp=4_498_005.0,
        monster_level=77.0,
        profile_set="profile",
        pack_id=ref,
        defense_base=1062.0,
        defense_up=0.0,
        defense_add=0.0,
        topple_limit=70.0,
        resistances=(("incantation", resistance),),
    )
    return BattleEncounterCandidate(
        environment_kind="feast",
        environment_ref=ref,
        environment_name=ref,
        scope_half="",
        outer_realm_floor=None,
        difficulty_id=4,
        feast_options=(),
        targets=(target,),
    )


def _inferred(*candidates: BattleEncounterCandidate) -> BattleInferredEncounter:
    default = candidates[0]
    matches = tuple(
        BattleEncounterCandidateMatch(candidate, ((0,),), 0, 0)
        for candidate in candidates
    )
    return BattleInferredEncounter(
        environment_kind=default.environment_kind,
        environment_ref=default.environment_ref,
        environment_name=default.environment_name,
        source_kind="inferred_encounter_hp_injective_default",
        confidence="低",
        inference_basis="同血量严格候选",
        scope_half="",
        outer_realm_floor=None,
        difficulty_id=4,
        feast_options=(),
        targets=default.targets,
        identities=(),
        target_condition=None,
        ambiguous=True,
        formula_matches=matches,
        formula_profile_conflict=True,
    )


def _analysis(predicted: float, *, target_id: str = "enemy-wire:test"):
    hit = SimpleNamespace(
        event_id="hit-1",
        raw_damage=100.0,
        character_id=1036,
        gameplay_effect_id="GE_test",
        damage_name="测试伤害",
        ability_id="GA_test",
        skill_name="测试技能",
        damage_attribute="incantation",
        scope_half="",
        target_id=target_id,
    )
    replay = SimpleNamespace(
        event_id="hit-1",
        observed_damage=100.0,
        non_critical_damage=predicted,
        critical_damage=None,
        expected_damage=predicted,
        corrected_expected_damage=100.0,
    )
    return SimpleNamespace(hits=(hit,), hit_replays=(replay,))


def _analysis_with_conflicted_pair(good_prediction: float, bad_prediction: float):
    def hit(
        event_id: str,
        sequence: int,
        at_us: int,
        effect_id: str,
        damage: float,
        hp_before: float,
        hp_after: float,
    ):
        return SimpleNamespace(
            event_id=event_id,
            sequence=sequence,
            relative_time_us=at_us,
            raw_damage=damage,
            damage=damage,
            direction="outgoing",
            character_id=1036,
            gameplay_effect_id=effect_id,
            damage_name="测试伤害",
            ability_id="GA_test",
            skill_name="测试技能",
            damage_attribute="incantation",
            scope_half="",
            target_id="enemy-wire:test",
            target_hp_before=hp_before,
            target_hp_after=hp_after,
        )

    hits = (
        hit("conflict-a", 1, 100_000, "GE_A", 500.0, 2_000.0, 1_500.0),
        hit("conflict-b", 2, 200_000, "GE_B", 500.0, 2_000.0, 1_500.0),
        hit("good", 3, 1_000_000, "GE_Good", 100.0, 1_500.0, 1_400.0),
    )
    replays = tuple(
        SimpleNamespace(
            event_id=row.event_id,
            observed_damage=row.damage,
            non_critical_damage=(
                good_prediction if row.event_id == "good" else bad_prediction
            ),
            critical_damage=None,
            expected_damage=(
                good_prediction if row.event_id == "good" else bad_prediction
            ),
            factors=(),
        )
        for row in hits
    )
    return SimpleNamespace(hits=hits, hit_replays=replays, buff_intervals=())


class BattleEncounterFitProjectionServiceTests(unittest.TestCase):
    def test_raw_residual_winner_becomes_formula_condition(self) -> None:
        stage3 = _candidate("DiyBossStage3", 0.16)
        stage4 = _candidate("DiyBossStage4", 0.50)
        analyses = {
            stage3.environment_ref: _analysis(98.0),
            stage4.environment_ref: _analysis(60.0),
        }

        outcome = BattleEncounterFitProjectionService.select(
            _inferred(stage3, stage4),
            project_candidate=lambda row: analyses[row.environment_ref],
        )

        assert outcome is not None
        self.assertEqual("DiyBossStage3", outcome.selection.winner_ref)
        self.assertEqual("robust_fit", outcome.selection.selection_mode)
        self.assertEqual("低", outcome.selection.confidence)
        self.assertIsNotNone(outcome.inferred.target_condition)
        self.assertEqual("DiyBossStage3", outcome.inferred.environment_ref)
        self.assertIn(
            "corrected_expected_damage 未进入评分",
            outcome.inferred.inference_basis,
        )
        condition = outcome.inferred.target_condition
        assert condition is not None
        resolutions = BattleInferredTargetConditionService.apply_residual_resolution_metadata(
            outcome.inferred,
            (
                BattleTargetInstanceResolution(
                    scope_half="",
                    captured_target_id="enemy-wire:test",
                    resolved_monster_id="target-DiyBossStage3",
                    default_monster_id="target-DiyBossStage3",
                    possible_monster_ids=("target-DiyBossStage3",),
                    resolution_mode="unique",
                    initial_max_hp=4_498_005.0,
                    target_condition=condition,
                ),
            ),
        )
        self.assertEqual("", resolutions[0].resolved_monster_id)
        self.assertEqual("target-DiyBossStage3", resolutions[0].default_monster_id)
        self.assertEqual(
            ("target-DiyBossStage3", "target-DiyBossStage4"),
            resolutions[0].possible_monster_ids,
        )
        self.assertEqual("ambiguous", resolutions[0].resolution_mode)

    def test_equal_residual_keeps_hard_default_instead_of_no_result(self) -> None:
        stage4 = _candidate("DiyBossStage4", 0.50)
        stage3 = _candidate("DiyBossStage3", 0.16)

        outcome = BattleEncounterFitProjectionService.select(
            _inferred(stage4, stage3),
            project_candidate=lambda _row: _analysis(90.0),
        )

        assert outcome is not None
        self.assertEqual("DiyBossStage4", outcome.selection.winner_ref)
        self.assertEqual("ambiguous_default", outcome.selection.selection_mode)
        self.assertEqual("低", outcome.selection.confidence)
        self.assertIsNotNone(outcome.inferred.target_condition)
        self.assertIn("残差完全一致", outcome.selection.audit_summary)

    def test_candidate_target_binding_does_not_change_shared_group_key(self) -> None:
        stage3 = _candidate("DiyBossStage3", 0.16)
        stage4 = _candidate("DiyBossStage4", 0.50)
        analyses = {
            stage3.environment_ref: _analysis(
                98.0,
                target_id="target-DiyBossStage3",
            ),
            stage4.environment_ref: _analysis(
                60.0,
                target_id="target-DiyBossStage4",
            ),
        }

        outcome = BattleEncounterFitProjectionService.select(
            _inferred(stage3, stage4),
            project_candidate=lambda row: analyses[row.environment_ref],
            group_analysis=_analysis(1.0, target_id="unknown"),
        )

        assert outcome is not None
        self.assertEqual("DiyBossStage3", outcome.selection.winner_ref)
        self.assertEqual(
            {1},
            {row.used_hit_count for row in outcome.selection.scores},
        )

    def test_damage_attribution_conflicts_are_excluded_from_every_candidate(self) -> None:
        stage3 = _candidate("DiyBossStage3", 0.16)
        stage4 = _candidate("DiyBossStage4", 0.50)
        analyses = {
            stage3.environment_ref: _analysis_with_conflicted_pair(98.0, 20.0),
            stage4.environment_ref: _analysis_with_conflicted_pair(60.0, 500.0),
        }

        outcome = BattleEncounterFitProjectionService.select(
            _inferred(stage3, stage4),
            project_candidate=lambda row: analyses[row.environment_ref],
        )

        assert outcome is not None
        self.assertEqual("DiyBossStage3", outcome.selection.winner_ref)
        self.assertEqual(
            {1},
            {row.used_hit_count for row in outcome.selection.scores},
        )


if __name__ == "__main__":
    unittest.main()
