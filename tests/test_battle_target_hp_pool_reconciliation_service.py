# 验证共享生命池别名只在完整且唯一的数值闭环中消除服务器残差。
from __future__ import annotations

import unittest

from src.services.battle_counterfactual_analysis_service import (
    BattleCounterfactualAnalysisService,
)
from src.services.battle_target_hp_pool_reconciliation_service import (
    BattleTargetHpPoolReconciliationService,
)


def _hit(
    sequence: int,
    damage: float,
    *,
    target_id: str,
    hp_before: float,
    hp_after: float,
    character_id: int | None = 1075,
    damage_name: str = "测试伤害",
) -> dict:
    known = character_id is not None and character_id > 0
    return {
        "sequence_order": sequence,
        "sequence_text": str(sequence),
        "relative_time_us": sequence * 100_000,
        "character_id": character_id,
        "character_known": known,
        "character_name": "伊洛伊" if known else "未归因",
        "direction": "outgoing",
        "damage": damage,
        "overkill_damage": 0.0,
        "follow_up_damage": 0.0,
        "ability_name": "GA_Test",
        "damage_name": damage_name,
        "damage_component": "skill",
        "attack_type": "skill",
        "target_id": target_id,
        "target_name": "训练目标",
        "target_hp_before": hp_before,
        "target_hp_after": hp_after,
        "target_max_hp": 100.0,
    }


def _closed_rows(*, alias_damage: float = 30.0) -> list[dict]:
    return [
        _hit(1, 60.0, target_id="main", hp_before=100.0, hp_after=40.0),
        _hit(
            2,
            alias_damage,
            target_id="alias",
            hp_before=40.0,
            hp_after=10.0,
        ),
        _hit(3, 10.0, target_id="main", hp_before=40.0, hp_after=0.0),
        _hit(4, 10.0, target_id="main", hp_before=0.0, hp_after=0.0),
        _hit(
            5,
            20.0,
            target_id="main",
            hp_before=20.0,
            hp_after=0.0,
            character_id=None,
            damage_name="Server settlement residual",
        ),
    ]


class BattleTargetHpPoolReconciliationServiceTests(unittest.TestCase):
    def test_unique_complete_closure_merges_alias_and_suppresses_residual(
        self,
    ) -> None:
        result = BattleTargetHpPoolReconciliationService.reconcile(
            _closed_rows(),
            axis_complete=True,
        )

        self.assertTrue(result.applied)
        self.assertEqual("main", result.primary_target_id)
        self.assertEqual("alias", result.alias_target_id)
        self.assertEqual((1075,), result.attributed_character_ids)
        self.assertEqual(30.0, result.alias_damage)
        self.assertEqual(20.0, result.residual_damage)
        self.assertEqual(10.0, result.overlap_correction)
        self.assertEqual(4, len(result.rows))
        self.assertEqual({"main"}, {row["target_id"] for row in result.rows})
        corrected = next(row for row in result.rows if row["sequence_order"] == 4)
        self.assertEqual(10.0, corrected["_calc_damage_overlap_correction"])

    def test_analysis_has_no_unattributed_residual_after_exact_closure(self) -> None:
        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=36,
            evidence={
                "axis_complete": True,
                "contract_version": 4,
                "hits": _closed_rows(),
                "time_stop_intervals": [],
            },
            build=None,
            capability_level="hit_axis",
            infer_buffs=False,
        )

        self.assertEqual(100.0, analysis.total_damage)
        self.assertEqual(110.0, analysis.raw_total_damage)
        self.assertEqual(10.0, analysis.damage_overlap_correction_total)
        self.assertEqual(1, len(analysis.roles))
        self.assertEqual(1075, analysis.roles[0].character_id)
        self.assertEqual(100.0, analysis.roles[0].damage)
        self.assertFalse(
            any(
                hit.damage_name == "Server settlement residual"
                for hit in analysis.timeline_hits
            )
        )

    def test_incomplete_axis_keeps_original_residual(self) -> None:
        rows = _closed_rows()
        result = BattleTargetHpPoolReconciliationService.reconcile(
            rows,
            axis_complete=False,
        )

        self.assertFalse(result.applied)
        self.assertEqual(tuple(rows), result.rows)

    def test_amount_mismatch_fails_closed(self) -> None:
        rows = _closed_rows(alias_damage=29.0)
        result = BattleTargetHpPoolReconciliationService.reconcile(
            rows,
            axis_complete=True,
        )

        self.assertFalse(result.applied)
        self.assertEqual(tuple(rows), result.rows)


if __name__ == "__main__":
    unittest.main()
