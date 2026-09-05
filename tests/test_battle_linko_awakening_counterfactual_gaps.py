# 验证灵可事件及真红凝视状态的觉醒差异不会被误报为完整零收益。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayResult,
    BattleRangeRoleSummary,
)
from src.services.battle_build_counterfactual_service import (
    BattleBuildCounterfactualService,
)
from src.services.battle_marginal_benefit_scope import (
    prepare_marginal_benefit_role_scope,
)
from src.services.battle_marginal_benefit_service import BattleMarginalBenefitService


def _baseline(
    *, effects: tuple[str, ...] = (), crit: float = 0.25,
    character_id: int = 1072,
):
    return BattleCharacterBaseline(
        character_id=character_id,
        character_name={1076: "真红", 1072: "灵可", 1003: "早雾"}[character_id],
        source="fixture",
        stats=(
            BattleCharacterStat("AtkBase", "基础攻击力", 100.0, False),
            BattleCharacterStat("AtkUp", "攻击力提升", 0.0, True),
            BattleCharacterStat("AtkAdd", "固定攻击力", 0.0, False),
            BattleCharacterStat("CritBase", "暴击率", crit, True),
            BattleCharacterStat("CritDamageBase", "暴击伤害", 1.0, True),
            BattleCharacterStat("DamageUpGeneralBase", "通伤", 0.0, True),
            BattleCharacterStat("DamageUpNatureBase", "灵伤", 0.0, True),
        ),
        selected_awaken_effect_ids=effects,
    )


def _snapshot(baseline: BattleCharacterBaseline) -> BattleAnalysisSnapshot:
    crit_rate = next(
        row.value for row in baseline.stats if row.property_id == "CritBase"
    )
    expected_damage = 100.0 * (1.0 + crit_rate)
    hit = BattleAnalysisHit(
        event_id="hit1",
        sequence=1,
        relative_time_us=1_000_000,
        character_id=baseline.character_id,
        character_name=baseline.character_name,
        skill_name="普通攻击",
        damage_name="第一段",
        damage_component="direct",
        attack_type="normal",
        damage_attribute="nature",
        target_id="target",
        target_name="目标",
        damage=125.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
    )
    replay = BattleHitReplayResult(
        event_id=hit.event_id,
        observed_damage=125.0,
        non_critical_damage=100.0,
        critical_damage=200.0,
        selected_damage=None,
        selected_error_percent=None,
        critical_state="ambiguous",
        confidence="高",
        factors=(),
        expected_damage=expected_damage,
        critical_rate=crit_rate,
        critical_policy="character",
        formula_damage_attribute="nature",
        formula_panel_character_id=baseline.character_id,
    )
    role = BattleRangeRoleSummary(
        character_id=baseline.character_id,
        character_name=baseline.character_name,
        hits=1,
        damage=125.0,
        dps=12.5,
        share_percent=100.0,
    )
    return BattleAnalysisSnapshot(
        battle_record_id=62,
        capability_level="formal_hit",
        axis_complete=True,
        formula_model_version="fixture",
        name_mapping_version="fixture",
        action_inference_version="fixture",
        timeline_projection_version="fixture",
        battle_start_us=0,
        battle_end_us=10_000_000,
        timeline_end_us=10_000_000,
        range_start_us=0,
        range_end_us=10_000_000,
        duration_seconds=10.0,
        total_damage=125.0,
        total_dps=12.5,
        timeline_hits=(hit,),
        inferred_actions=(),
        inferred_inputs=(),
        timeline_damage_groups=(),
        hits=(hit,),
        roles=(role,),
        skills=(),
        targets=(),
        baselines=(baseline,),
        effective_damage=125.0,
        effective_dps=12.5,
        hit_replays=(replay,),
    )


def _team_snapshot(*baselines: BattleCharacterBaseline) -> BattleAnalysisSnapshot:
    snapshots = tuple(_snapshot(baseline) for baseline in baselines)
    hits = tuple(
        replace(
            snapshot.hits[0],
            event_id=f"hit-{baseline.character_id}",
            sequence=index,
            relative_time_us=index * 1_000_000,
        )
        for index, (baseline, snapshot) in enumerate(
            zip(baselines, snapshots), start=1,
        )
    )
    total_damage = sum(snapshot.total_damage for snapshot in snapshots)
    return replace(
        snapshots[0],
        baselines=baselines,
        hits=hits,
        timeline_hits=hits,
        hit_replays=tuple(
            replace(snapshot.hit_replays[0], event_id=hit.event_id)
            for snapshot, hit in zip(snapshots, hits)
        ),
        roles=tuple(
            replace(
                snapshot.roles[0],
                share_percent=snapshot.total_damage / total_damage * 100.0,
            )
            for snapshot in snapshots
        ),
        total_damage=total_damage,
        effective_damage=total_damage,
        total_dps=total_damage / snapshots[0].duration_seconds,
        effective_dps=total_damage / snapshots[0].duration_seconds,
    )


class BattleLinkoAwakeningCounterfactualGapTests(unittest.TestCase):
    def test_ungenerated_awakenings_are_not_complete_zero_gain(self) -> None:
        original = _snapshot(_baseline())
        for effect_id in ("Effect1", "Effect3", "Effect4", "Effect5"):
            with self.subTest(effect_id=effect_id):
                candidate = _snapshot(_baseline(effects=(effect_id,)))
                result = BattleBuildCounterfactualService.compare(
                    original=original,
                    candidate=candidate,
                )

                self.assertEqual("partial", result.quantification.status)
                self.assertIsNone(result.candidate_damage)
                self.assertEqual(125.0, result.known_projection_damage)
                self.assertTrue(any(
                    effect_id.casefold() in gap.dimension_id.casefold()
                    for gap in result.quantification.gaps
                ))

    def test_effect_six_keeps_known_crit_gain_but_adds_resource_gap(self) -> None:
        original = _snapshot(_baseline())
        candidate = _snapshot(_baseline(effects=("Effect6",), crit=0.50))

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        self.assertEqual("partial", result.quantification.status)
        self.assertGreater(result.known_projection_damage or 0.0, 125.0)
        self.assertIsNone(result.candidate_damage)
        self.assertEqual("partial", result.roles[0].quantification.status)
        self.assertGreater(result.roles[0].known_projection_damage or 0.0, 125.0)
        self.assertIn(
            "linko_effect6_resource_restore_unquantified",
            {gap.code for gap in result.quantification.gaps},
        )


class BattleShinkuAwakeningCounterfactualGapTests(unittest.TestCase):
    def test_watch_growth_and_cap_changes_are_not_complete_zero_gain(self) -> None:
        for effect_id in ("Effect3", "Effect4"):
            with self.subTest(effect_id=effect_id):
                result = BattleBuildCounterfactualService.compare(
                    original=_snapshot(_baseline(character_id=1076)),
                    candidate=_snapshot(_baseline(
                        character_id=1076, effects=(effect_id,),
                    )),
                )
                self.assertEqual("partial", result.quantification.status)
                self.assertIsNone(result.candidate_damage)
                self.assertEqual("partial", result.roles[0].quantification.status)
                self.assertIsNone(result.roles[0].candidate_damage)
                self.assertTrue(any(
                    gap.dimension_id.startswith("shinku_awaken_")
                    for gap in result.roles[0].quantification.gaps
                ))

    def test_unchanged_watch_awakening_does_not_block_known_crit_gain(self) -> None:
        result = BattleBuildCounterfactualService.compare(
            original=_snapshot(_baseline(character_id=1076, effects=("Effect4",))),
            candidate=_snapshot(_baseline(
                character_id=1076, effects=("Effect4",), crit=0.50,
            )),
        )
        self.assertFalse(any(
            gap.dimension_id.startswith("shinku_awaken_")
            for gap in result.quantification.gaps
        ))
        self.assertGreater(result.known_projection_damage or 0.0, 125.0)


class BattleAwakeningRoleScopeTests(unittest.TestCase):
    def test_shinku_watch_changes_leave_other_role_results_unchanged(self) -> None:
        original = _team_snapshot(*(
            _baseline(character_id=character_id)
            for character_id in (1076, 1072, 1003)
        ))
        known_crit_candidate = _team_snapshot(*(
            _baseline(character_id=character_id, crit=0.50)
            for character_id in (1076, 1072, 1003)
        ))
        control = BattleBuildCounterfactualService.compare(
            original=original, candidate=known_crit_candidate,
        )
        control_roles = {row.character_id: row for row in control.roles}
        for effect_id in ("Effect3", "Effect4"):
            with self.subTest(effect_id=effect_id):
                candidate = _team_snapshot(
                    _baseline(character_id=1076, effects=(effect_id,), crit=0.50),
                    *known_crit_candidate.baselines[1:],
                )
                result = BattleBuildCounterfactualService.compare(
                    original=original, candidate=candidate,
                )
                roles = {row.character_id: row for row in result.roles}
                self.assertEqual("partial", result.quantification.status)
                self.assertEqual("partial", roles[1076].quantification.status)
                self.assertIsNone(roles[1076].candidate_damage)
                for character_id in (1072, 1003):
                    self.assertEqual(control_roles[character_id], roles[character_id])
                    self.assertEqual("complete", roles[character_id].quantification.status)
                    self.assertGreater(roles[character_id].candidate_damage or 0.0, 125.0)

    def test_mixed_awakening_gaps_remain_owned_by_their_roles(self) -> None:
        original, candidate = self._mixed_change_snapshots()
        result = BattleBuildCounterfactualService.compare(
            original=original, candidate=candidate,
        )
        roles = {row.character_id: row for row in result.roles}
        expected_dimensions = {
            1076: {"shinku_awaken_effect4_watch_cap"},
            1072: {"linko_awaken_effect6_resource_restore"},
            1003: set(),
        }
        self.assertEqual("partial", result.quantification.status)
        self.assertEqual(
            expected_dimensions[1076] | expected_dimensions[1072],
            {gap.dimension_id for gap in result.quantification.gaps},
        )
        for character_id, dimensions in expected_dimensions.items():
            with self.subTest(character_id=character_id):
                role = roles[character_id]
                self.assertEqual(
                    dimensions, {gap.dimension_id for gap in role.quantification.gaps},
                )
                self.assertEqual(
                    "partial" if dimensions else "complete", role.quantification.status,
                )
                self.assertGreater(role.known_projection_damage or 0.0, 125.0)
                if dimensions:
                    self.assertIsNone(role.candidate_damage)
                else:
                    self.assertAlmostEqual(150.0, role.candidate_damage or 0.0)

    def test_marginal_delta_does_not_copy_team_gaps_to_unrelated_role(self) -> None:
        original, candidate = self._mixed_change_snapshots()
        result = BattleBuildCounterfactualService.compare(
            original=original, candidate=candidate,
        )
        for character_id in (1076, 1072, 1003):
            with self.subTest(character_id=character_id):
                delta = BattleMarginalBenefitService._delta(
                    result, prepare_marginal_benefit_role_scope(original, character_id),
                )
                self.assertEqual("partial", delta.team_status)
                self.assertEqual(
                    "complete" if character_id == 1003 else "partial", delta.role_status,
                )
                self.assertAlmostEqual(125.0, delta.baseline_role_damage)
                if character_id == 1003:
                    self.assertAlmostEqual(150.0, delta.projected_role_damage or 0.0)
                    self.assertAlmostEqual(20.0, delta.role_gain_percent or 0.0)

    @staticmethod
    def _mixed_change_snapshots() -> tuple[BattleAnalysisSnapshot, BattleAnalysisSnapshot]:
        original = _team_snapshot(*(
            _baseline(character_id=character_id)
            for character_id in (1076, 1072, 1003)
        ))
        candidate = _team_snapshot(
            _baseline(character_id=1076, effects=("Effect4",), crit=0.50),
            _baseline(character_id=1072, effects=("Effect6",), crit=0.50),
            _baseline(character_id=1003, crit=0.50),
        )
        return original, candidate


if __name__ == "__main__":
    unittest.main()
