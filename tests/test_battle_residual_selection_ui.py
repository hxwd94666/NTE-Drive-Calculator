# 验证候选冲突时仍展示残差选中的低置信范围。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from src.domain.battle_report import BattleTargetCondition
from src.domain.battle_target import BattleTargetInstanceResolution
from src.features.battle_report.analysis_view import BattleLongAnalysisView


class BattleResidualSelectionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_scope_stays_visible_with_red_conflict_warning(self) -> None:
        view = BattleLongAnalysisView()
        for name in (
            "_hide_hit_formula_dialog",
            "_hide_hit_buff_dialog",
            "_focus_selected_timeline_range",
            "_render_time_presentation",
            "_render_damage_composition",
            "_render_roles",
        ):
            setattr(view, name, lambda *args: None)
        view.timeline.set_analysis = lambda _analysis: None
        view.timeline.set_time_mode = lambda _mode: None
        condition = BattleTargetCondition(
            "争锋赏宴 · 愿望成真 · 极难 积分倍率×5 · 生命 +150%",
            77.0,
            "outer_realm",
            0.0,
            0.0,
            (),
            source_kind="inferred_encounter_hp_injective_default",
            environment_kind="feast",
        )
        flower_condition = BattleTargetCondition(
            "愿望之花",
            77.0,
            "outer_realm",
            0.0,
            0.0,
            (),
            source_kind="inferred_encounter_hp_injective_default",
            environment_kind="feast",
        )
        analysis = SimpleNamespace(
            battle_record_id=2,
            timeline_buff_intervals=(),
            target_condition=condition,
            target_identity_inference_ambiguous=True,
            target_identity_inference_confidence="低",
            target_identity_inference_basis=(
                "残差裁决模式 robust_fit；共同合格逐击 80 条，残差候选已裁决。"
            ),
            target_instance_resolutions=(
                BattleTargetInstanceResolution(
                    scope_half="",
                    captured_target_id="enemy-wire:flower",
                    resolved_monster_id="",
                    default_monster_id="Boss_Flower",
                    possible_monster_ids=("Boss_Flower",),
                    resolution_mode="ambiguous",
                    initial_max_hp=4_498_005.0,
                    target_condition=flower_condition,
                ),
            ),
            targets=(),
        )

        view.set_analysis(analysis)

        self.assertEqual(
            "候选冲突：争锋赏宴 · 愿望成真 · 愿望之花",
            view.current_scope_label.text(),
        )
        self.assertIn("#f85149", view.current_scope_label.styleSheet())


if __name__ == "__main__":
    unittest.main()
