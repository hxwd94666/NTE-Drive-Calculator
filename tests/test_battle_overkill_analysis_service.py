# 验证 Core v3 超杀与服务器结算残余进入统一逐击分析的边界。
from __future__ import annotations

import unittest

from src.services.battle_counterfactual_analysis_service import (
    BattleCounterfactualAnalysisService,
)


def _hit(
    sequence: int,
    damage: float,
    *,
    overkill_damage: float | None,
    follow_up_damage: float = 0.0,
    character_id: int | None = 1072,
) -> dict:
    character_known = character_id is not None and character_id > 0
    row = {
        "sequence_text": str(sequence),
        "sequence_order": sequence,
        "relative_time_us": sequence * 100_000,
        "character_id": character_id,
        "character_known": character_known,
        "character_name": "灵可" if character_known else "未归因",
        "direction": "outgoing",
        "damage": damage,
        "follow_up_damage": follow_up_damage,
        "ability_name": "GA_Test",
        "damage_name": (
            "测试伤害" if character_known else "Server settlement residual"
        ),
        "damage_component": "skill",
        "attack_type": "skill",
        "follow_up_damage_name": "追加攻击",
        "follow_up_labels": [],
        "target_id": "monster-1",
        "target_name": "训练目标",
    }
    if overkill_damage is not None:
        row["overkill_damage"] = overkill_damage
    return row


class BattleOverkillAnalysisServiceTests(unittest.TestCase):
    def test_v3_subtracts_primary_overkill_once_and_keeps_residual_unattributed(
        self,
    ) -> None:
        result = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=1,
            evidence={
                "axis_complete": True,
                "hits": [
                    _hit(1, 120.0, overkill_damage=20.0, follow_up_damage=5.0),
                    _hit(2, 15.0, overkill_damage=0.0, character_id=0),
                ],
                "time_stop_intervals": [],
            },
            build=None,
            capability_level="hit_axis",
        )

        self.assertEqual(3, len(result.timeline_hits))
        self.assertEqual(120.0, result.total_damage)
        self.assertEqual(140.0, result.raw_total_damage)
        self.assertEqual(20.0, result.damage_correction_total)
        self.assertEqual(105.0, result.roles[0].damage)
        self.assertEqual(1072, result.roles[0].character_id)
        residual = next(
            hit for hit in result.timeline_hits
            if hit.damage_name == "Server settlement residual"
        )
        self.assertIsNone(residual.character_id)
        self.assertEqual(15.0, residual.damage)
        self.assertEqual(0.0, residual.overkill_damage)
        primary = next(hit for hit in result.timeline_hits if hit.event_id == "1:primary")
        self.assertEqual(100.0, primary.damage)
        self.assertEqual(120.0, primary.raw_damage)
        self.assertEqual("nte_core_overkill_v3", primary.damage_correction_kind)
        follow_up = next(
            hit for hit in result.timeline_hits if hit.event_id == "1:follow_up"
        )
        self.assertEqual(5.0, follow_up.damage)
        self.assertIsNone(follow_up.overkill_damage)

    def test_legacy_hit_without_overkill_keeps_original_damage(self) -> None:
        result = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=2,
            evidence={
                "axis_complete": True,
                "hits": [_hit(1, 120.0, overkill_damage=None)],
                "time_stop_intervals": [],
            },
            build=None,
            capability_level="hit_axis",
        )

        self.assertEqual(120.0, result.total_damage)
        self.assertEqual(120.0, result.raw_total_damage)
        self.assertEqual(0.0, result.damage_correction_total)
        self.assertIsNone(result.timeline_hits[0].overkill_damage)
