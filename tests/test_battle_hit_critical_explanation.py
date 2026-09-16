# 验证逐击详情区分公式禁暴击、原生飘字标记和推断结果。
from dataclasses import replace
import unittest

from src.domain.battle_native_evidence import BattleHitFieldEvidence
from src.domain.battle_report import BattleAnalysisHit, BattleHitReplayResult
from src.services.battle_hit_critical_explanation import critical_explanation


class CriticalExplanationTests(unittest.TestCase):
    def test_topple_retains_native_flag_without_claiming_formula_crit(self):
        hit = BattleAnalysisHit(
            event_id="1:primary", sequence=1, relative_time_us=0, character_id=1072,
            character_name="灵可", skill_name="倾陷", damage_name="倾陷", damage_component="",
            attack_type="", damage_attribute="true", target_id="target", target_name="目标",
            damage=30425.0, direction="outgoing", is_follow_up=False, classification="special",
            field_evidence=BattleHitFieldEvidence("critical", "native_prediction", "中", "native_prediction", "中"),
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id, observed_damage=30425.0, non_critical_damage=28167.0,
            critical_damage=None, selected_damage=28167.0, selected_error_percent=7.42,
            critical_state="not_applicable", critical_policy="disabled", confidence="低", factors=(),
        )
        text = critical_explanation(hit, replay)
        self.assertIn("公式暴击：不适用", text)
        self.assertIn("飘字暴击标记：是", text)
        self.assertNotIn("推断暴击", text)
        direct = replace(replay, critical_policy="character", critical_state="critical")
        self.assertIn("字段置信度中", critical_explanation(hit, direct))
        self.assertIn("推断暴击", critical_explanation(replace(hit, field_evidence=None), direct))
