# 验证战报按需加载不假装完成、倾陷归属请求不重复派发。
from __future__ import annotations

from types import SimpleNamespace
import unittest

from PySide6.QtWidgets import QApplication

from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.analysis.composition import BattleRangeRoleSummary
from src.features.battle_report.analysis.view import BattleLongAnalysisView


class BattleReportCompositionUiTests(unittest.TestCase):
    def test_topple_only_results_do_not_claim_normal_hit_details_are_loaded(self) -> None:
        view = BattleLongAnalysisView()
        self.addCleanup(view.close)
        requests = []
        view.details_requested.connect(lambda kind, payload: requests.append((kind, payload)))
        view._analysis = SimpleNamespace(hit_replays=(object(),), hit_replay_model_version="")
        selection = SimpleNamespace(kind="hit")
        view._timeline_selection_activated(selection)
        view._request_detailed_analysis("hit")
        self.assertEqual([("hit", selection), ("hit", None)], requests)

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_team_topple_automatically_requests_role_attribution_after_render(self) -> None:
        view = BattleLongAnalysisView()
        self.addCleanup(view.close)
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

if __name__ == "__main__":
    unittest.main()
