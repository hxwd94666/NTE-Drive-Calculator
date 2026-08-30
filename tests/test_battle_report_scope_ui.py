# 验证上下半切换后时间轴摘要、定位和 Buff 统计使用同一范围。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from src.domain.battle_counterfactual_quantification import BattleDamageQuantification
from src.domain.battle_buff_counterfactual import BattleDamageCoverage
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleInferredAction,
    BattleInferredInput,
    BattleTimelineDamageGroup,
)
from src.features.battle_report.analysis_scope_mixin import (
    BattleAnalysisScopeMixin,
)
from src.features.battle_report.analysis_view import BattleLongAnalysisView
from src.features.battle_report.buff_evidence_view import BattleBuffEvidencePanel
from src.services.battle_buff_counterfactual_plan_service import (
    battle_buff_counterfactual_key,
)


def _hit(event_id: str, time_us: int, damage: float) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=1,
        relative_time_us=time_us,
        character_id=1001,
        character_name="测试角色",
        skill_name="测试技能",
        damage_name="测试伤害",
        damage_component="skill",
        attack_type="skill",
        damage_attribute="nature",
        target_id="target",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
    )


class BattleReportScopeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_half_drives_input_action_and_coverage_summary(self) -> None:
        view = BattleLongAnalysisView()
        upper_hit = _hit("upper", 10_000_000, 100.0)
        lower_hit = _hit("lower", 80_000_000, 200.0)
        actions = (
            SimpleNamespace(action_id="upper-action", evidence_event_ids=("upper",)),
            SimpleNamespace(action_id="lower-action", evidence_event_ids=("lower",)),
        )
        view._analysis = SimpleNamespace(
            range_start_us=70_000_000,
            range_end_us=100_000_000,
            battle_start_us=0,
            battle_end_us=100_000_000,
            time_stop_intervals=(),
            capability_level="hit_axis",
            axis_complete=True,
            formula_model_version="formula-test",
            hit_replay_model_version="replay-test",
            timeline_projection_version="timeline-test",
            target_vital_model_version="vital-test",
            buff_inference_version="buff-test",
            buff_attribute_projection_version="projection-test",
            timeline_hits=(upper_hit, lower_hit),
            inferred_actions=actions,
            inferred_inputs=(
                SimpleNamespace(action_id="upper-action"),
                SimpleNamespace(action_id="lower-action"),
            ),
            hit_replays=(),
            effective_damage=200.0,
            total_damage=200.0,
            damage_correction_total=0.0,
            max_hp_reduction_damage=0.0,
            hits=(lower_hit,),
        )

        view._render_time_presentation()

        self.assertIn("输入投影 1 块 / 动作 1 段", view.capability_label.text())
        self.assertIn("当前时段推算输入 1 块 / 动作 1 段", view.action_summary_label.text())
        self.assertIn("引用出伤事件 1/1（100.0%）", view.action_summary_label.text())

    def test_timeline_itself_switches_to_selected_half(self) -> None:
        view = BattleLongAnalysisView()
        upper_hit = _hit("upper", 10_000_000, 100.0)
        lower_hit = _hit("lower", 80_000_000, 200.0)
        actions = (
            BattleInferredAction(
                "upper-action", 1001, "测试角色", "上半技能", "A", "A",
                9_000_000, 11_000_000, 1, 100.0, "中", "中", "测试",
                ("upper",), (),
            ),
            BattleInferredAction(
                "lower-action", 1001, "测试角色", "下半技能", "E", "E",
                79_000_000, 81_000_000, 1, 200.0, "中", "中", "测试",
                ("lower",), (),
            ),
        )
        inputs = tuple(
            BattleInferredInput(
                f"{name}-input", f"{name}-action", "keyboard", "E", 1001,
                "测试角色", start, start + 1, False, "中",
            )
            for name, start in (("upper", 9_000_000), ("lower", 79_000_000))
        )
        groups = tuple(
            BattleTimelineDamageGroup(
                f"{name}-group", 1001, "测试角色", "outgoing", "direct",
                "直伤", f"{name}伤害", f"{name}技能", "GA_Test", start,
                start + 1_000_000, 1, damage, (name,),
            )
            for name, start, damage in (
                ("upper", 10_000_000, 100.0),
                ("lower", 80_000_000, 200.0),
            )
        )
        analysis = BattleAnalysisSnapshot(
            battle_record_id=12,
            capability_level="hit_axis",
            axis_complete=True,
            formula_model_version="test",
            name_mapping_version="test",
            action_inference_version="test",
            timeline_projection_version="test",
            battle_start_us=0,
            battle_end_us=100_000_000,
            timeline_end_us=100_000_000,
            range_start_us=70_000_000,
            range_end_us=100_000_000,
            duration_seconds=30.0,
            total_damage=200.0,
            total_dps=200.0 / 30.0,
            timeline_hits=(upper_hit, lower_hit),
            inferred_actions=actions,
            inferred_inputs=inputs,
            timeline_damage_groups=groups,
            hits=(lower_hit,),
            roles=(),
            skills=(),
            targets=(),
            baselines=(),
        )

        view.timeline.set_analysis(analysis)
        visible = view.timeline._visible_analysis()

        self.assertEqual(("lower",), tuple(hit.event_id for hit in visible.timeline_hits))
        self.assertEqual(("lower-action",), tuple(row.action_id for row in visible.inferred_actions))
        self.assertEqual(("lower-input",), tuple(row.input_event_id for row in visible.inferred_inputs))
        self.assertEqual(("lower-group",), tuple(row.group_id for row in visible.timeline_damage_groups))
        self.assertEqual(70_000_000, view.timeline._display_origin_us())
        self.assertEqual(30_000_000, view.timeline._display_span_us())

    def test_buff_duration_is_clipped_to_selected_half(self) -> None:
        panel = BattleBuffEvidencePanel()
        interval = SimpleNamespace(
            source_character_id=1001,
            source_character_name="测试角色",
            buff_asset_path="/Game/Buff_Test",
            buff_name="跨半场 Buff",
            target_scope="self",
            trigger_event_type="技能命中",
            state_confidence="中",
            value_confidence="低",
            start_us=0,
            end_us=100_000_000,
            modifiers=(),
            inference_basis="静态推算",
        )

        panel.render(
            SimpleNamespace(
                buff_intervals=(interval,),
                range_start_us=50_000_000,
                range_end_us=75_000_000,
                hits=(_hit("lower", 60_000_000, 100.0),),
                buff_inference_version="buff-test",
            )
        )

        self.assertEqual("25.000s / 25.000s = 100.0%", panel.table.item(0, 5).text())
        self.assertTrue(panel.summary_label.isHidden())

    def test_buff_panel_shows_selected_range_removal_gain(self) -> None:
        panel = BattleBuffEvidencePanel()
        interval = SimpleNamespace(
            source_character_id=1001,
            source_character_name="测试角色",
            source_effect_definition_id="equipment:test:4",
            buff_asset_path="/Game/Buff_Test",
            buff_name="通用伤害提升15%",
            target_scope="team",
            trigger_event_type="进场",
            state_confidence="中",
            value_confidence="中",
            start_us=0,
            end_us=10_000_000,
            modifiers=(),
            inference_basis="静态推算",
        )
        result = SimpleNamespace(
            buff_key=battle_buff_counterfactual_key(interval),
            coverage_seconds=10.0,
            affected_hits=12,
            quantified_hits=10,
            baseline_damage=1_150.0,
            without_quantified_effect_damage=1_000.0,
            quantified_damage_gain=150.0,
            quantified_gain_percent=15.0,
            without_buff_damage=1_000.0,
            damage_gain=150.0,
            gain_percent=15.0,
            quantification=BattleDamageQuantification.from_buckets(
                status="complete",
                fully_quantified_damage=1_150.0,
                quantified_increment=150.0,
            ),
            confidence="中",
            method="observed_axis_remove_replay",
            explanation="按逐击移除重放。",
            damage_coverage=BattleDamageCoverage(1_150.0, 1_150.0),
        )

        panel.render(SimpleNamespace(
            buff_intervals=(interval,),
            buff_counterfactuals=(result,),
            range_start_us=0,
            range_end_us=10_000_000,
            hits=(_hit("hit", 1_000_000, 1_150.0),),
            buff_inference_version="buff-test",
        ))

        self.assertEqual("100.0%", panel.table.item(0, 7).text())
        self.assertEqual("+15.00%", panel.table.item(0, 8).text())
        self.assertEqual("查看", panel.table.item(0, 9).text())
        self.assertIn("完整伤害增量：+150.00", panel.table.item(0, 9).toolTip())

    def test_selected_half_focuses_the_full_timeline_viewport(self) -> None:
        scrollbar = SimpleNamespace(value=None)
        scrollbar.setValue = lambda value: setattr(scrollbar, "value", value)
        harness = BattleAnalysisScopeMixin()
        harness._analysis = SimpleNamespace(
            range_start_us=80_000_000,
            range_end_us=100_000_000,
        )
        harness._display_time_us = lambda value: value
        harness.timeline_scroll = SimpleNamespace(
            horizontalScrollBar=lambda: scrollbar,
            viewport=lambda: SimpleNamespace(width=lambda: 400),
        )
        harness.timeline = SimpleNamespace(
            widget_x_for_display_time=lambda value: value / 100_000.0,
        )

        harness._focus_selected_timeline_range()
        self.app.processEvents()

        self.assertEqual(700, scrollbar.value)


if __name__ == "__main__":
    unittest.main()
