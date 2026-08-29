# 验证战报伤害构成的粗细切换与倾陷归属自动加载入口。
from __future__ import annotations

from types import SimpleNamespace
import unittest

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleRangeRoleSummary,
    DamageCompositionEntry,
)
from src.features.battle_report.analysis_view import BattleLongAnalysisView
from src.features.battle_report.composition_view import BattleDamageCompositionPanel


class BattleReportCompositionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_range_hides_empty_unknown_and_unattributed_blocks(self) -> None:
        view = BattleLongAnalysisView()
        role = BattleRangeRoleSummary(
            character_id=1036,
            character_name="残虹",
            hits=1,
            damage=100.0,
            dps=100.0,
            share_percent=100.0,
        )
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
            gameplay_effect_id="GE_Player_Zankou_DotDamage",
        )
        view._analysis = SimpleNamespace(
            roles=(role,),
            hits=(hit,),
            total_damage=100.0,
        )

        view._render_damage_composition()

        self.assertEqual(1, view.damage_composition_panel._grid.count())
        labels = self._composition_labels(view)
        self.assertIn("残虹", labels)
        self.assertIn("蚀心", labels)
        self.assertNotIn("未知角色", labels)
        self.assertNotIn("未归因", labels)

        view.composition_buttons["fine"].click()

        self.assertIn("普通攻击：燎原 · 蚀心", self._composition_labels(view))

    def test_team_topple_automatically_requests_role_attribution_after_render(self) -> None:
        view = BattleLongAnalysisView()
        requests: list[str] = []
        view.details_requested.connect(
            lambda kind, _payload: requests.append(kind)
        )
        role = BattleRangeRoleSummary(
            character_id=1004,
            character_name="安魂曲",
            hits=1,
            damage=100.0,
            dps=100.0,
            share_percent=100.0,
        )
        topple = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1,
            character_id=1004,
            character_name="安魂曲",
            skill_name="倾陷伤害",
            damage_name="倾陷伤害",
            damage_component="",
            attack_type="倾陷伤害",
            damage_attribute="CHAOS",
            target_id="target",
            target_name="目标",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="topple",
            gameplay_effect_id="Buff_Tenacity_damage",
        )
        view._analysis = SimpleNamespace(
            roles=(role,),
            hits=(topple,),
            hit_replays=(),
            baselines=(),
            max_hp_events=(),
            effective_damage=100.0,
        )

        view._render_damage_composition()
        self.app.processEvents()

        self.assertFalse(view.composition_topple_button.isHidden())
        self.assertIn("尚未加载", view.composition_status_label.text())
        self.assertEqual(["composition"], requests)
        view.composition_topple_button.click()
        self.assertEqual(["composition"], requests)

    def test_long_row_label_keeps_numeric_columns_inside_card(self) -> None:
        entry = DamageCompositionEntry(
            key="direct",
            label="普通攻击：未来自我连续性假设 · 未来自我连续性假设",
            damage=123_456.0,
            share_percent=12.3,
        )
        row = BattleDamageCompositionPanel._damage_row(entry)
        row.resize(420, 25)
        row.show()
        self.app.processEvents()

        labels = row.findChildren(QLabel)
        name = next(label for label in labels if label.toolTip() == entry.label)
        damage = next(label for label in labels if label.text() == "123,456")
        share = next(label for label in labels if label.text() == "12.3%")

        self.assertEqual(entry.label, name.text())
        self.assertGreater(
            name.fontMetrics().horizontalAdvance(name.text()),
            name.width(),
        )
        self.assertEqual(92, damage.width())
        self.assertEqual(52, share.width())
        self.assertLessEqual(share.geometry().right(), row.rect().right())

    @staticmethod
    def _composition_labels(view: BattleLongAnalysisView) -> tuple[str, ...]:
        return tuple(
            label.text()
            for label in view.damage_composition_panel.findChildren(QWidget)
            if hasattr(label, "text")
        )


if __name__ == "__main__":
    unittest.main()
