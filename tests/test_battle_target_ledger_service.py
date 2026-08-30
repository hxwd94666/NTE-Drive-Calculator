# 验证目标账本按半场隔离，并显式闭合观测耗血与未解释差额。
import unittest

from src.services.battle_counterfactual_analysis_service import (
    BattleCounterfactualAnalysisService,
)


def _hit(
    sequence: int,
    *,
    half: str,
    name: str,
    damage: float,
    hp_before: float,
    hp_after: float,
) -> dict[str, object]:
    return {
        "sequence_order": sequence,
        "relative_time_us": sequence * 1_000_000,
        "character_id": 1001,
        "character_name": "测试角色",
        "direction": "outgoing",
        "damage": damage,
        "abyss_half": half,
        "target_id": "enemy-wire:reused",
        "target_name": name,
        "target_hp_before": hp_before,
        "target_hp_after": hp_after,
        "target_max_hp": hp_before,
    }


class BattleTargetLedgerServiceTests(unittest.TestCase):
    def test_reused_wire_id_is_scoped_by_abyss_half(self) -> None:
        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=1,
            evidence={
                "axis_complete": True,
                "hits": [
                    _hit(
                        1,
                        half="upper",
                        name="上半目标",
                        damage=110.0,
                        hp_before=100.0,
                        hp_after=0.0,
                    ),
                    _hit(
                        2,
                        half="lower",
                        name="下半目标",
                        damage=80.0,
                        hp_before=120.0,
                        hp_after=40.0,
                    ),
                ],
            },
            build={"characters": []},
            capability_level="hit_axis",
        )

        targets = {target.scope_half: target for target in analysis.targets}
        self.assertEqual({"upper", "lower"}, set(targets))
        self.assertEqual("上半目标", targets["upper"].target_name)
        self.assertEqual("下半目标", targets["lower"].target_name)
        self.assertEqual(-10.0, targets["upper"].unexplained_hp_delta)
        self.assertEqual(0.0, targets["lower"].unexplained_hp_delta)
        for target in targets.values():
            self.assertEqual(
                target.observed_hp_loss,
                target.effective_damage + target.unexplained_hp_delta,
            )

    def test_overlapping_hp_intervals_are_diagnostic_only(self) -> None:
        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=2,
            evidence={
                "axis_complete": True,
                "hits": [
                    _hit(
                        1,
                        half="upper",
                        name="并发目标",
                        damage=60.0,
                        hp_before=100.0,
                        hp_after=40.0,
                    ),
                    _hit(
                        2,
                        half="upper",
                        name="并发目标",
                        damage=80.0,
                        hp_before=100.0,
                        hp_after=20.0,
                    ),
                ],
            },
            build={"characters": []},
            capability_level="hit_axis",
        )

        self.assertEqual(140.0, analysis.raw_total_damage)
        self.assertEqual(140.0, analysis.total_damage)
        self.assertEqual(60.0, analysis.damage_overlap_correction_total)
        second = max(analysis.hits, key=lambda hit: hit.sequence)
        self.assertEqual(80.0, second.damage)
        self.assertEqual(
            "single_target_hp_interval_overlap_diagnostic",
            second.damage_correction_kind,
        )


if __name__ == "__main__":
    unittest.main()
