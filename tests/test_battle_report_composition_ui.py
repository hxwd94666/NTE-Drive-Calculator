# 验证战报伤害构成的粗细切换与倾陷归属懒加载入口。
from __future__ import annotations

from types import SimpleNamespace
import unittest

from PySide6.QtWidgets import QApplication, QWidget

from src.domain.battle_report import BattleAnalysisHit, BattleRangeRoleSummary
from src.features.battle_report.analysis_view import BattleLongAnalysisView


class BattleReportCompositionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_range_switches_between_coarse_and_fine_channels(self) -> None:
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

        self.assertEqual(2, view.damage_composition_panel._grid.count())
        labels = self._composition_labels(view)
        self.assertIn("残虹", labels)
        self.assertIn("持续伤害", labels)
        self.assertIn("未归因", labels)

        view.composition_buttons["fine"].click()

        self.assertIn("普通攻击：燎原 · 蚀心", self._composition_labels(view))

    def test_team_topple_prompts_for_lazy_role_attribution(self) -> None:
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

        self.assertFalse(view.composition_topple_button.isHidden())
        self.assertIn("尚未加载", view.composition_status_label.text())
        view.composition_topple_button.click()
        self.assertEqual(["composition"], requests)

    @staticmethod
    def _composition_labels(view: BattleLongAnalysisView) -> tuple[str, ...]:
        return tuple(
            label.text()
            for label in view.damage_composition_panel.findChildren(QWidget)
            if hasattr(label, "text")
        )


if __name__ == "__main__":
    unittest.main()
