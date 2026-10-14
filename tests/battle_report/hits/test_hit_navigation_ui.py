# 验证缓存逐击事实、未知技能标识与日志分页的内容连续性。
import unittest
from types import SimpleNamespace
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget
from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.replay.models import BattleHitReplayFactor, BattleHitReplayResult
from src.domain.battle_report.analysis.composition import BattleRangeSkillSummary
from src.features.battle_report.analysis.view import BattleLongAnalysisView
from src.features.battle_report.timeline.layout import TimelineSelection


class BattleHitNavigationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cached_hit_selection_displays_frozen_measured_and_expected_damage(self) -> None:
        view = BattleLongAnalysisView()
        self.addCleanup(view.close)
        hit = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1004,
            character_name="安魂曲",
            skill_name="测试技能",
            damage_name="测试伤害",
            damage_component="skill",
            attack_type="Skill",
            damage_attribute="COSMOS",
            target_id="boss",
            target_name="墨菲斯托",
            damage=24.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=24.0,
            non_critical_damage=24.0,
            critical_damage=36.0,
            selected_damage=24.0,
            selected_error_percent=0.0,
            critical_state="non_critical",
            confidence="高",
            factors=(
                BattleHitReplayFactor("skill", "技能倍率", 0.5, "静态倍率"),
                BattleHitReplayFactor("scaling", "Atk 面板", 100.0, "冻结面板"),
                BattleHitReplayFactor("damage_up", "增伤区", 1.2, "增伤"),
                BattleHitReplayFactor("defense", "防御区", 0.5, "防御"),
                BattleHitReplayFactor("resistance", "抗性区", 0.8, "抗性"),
                BattleHitReplayFactor("vulnerability", "易伤区", 1.0, "易伤"),
                BattleHitReplayFactor("independent", "独立最终乘区", 1.0, "独立"),
                BattleHitReplayFactor("critical", "暴击伤害倍率", 1.5, "爆伤"),
            ),
            formula_type="直伤",
            critical_rate=0.5,
            expected_damage=30.0,
            corrected_expected_damage=30.0,
            signed_error_percent=0.0,
        )
        view._analysis = SimpleNamespace(
            hit_replays=(replay,),
            hit_replay_model_version="cached-test-v1",
            timeline_buff_intervals=(),
            baselines=(),
            target_instance_resolutions=(),
        )

        view.timeline.selection_activated.emit(
            TimelineSelection("hit", hit.event_id, hit)
        )

        dialog = view._hit_formula_dialog
        detail = dialog.detail.toPlainText()
        self.assertTrue(dialog.isVisible())
        self.assertIn("【伤害公式】", detail)
        self.assertIn("实际伤害：24.00", detail)
        self.assertIn("预计伤害期望：30.00", detail)
        self.assertIn("推断暴击：否", detail)
        dialog.hide()

    def test_unknown_damage_falls_back_to_original_ability_name(self) -> None:
        view = BattleLongAnalysisView()
        self.addCleanup(view.close)
        hit = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1001,
            character_name="测试角色",
            skill_name="未知技能",
            damage_name="未知伤害",
            damage_component="direct",
            attack_type="E技能",
            damage_attribute="NATURE",
            target_id="target",
            target_name="目标",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
            ability_id="GA_Test_Skill",
        )
        skill = BattleRangeSkillSummary(
            character_id=1001,
            character_name="测试角色",
            skill_name="未知技能",
            damage_name="未知伤害",
            classification="direct",
            hits=1,
            damage=100.0,
            share_percent=100.0,
            ability_id="GA_Test_Skill",
        )
        view._analysis = SimpleNamespace(
            skills=(skill,),
            hits=(hit,),
            battle_start_us=0,
            time_stop_intervals=(),
        )

        view._render_skills()
        view._render_log()

        self.assertEqual("GA_Test_Skill", view.skills_table.item(0, 1).text())
        self.assertEqual("GA_Test_Skill", view.log_table.item(0, 3).text())

    def test_log_page_buttons_change_page_and_keep_outer_scroll_position(self) -> None:
        outer = QScrollArea()
        self.addCleanup(outer.close)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        view = BattleLongAnalysisView()
        content_layout.addWidget(view)
        filler = QWidget()
        filler.setFixedHeight(800)
        content_layout.addWidget(filler)
        outer.setWidgetResizable(True)
        outer.setWidget(content)
        outer.resize(640, 360)
        outer.show()
        view._log_page_size = 2
        view._analysis = SimpleNamespace(
            hits=tuple(
                BattleAnalysisHit(
                    event_id=f"{index}:primary",
                    sequence=index,
                    relative_time_us=index * 1_000,
                    character_id=1001,
                    character_name="测试角色",
                    skill_name="测试技能",
                    damage_name="测试伤害",
                    damage_component="skill",
                    attack_type="普攻",
                    damage_attribute="CHAOS",
                    target_id="target",
                    target_name="目标",
                    damage=100.0,
                    direction="outgoing",
                    is_follow_up=False,
                    classification="direct",
                )
                for index in range(5)
            )
        )
        view._render_log()
        self.app.processEvents()
        outer_scrollbar = outer.verticalScrollBar()
        outer_scrollbar.setValue(min(240, outer_scrollbar.maximum()))
        expected_scroll = outer_scrollbar.value()
        self.assertGreater(expected_scroll, 0)
        self.assertFalse(view.prev_button.isEnabled())
        self.assertTrue(view.next_button.isEnabled())

        view.next_button.click()
        self.app.processEvents()
        self.assertEqual("2:primary", view.log_table.item(0, 9).data(Qt.ItemDataRole.UserRole))
        self.assertEqual(
            min(expected_scroll, outer_scrollbar.maximum()),
            outer_scrollbar.value(),
        )

        view.next_button.click()
        self.assertEqual("4:primary", view.log_table.item(0, 9).data(Qt.ItemDataRole.UserRole))
        self.assertTrue(view.prev_button.isEnabled())
        self.assertFalse(view.next_button.isEnabled())

        view.prev_button.click()
        self.assertEqual("2:primary", view.log_table.item(0, 9).data(Qt.ItemDataRole.UserRole))
        # 单页与多页共用同一公共翻页边界；无需为三主题重复运行。
        view._analysis = SimpleNamespace(hits=view._analysis.hits[:1])
        view._render_log()
        self.assertFalse(view.prev_button.isEnabled())
        self.assertFalse(view.next_button.isEnabled())
        outer.close()
