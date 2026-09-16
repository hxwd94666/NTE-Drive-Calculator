# 验证逐击公式关系、原始因子点击及窄屏滚动，不重算战报。
import unittest
from dataclasses import replace
from types import SimpleNamespace

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from src.domain.battle_report import BattleHitReplayFactor
from src.features.battle_report.hit_formula_overview import formula_layout, factor_value, target_portrait_identity
from src.features.battle_report.hit_inspection_dialog import HitInspectionDialog
from src.services.skill_name_rendering_service import battle_hit_skill_label
from tests.test_battle_hit_buff_detail import _hit


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

    def test_portrait_never_borrows_another_target_or_half(self):
        hit = _hit()
        other = SimpleNamespace(scope_half=hit.scope_half, captured_target_id="other",
                                resolved_monster_id="boss_05", default_monster_id="")
        other_half = SimpleNamespace(scope_half="second", captured_target_id=hit.target_id,
                                     resolved_monster_id="boss_18", default_monster_id="")
        self.assertEqual(target_portrait_identity(hit, {}, [other, other_half])[0], "")
    def test_skill_label_keeps_category_without_duplicate_phase(self):
        self.assertEqual(battle_hit_skill_label("同频合击", "援护技：同频合击"), "援护技：同频合击")
        self.assertEqual(battle_hit_skill_label("落月", "普通攻击：落月"), "普通攻击：落月")
        self.assertEqual(battle_hit_skill_label("覆纹追加攻击", "覆纹追加攻击"), "覆纹追加攻击")
        self.assertEqual(battle_hit_skill_label("独立伤害项", "技能来源"), "技能来源 · 独立伤害项")

    def test_noncritical_does_not_display_candidate_critical_multiplier(self):
        item = factor("critical", 2.14)
        self.assertEqual(factor_value(item, "non_critical"), "1")
        self.assertEqual(factor_value(item, "critical"), "2.14")
        self.assertEqual(item.value, 2.14)

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
        item = factor("skill", 0.801)
        received = []
        view.factor_requested.connect(received.append)
        try:
            dialog.inspection.set_hit(_hit(), replay([item]))
            view._tiles[0].click()
            self.assertIs(received[0], item)
            self.assertTrue(dialog.inspection._popup.isVisible())
            dialog.inspection.set_hit(_hit())
            self.assertEqual(view.factor_grid.count(), 0)
            self.assertIsNone(dialog.inspection._popup)
            self.assertEqual(view.calculated.text(), "—")
            self.assertFalse(any("关联反事实" in b.text() for b in dialog.findChildren(QPushButton)))
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
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
            for i, tile in enumerate(view._tiles):
                for other in view._tiles[i + 1:]:
                    self.assertFalse(tile.geometry().intersects(other.geometry()))
            bottom = max(t.mapTo(dialog.inspection, QPoint(0, t.height())).y() for t in view._tiles)
            self.assertGreater(dialog.inspection.buff_button.y(), bottom)
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            QTest.qWait(20)
            self.assertTrue(scroll.viewport().rect().contains(
                dialog.inspection.buff_button.mapTo(scroll.viewport(), QPoint(1, 1))))
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
