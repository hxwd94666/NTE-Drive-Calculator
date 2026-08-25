# 验证战报长页的时间轴展开和角色占比展示契约。
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtWidgets import (
    QApplication,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleTargetCondition,
    BattleRangeRoleSummary,
    BattleRangeSkillSummary,
)
from src.features.battle_report.analysis_view import BattleLongAnalysisView
from src.features.battle_report.marginal_page import BattleMarginalPage
from src.features.battle_report.page import BattleReportPage
from src.features.battle_report.build_snapshot_editor import (
    BattleBuildSnapshotEditorDialog,
)
from src.features.battle_report.role_contribution_view import (
    BattleRoleDamagePieWidget,
    BattleRoleShareBar,
    role_contribution_color,
)
from src.features.battle_report.target_vital_view import BattleTargetVitalPanel
from src.features.battle_report.target_condition_selector import (
    BattleTargetConditionSelector,
)
from src.services.battle_target_catalog_service import BattleTargetCatalogService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.features.battle_report.timeline_layout import (
    LABEL_WIDTH,
    TimelineLane,
    TimelinePaintedBar,
    TimelineSelection,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    ELAPSED_TIME_MODE,
)
from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox


class BattleReportAnalysisUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_unified_axis_expands_without_internal_vertical_scrollbar(self) -> None:
        view = BattleLongAnalysisView()

        self.assertEqual(
            Qt.ScrollBarAlwaysOff,
            view.timeline_scroll.verticalScrollBarPolicy(),
        )
        self.assertEqual(view.timeline.minimumHeight(), view.timeline.maximumHeight())
        self.assertFalse(hasattr(view, "action_timeline_scroll"))
        self.assertFalse(hasattr(view.timeline, "hit_selected"))
        self.assertFalse(hasattr(view.timeline, "action_selected"))
        self.assertFalse(hasattr(view.timeline, "damage_group_selected"))
        for combo in (
            view.time_mode_combo,
            view.zoom_combo,
        ):
            self.assertIsInstance(combo, NoWheelComboBox)
        self.assertEqual(ELAPSED_TIME_MODE, view.time_mode_combo.currentData())
        self.assertEqual(ELAPSED_TIME_MODE, view.timeline._time_mode)
        self.assertTrue(
            any(
                child.text() == "重置"
                for child in view.findChildren(QWidget)
                if hasattr(child, "text")
            )
        )

    def test_analysis_status_label_is_embedded_in_the_timeline_card(self) -> None:
        view = BattleLongAnalysisView()

        view.set_loading("正在读取轻量概览…")

        self.assertIsNotNone(view.capability_label.parentWidget())
        self.assertFalse(view.capability_label.isWindow())

    def test_environment_editor_refreshes_current_analysis_before_opening(self) -> None:
        view = BattleLongAnalysisView()
        calls = []
        view._render_targets = lambda: calls.append("render")
        view.target_vital_panel.open_environment_dialog = lambda: calls.append("open")

        view.environment_button.click()

        self.assertEqual(["render", "open"], calls)

    def test_half_scope_selector_stays_visible_with_long_analysis(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")

        self.assertFalse(hasattr(page, "detail_scope_card"))
        self.assertFalse(hasattr(page, "aggregate_character_card"))
        self.assertEqual(
            ("current", "first", "second"),
            tuple(page.long_analysis_view.scope_buttons),
        )
        self.assertEqual(
            {"buff", "skills", "hits", "targets", "marginal"},
            set(page.long_analysis_view.audit_buttons),
        )

    def test_lazy_detail_progress_is_pinned_below_the_page_stack(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")

        page.begin_analysis_details("buff")

        footer = page.analysis_progress
        self.assertFalse(footer.isHidden())
        self.assertIs(page, footer.parentWidget())
        self.assertEqual(1, page.layout().indexOf(footer))
        self.assertEqual(0, footer.progress.minimum())
        self.assertEqual(0, footer.progress.maximum())
        self.assertIn("Buff", footer.message_label.text())

        page.end_analysis_details()

        self.assertTrue(footer.isHidden())

    def test_full_analysis_corrects_top_summary_without_mutating_raw_summary(self) -> None:
        rendered: dict[str, str] = {}
        labels = {
            key: SimpleNamespace(
                setText=lambda value, name=key: rendered.__setitem__(name, value)
            )
            for key in ("damage", "dps", "duration")
        }
        raw_summary = SimpleNamespace(
            total_damage=5_082_783.86328125,
            duration_seconds=113.30294466018677,
        )
        page = SimpleNamespace(
            _latest_summary=raw_summary,
            metric_labels=labels,
            long_analysis_view=SimpleNamespace(
                set_analysis=lambda _analysis, selected_character_id=None: None
            ),
            marginal_page=SimpleNamespace(set_analysis=lambda _analysis: None),
        )
        analysis = SimpleNamespace(
            axis_complete=True,
            range_start_us=0,
            range_end_us=113_302_945,
            battle_end_us=113_302_945,
            timeline_damage_correction_total=57_600.0,
        )

        BattleReportPage.set_analysis(page, analysis)

        self.assertEqual(5_082_783.86328125, raw_summary.total_damage)
        self.assertEqual("5,025,184", rendered["damage"])
        self.assertEqual("44,352", rendered["dps"])
        self.assertEqual("113.3s（113.3s）", rendered["duration"])

    def test_feast_selector_applies_difficulty_options_and_witch_buff(self) -> None:
        with StaticGameDataDao("data/game_static.sqlite3") as dao:
            catalog = BattleTargetCatalogService.load(dao)
        selector = BattleTargetConditionSelector()
        selector.set_catalog(catalog)
        selector.environment_combo.setCurrentIndex(
            selector.environment_combo.findData("feast")
        )
        selector.feast_stage_combo.setCurrentIndex(
            selector.feast_stage_combo.count() - 1
        )
        selector.feast_difficulty_combo.setCurrentIndex(3)
        for combo in selector._feast_option_combos.values():
            combo.setCurrentIndex(combo.count() - 1)
        selector.witch_combo.setCurrentIndex(4)

        preset = selector.current_preset()

        self.assertEqual("DiyBossStage8", preset["environment_ref"])
        self.assertEqual(4, preset["difficulty_id"])
        self.assertEqual(1050.0, preset["enemy_defense_base"])
        self.assertEqual(0.5, preset["resistances"]["psyche"])
        self.assertEqual("DamageUpGeneralBase", preset["witch_buff_property_id"])

        restored = BattleTargetCondition(
            target_name=preset["target_name"],
            enemy_level=preset["enemy_level"],
            scene=preset["scene"],
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=tuple(sorted(preset["resistances"].items())),
            enemy_defense_base=preset["enemy_defense_base"],
            enemy_defense_up=preset["enemy_defense_up"],
            enemy_defense_add=preset["enemy_defense_add"],
            enemy_topple_limit=preset["enemy_topple_limit"],
            environment_kind=preset["environment_kind"],
            environment_ref=preset["environment_ref"],
            selected_target_ids=tuple(preset["selected_target_ids"]),
            primary_target_id=preset["primary_target_id"],
            difficulty_id=preset["difficulty_id"],
            feast_options=tuple(sorted(preset["feast_options"].items())),
            witch_buff_id=preset["witch_buff_id"],
            witch_buff_name_zh=preset["witch_buff_name_zh"],
            witch_buff_property_id=preset["witch_buff_property_id"],
            witch_buff_value=preset["witch_buff_value"],
            witch_buff_is_percent=preset["witch_buff_is_percent"],
        )
        restored_selector = BattleTargetConditionSelector()
        restored_selector.set_catalog(catalog)
        restored_selector.render(restored)
        restored_preset = restored_selector.current_preset()

        self.assertEqual(preset["feast_options"], restored_preset["feast_options"])
        self.assertEqual(0.5, restored_preset["resistances"]["psyche"])

        panel = BattleTargetVitalPanel()
        panel.set_catalog(catalog)
        panel.condition_selector.environment_combo.setCurrentIndex(
            panel.condition_selector.environment_combo.findData("feast")
        )
        panel.condition_selector.feast_stage_combo.setCurrentIndex(
            panel.condition_selector.feast_stage_combo.count() - 1
        )
        panel.condition_selector.feast_difficulty_combo.setCurrentIndex(3)
        panel.defense_reduction_spin.setValue(12.5)
        emitted = []
        panel.condition_save_requested.connect(emitted.append)
        panel.save_condition_button.click()

        self.assertEqual("feast", emitted[0]["environment_kind"])
        self.assertEqual(4, emitted[0]["difficulty_id"])
        self.assertEqual(0.125, emitted[0]["defense_reduction"])
        self.assertEqual(
            ["boss_05_BP_DiyBoss"],
            list(emitted[0]["selected_target_ids"]),
        )

    def test_clone_selector_uses_official_category_activity_and_spawn_members(self) -> None:
        with StaticGameDataDao("data/game_static.sqlite3") as dao:
            catalog = BattleTargetCatalogService.load(dao)
        selector = BattleTargetConditionSelector()
        selector.set_catalog(catalog)
        selector.environment_combo.setCurrentIndex(
            selector.environment_combo.findData("clone")
        )
        selector.clone_category_combo.setCurrentIndex(
            next(
                index
                for index in range(selector.clone_category_combo.count())
                if selector.clone_category_combo.itemText(index) == "异能升级材料"
            )
        )
        selector.clone_activity_combo.setCurrentIndex(
            next(
                index
                for index in range(selector.clone_activity_combo.count())
                if selector.clone_activity_combo.itemText(index) == "小心鸽子"
            )
        )

        preset = selector.current_preset()

        self.assertEqual("open_world", preset["environment_kind"])
        self.assertTrue(preset["environment_ref"].startswith("clone|"))
        self.assertIsNone(preset["difficulty_id"])
        self.assertTrue(preset["selected_target_ids"])
        self.assertTrue(preset["target_name"])

    def test_environment_entry_is_in_build_card_and_editor_is_modal(self) -> None:
        view = BattleLongAnalysisView()

        self.assertEqual("环境配置 · 未配置", view.build_edit_control.environment_button.text())
        self.assertTrue(view.target_vital_panel.environment_dialog.isModal())
        self.assertIs(
            view.target_vital_panel.condition_selector.parentWidget(),
            view.target_vital_panel.environment_dialog,
        )

    def test_build_editor_passes_role_page_scoring_context_to_reused_ui(self) -> None:
        scoring_engine = object()
        parent = QWidget()
        parent.scoring_engine = scoring_engine
        parent._shape_areas = {"ShapeA": 3}

        class FakeRoleEditor(QWidget):
            def __init__(
                self,
                _detail,
                parent=None,
                *,
                include_analysis=False,
                include_equipment=False,
                scoring_engine=None,
                shape_areas=None,
                **_kwargs,
            ) -> None:
                super().__init__(parent)
                self.include_analysis = include_analysis
                self.include_equipment = include_equipment
                self.scoring_engine = scoring_engine
                self.shape_areas = dict(shape_areas or {})

        with patch(
            "src.features.battle_report.build_snapshot_editor.OfficialRoleProfileEditor",
            FakeRoleEditor,
        ):
            dialog = BattleBuildSnapshotEditorDialog(
                {
                    "has_edit": False,
                    "details": [
                        {"character": {"character_id": 1004, "name_zh": "安魂曲"}}
                    ],
                },
                parent,
            )

        editor = dialog._editors[0]
        self.assertFalse(editor.include_analysis)
        self.assertTrue(editor.include_equipment)
        self.assertIs(scoring_engine, editor.scoring_engine)
        self.assertEqual({"ShapeA": 3}, editor.shape_areas)

    def test_clicking_hit_opens_formula_dialog_without_persistent_panel(self) -> None:
        view = BattleLongAnalysisView()
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
            timeline_buff_intervals=(),
        )

        view._render_timeline_selection_detail(
            TimelineSelection("hit", hit.event_id, hit)
        )

        self.assertFalse(hasattr(view, "timeline_detail"))
        dialog = view._hit_formula_dialog
        detail = dialog.detail.toPlainText()
        self.assertTrue(dialog.isVisible())
        self.assertIn("【伤害公式】", detail)
        self.assertIn("实际伤害：24.00", detail)
        self.assertIn("预计伤害期望：30.00", detail)
        self.assertIn("推断暴击：否", detail)
        dialog.hide()
        for spin in (view.start_spin, view.end_spin):
            self.assertIsInstance(spin, NoWheelDoubleSpinBox)
        self.assertEqual(
            (10, 25, 50, 100, 200, 300, 400, 500, 600, 700, 800),
            tuple(
                round(float(view.zoom_combo.itemData(index)) * 100)
                for index in range(view.zoom_combo.count())
            ),
        )
        self.assertEqual(1.0, view.zoom_combo.currentData())

        view.timeline.set_analysis(
            SimpleNamespace(
                battle_start_us=0,
                timeline_end_us=100_000_000,
                time_stop_intervals=(),
                timeline_hits=(),
                range_start_us=0,
                range_end_us=100_000_000,
                capability_level="hit_axis",
                axis_complete=True,
                formula_model_version="test-formula",
                inferred_inputs=(),
                inferred_actions=(),
                timeline_projection_version="test-timeline",
                total_damage=0.0,
                hits=(),
                roles=(),
            )
        )
        view._analysis = view.timeline._analysis
        self.assertEqual(3_488, view.timeline.minimumWidth())

        view.time_mode_combo.setCurrentIndex(
            view.time_mode_combo.findData(ACTIVE_TIME_MODE)
        )
        self.assertEqual(ACTIVE_TIME_MODE, view.timeline._time_mode)
        view.time_mode_combo.setCurrentIndex(
            view.time_mode_combo.findData(ELAPSED_TIME_MODE)
        )
        view.zoom_combo.setCurrentIndex(view.zoom_combo.findData(8.0))
        self.assertEqual(ELAPSED_TIME_MODE, view.timeline._time_mode)
        self.assertEqual(8.0, view.timeline._zoom_factor)
        self.assertEqual(26_588, view.timeline.minimumWidth())

        view.zoom_combo.setCurrentIndex(view.zoom_combo.findData(0.1))
        self.assertEqual(760, view.timeline.minimumWidth())

    def test_role_share_visuals_keep_exact_selected_range_percentage(self) -> None:
        role = BattleRangeRoleSummary(
            character_id=1072,
            character_name="灵可",
            hits=12,
            damage=625.0,
            dps=125.0,
            share_percent=62.5,
        )
        bar = BattleRoleShareBar(
            share_percent=role.share_percent,
            color=role_contribution_color(0),
        )
        pie = BattleRoleDamagePieWidget()
        pie.set_roles((role,))

        self.assertIn("62.50%", bar.toolTip())
        self.assertIn("灵可", pie.toolTip())
        self.assertIn("62.50%", pie.toolTip())

    def test_instant_action_is_clickable_across_its_complete_visible_bar(self) -> None:
        view = BattleLongAnalysisView()
        timeline = view.timeline
        timeline.resize(980, 300)
        timeline._analysis = SimpleNamespace(
            battle_start_us=0,
            battle_end_us=100_000_000,
            timeline_end_us=100_000_000,
            time_stop_intervals=(),
        )
        lane = TimelineLane(
            key="input:keyboard",
            label="推算键盘",
            kind="input",
            top=42,
            height=31,
        )
        rect = QRectF(300, 47, 52, 21)
        event_time = timeline._display_time_for_x(rect.left())
        timeline._lanes = (lane,)
        timeline._painted_hits = []
        timeline._painted_bars = [
            TimelinePaintedBar(
                kind="input",
                item_id="input:g",
                action_id="action:g",
                lane_key=lane.key,
                rect=rect,
                start_us=event_time,
                end_us=event_time + 1,
                payload=SimpleNamespace(start_us=event_time),
            )
        ]
        point = QPointF(rect.right() - 1, rect.center().y())

        self.assertGreater(
            timeline._display_time_for_x(point.x()) - event_time,
            75_000,
        )
        candidates = timeline._selection_candidates(point)

        self.assertEqual(("input:g",), tuple(item.item_id for item in candidates))

    def test_scrolled_lane_label_column_stays_outside_timeline_hit_testing(self) -> None:
        view = BattleLongAnalysisView()
        timeline = view.timeline
        lane = TimelineLane(
            key="input:keyboard",
            label="推算键盘",
            kind="input",
            top=42,
            height=31,
        )
        timeline._lanes = (lane,)
        timeline._painted_hits = []
        timeline._painted_bars = [
            TimelinePaintedBar(
                kind="input",
                item_id="input:g",
                action_id="action:g",
                lane_key=lane.key,
                rect=QRectF(300, 47, 52, 21),
                start_us=0,
                end_us=1,
                payload=SimpleNamespace(start_us=0),
            )
        ]

        timeline.set_horizontal_view_offset(240)

        self.assertTrue(timeline._is_in_sticky_label(240))
        self.assertTrue(timeline._is_in_sticky_label(240 + LABEL_WIDTH - 1))
        self.assertFalse(timeline._is_in_sticky_label(240 + LABEL_WIDTH))
        self.assertEqual([], timeline._selection_candidates(QPointF(310, 57)))

    def test_counterfactual_value_editors_ignore_wheel_and_use_percent_symbol(self) -> None:
        view = BattleMarginalPage()
        baseline = BattleCharacterBaseline(
            character_id=1001,
            character_name="测试角色",
            source="frozen_v25",
            stats=(
                BattleCharacterStat(
                    property_id="CritBase",
                    label="暴击率",
                    value=0.5,
                    is_percent=True,
                ),
            ),
        )
        view._analysis = SimpleNamespace(
            baselines=(baseline,),
            roles=(),
            build_counterfactual=None,
        )
        view.character_combo.addItem("测试角色", 1001)
        view._render_selected_role()

        editor = view.attribute_table.cellWidget(0, 2)
        self.assertIsInstance(editor, NoWheelDoubleSpinBox)
        self.assertEqual("%", editor.suffix())
        self.assertEqual("50.00%", view.attribute_table.item(0, 1).text())

    def test_marginal_page_lazily_builds_only_the_selected_role_editor(self) -> None:
        created: list[int] = []

        class FakeEditor(QWidget):
            def __init__(self, detail, *_args, **_kwargs) -> None:
                super().__init__()
                self.detail = detail
                created.append(int(detail["character"]["character_id"]))

            def profile(self):
                return dict(self.detail["profile"])

            def selected_equipment_context(self):
                return "battle", self.detail["equipment_contexts"]["battle"]

        def detail(character_id: int) -> dict:
            return {
                "character": {
                    "character_id": character_id,
                    "name_zh": f"角色{character_id}",
                },
                "profile": {
                    "character_id": character_id,
                    "character_level": 80,
                    "selected_awaken_effect_ids": [],
                    "skill_levels": {"melee": 10},
                },
                "selected_equipment_context_key": "battle",
                "equipment_contexts": {
                    "battle": {"items": [], "source_title": "本场原始"}
                },
            }

        with patch(
            "src.features.battle_report.marginal_page.OfficialRoleProfileEditor",
            FakeEditor,
        ):
            page = BattleMarginalPage()
            page.set_editor_data({"details": [detail(1001), detail(1002)]})
            self.assertEqual([1001], created)

            page.character_combo.setCurrentIndex(1)

        self.assertEqual([1001, 1002], created)

    def test_marginal_page_adjusted_timeline_has_no_internal_vertical_scroll(self) -> None:
        page = BattleMarginalPage()

        self.assertEqual(
            Qt.ScrollBarAlwaysOff,
            page.counterfactual_timeline_scroll.verticalScrollBarPolicy(),
        )
        self.assertIsInstance(page.timeline_time_mode_combo, NoWheelComboBox)
        self.assertIsInstance(page.timeline_zoom_combo, NoWheelComboBox)
        self.assertEqual(
            ELAPSED_TIME_MODE,
            page.timeline_time_mode_combo.currentData(),
        )
        self.assertEqual(ELAPSED_TIME_MODE, page.counterfactual_timeline._time_mode)

    def test_target_condition_editor_emits_final_percent_values(self) -> None:
        panel = BattleTargetVitalPanel()
        emitted = []
        panel.condition_save_requested.connect(emitted.append)
        panel.target_name_edit.setText("墨菲斯托")
        panel.enemy_level_spin.setValue(90)
        panel.enemy_defense_base_spin.setValue(1050)
        panel.resistance_spins["cosmos"].setValue(50)
        panel.resistance_spins["psyche"].setValue(50)
        panel.resistance_spins["lakshana"].setValue(50)

        panel.save_condition_button.click()

        self.assertEqual(1, len(emitted))
        self.assertEqual("墨菲斯托", emitted[0]["target_name"])
        self.assertEqual(1050.0, emitted[0]["enemy_defense_base"])
        self.assertEqual(0.5, emitted[0]["resistances"]["cosmos"])
        self.assertEqual(0.5, emitted[0]["resistances"]["psyche"])
        self.assertEqual(0.5, emitted[0]["resistances"]["lakshana"])
        self.assertIsInstance(panel.enemy_level_spin, NoWheelDoubleSpinBox)

    def test_damage_tables_put_damage_item_before_its_source_skill(self) -> None:
        view = BattleLongAnalysisView()
        hit = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1036,
            character_name="残虹",
            skill_name="普通攻击：燎原",
            damage_name="蚀心",
            damage_component="special",
            attack_type="Special Damage",
            damage_attribute="CHAOS",
            target_id="target",
            target_name="目标",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="special",
        )
        skill = BattleRangeSkillSummary(
            character_id=1036,
            character_name="残虹",
            skill_name="普通攻击：燎原",
            damage_name="蚀心",
            classification="special",
            hits=1,
            damage=100.0,
            share_percent=100.0,
        )
        view._analysis = SimpleNamespace(
            skills=(skill,),
            hits=(hit,),
            battle_start_us=0,
            time_stop_intervals=(),
        )

        view._render_skills()
        view._render_log()

        self.assertEqual("蚀心", view.skills_table.item(0, 1).text())
        self.assertEqual("普通攻击：燎原", view.skills_table.item(0, 2).text())
        self.assertEqual("蚀心 / 普通攻击：燎原", view.log_table.item(0, 3).text())

    def test_unknown_damage_falls_back_to_original_ability_name(self) -> None:
        view = BattleLongAnalysisView()
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

        view.next_button.click()
        self.app.processEvents()
        self.assertEqual(1, view._log_page)
        self.assertTrue(view.log_page_label.text().startswith("2 / 3"))
        self.assertEqual(
            min(expected_scroll, outer_scrollbar.maximum()),
            outer_scrollbar.value(),
        )

        view.next_button.click()
        self.assertEqual(2, view._log_page)
        self.assertTrue(view.log_page_label.text().startswith("3 / 3"))

        view.prev_button.click()
        self.assertEqual(1, view._log_page)
        outer.close()


if __name__ == "__main__":
    unittest.main()
