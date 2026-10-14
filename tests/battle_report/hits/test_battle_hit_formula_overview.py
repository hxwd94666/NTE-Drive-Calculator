# 验证逐击公式关系、原始因子点击及窄屏滚动，不重算战报。
import unittest
from dataclasses import replace
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QScrollArea

from src.domain.battle_report.replay.models import BattleHitReplayFactor
from src.features.battle_report.hits.formula_overview import formula_layout, target_portrait_identity
from src.features.battle_report.hits.inspection_dialog import HitInspectionDialog
from tests.battle_report.buffs.test_hit_explanation import _hit


def factor(key, value=1.0):
    return BattleHitReplayFactor(key, key, value, "已有证据", formula="核心提供的公式")


def replay(factors):
    return SimpleNamespace(factors=tuple(factors), critical_state="non_critical",
                           selected_damage=1000, observed_damage=1000, formula_type="直伤",
                           formula_panel_character_id=None, signed_error_percent=0)


class FormulaRelationsTests(unittest.TestCase):
    def test_portrait_uses_packet_target_mapping_and_keeps_native_priority(self):
        hit = _hit()
        row = SimpleNamespace(scope_half=hit.scope_half, captured_target_id=hit.target_id,
                              resolved_monster_id="", default_monster_id="boss_18_BP_DiyBoss")
        self.assertEqual(target_portrait_identity(hit, {}, [row]),
                         ("boss_18_BP_DiyBoss", "核心首选推断的目标画像"))
        self.assertEqual(target_portrait_identity(hit, {"targetMonsterId":"boss_05"}, [row]),
                         ("boss_05", "DLL 逐击怪物标识"))

        # 半场与采集实例共同定位；无匹配时不借用别的目标补画像。
        other = SimpleNamespace(scope_half=hit.scope_half, captured_target_id="other",
                                resolved_monster_id="boss_05", default_monster_id="")
        other_half = SimpleNamespace(scope_half="second", captured_target_id=hit.target_id,
                                     resolved_monster_id="boss_18", default_monster_id="")
        self.assertEqual(target_portrait_identity(hit, {}, [other, other_half])[0], "")
    def test_weave_diagnostics_are_not_multiplied_again(self):
        source, middle, final = factor("recorded_direct_damage"), factor("weave_copy"), factor("weave_followup")
        primary, extras, operator = formula_layout(replay([source, middle, final]))
        self.assertEqual((primary, extras, operator), ((source, final), (middle,), "×"))

    def test_topple_sums_contributions_without_target_input(self):
        target, a, b = factor("topple_target"), factor("topple_character:1004"), factor("topple_character:1036")
        self.assertEqual(formula_layout(replay([target, a, b])), ((a, b), (target,), "+"))

    def test_unrecognized_relationship_is_not_fabricated(self):
        item = factor("new_formula_input")
        self.assertEqual(formula_layout(replay([item])), ((item,), (), ""))


class FormulaOverviewInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_click_preserves_factor_identity_and_new_hit_replaces_tiles(self):
        dialog = HitInspectionDialog()
        view = dialog.inspection.overview
        item = factor("critical", 2.14)
        received = []
        view.factor_requested.connect(received.append)
        try:
            dialog.inspection.set_hit(_hit(), replay([item]))
            dialog.show()
            QTest.qWait(1)
            tile = view.findChild(QPushButton, "hitFactorTile")
            # 未暴击的采用值为 1，原始候选与点击对象保持核心冻结身份。
            self.assertIn("：1，", tile.accessibleName())
            self.assertEqual(item.value, 2.14)
            tile.click()
            self.assertIs(received[0], item)
            detail = next(child for child in dialog.findChildren(QDialog) if child.isVisible())
            dialog.inspection.set_hit(_hit())
            self.assertFalse(detail.isVisible())
            QTest.qWait(1)
            self.assertFalse(any(button.isVisible() for button in view.findChildren(QPushButton, "hitFactorTile")))
            self.assertEqual(view.calculated.text(), "—")
        finally:
            dialog.close()

    def test_hit_identity_and_assist_skill_are_visible_separately_from_formula_type(self):
        dialog = HitInspectionDialog()
        hit = replace(_hit(), event_id="242:primary", sequence=242,
                      ability_id="GA_Radio072_QTE_BackToLTE", skill_name="援护技：同频合击",
                      damage_name="同频合击", relative_time_us=31_123_000)
        try:
            dialog.inspection.set_hit(hit, replay([factor("skill")]))
            view = dialog.inspection.overview
            self.assertEqual(view.hit_identity.text(), "Hit #242 · 31.123 s")
            self.assertIn("242:primary", view.hit_identity.toolTip())
            self.assertEqual(view.skill_name.text(), "援护技：同频合击")
            self.assertIn("直伤公式", view.skill_meta.text())
        finally:
            dialog.close()

    def test_narrow_window_scrolls_without_overlapping_tiles_or_footer(self):
        dialog = HitInspectionDialog()
        view = dialog.inspection.overview
        keys = ("skill", "scaling", "damage_up", "defense", "resistance", "vulnerability", "independent", "critical")
        try:
            dialog.inspection.set_hit(_hit(), replay([factor(k) for k in keys]))
            dialog.resize(620, 550)
            dialog.show()
            QTest.qWait(80)
            scroll = dialog.findChild(QScrollArea)
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
            tiles = view.findChildren(QPushButton, "hitFactorTile")
            self.assertEqual({f"{key}：1，查看详情" for key in keys}, {tile.accessibleName() for tile in tiles})
            # 不固定列数或坐标：所有冻结因子都可滚到、点击中心不被其它控件遮挡。
            for tile in tiles:
                scroll.ensureWidgetVisible(tile)
                QTest.qWait(1)
                center = tile.rect().center()
                self.assertTrue(scroll.viewport().rect().contains(tile.mapTo(scroll.viewport(), center)))
                self.assertIs(self.app.widgetAt(tile.mapToGlobal(center)), tile)
            view.attacker.setFocus()
            footer = dialog.inspection.buff_button
            for _ in range(30):
                if self.app.focusWidget() is footer:
                    break
                QTest.keyClick(self.app.focusWidget() or dialog, Qt.Key.Key_Tab)
                QTest.qWait(1)
            self.assertIs(self.app.focusWidget(), footer)
            self.assertTrue(scroll.viewport().rect().contains(footer.mapTo(scroll.viewport(), footer.rect().center())))
            QTest.keyClick(footer, Qt.Key.Key_Space)
            self.assertTrue(any(child.isVisible() and child.windowTitle() == "本击 Buff 贡献"
                                for child in dialog.findChildren(QDialog)))
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
