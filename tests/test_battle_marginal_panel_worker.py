# 验证人物面板沿现有后台请求计算，保留取消、角色边界与原生整批投影。
from __future__ import annotations

from concurrent.futures import CancelledError
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.domain.battle_report import BattleAnalysisHit, BattleCharacterBaseline, BattleHitReplayResult
from src.features.battle_report.marginal_character_panel import render_character_panel_and_margins
from src.features.battle_report.marginal_page import BattleMarginalPage
from src.features.battle_report.page import BattleReportPage, BUFF_COUNTERFACTUAL_MODEL_VERSION, PASSIVE_COUNTERFACTUAL_MODEL_VERSION
from src.services.battle_buff_projection_memo import BattleBuffProjectionMemo
from src.services.battle_marginal_formula_scope import prepare_marginal_formula_scope
from src.services.battle_marginal_panel_service import BattleMarginalPanelResult, BattleMarginalPanelService
from src.services.battle_report_analysis_load_service import BattleReportAnalysisLoadRequest, BattleReportAnalysisLoadService


class _ProjectionBackend:
    def __init__(self):
        self.batches = []

    def project_batch(self, batch, *, checkpoint=None):
        self.batches.append(batch)
        return tuple({
            "event_id": batch["hits"][job["hit_index"]]["event_id"], "modifiers": [],
            "applied_interval_ids": [], "excluded_interval_ids": [], "exclusion_reasons": [],
            "confidence": "高", "decisions": [],
        } for job in batch["jobs"])


class BattleMarginalPanelWorkerTests(unittest.TestCase):
    def test_raw_and_formula_views_are_prepared_together(self):
        hit = BattleAnalysisHit(
            event_id="fixture-hit", sequence=1, relative_time_us=10, character_id=1,
            character_name="角色", skill_name="测试技能", damage_name="测试伤害", damage_component="skill",
            attack_type="normal", damage_attribute="nature", target_id="fixture-target", target_name="目标",
            damage=100.0, direction="outgoing", is_follow_up=False, classification="direct",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id, observed_damage=100.0, non_critical_damage=None, critical_damage=None,
            selected_damage=None, selected_error_percent=None, critical_state="unreplayable",
            confidence="未知", factors=(), formula_panel_character_id=2, formula_damage_attribute="cosmos",
        )
        analysis = SimpleNamespace(hits=(hit,), hit_replays=(replay,), buff_intervals=())
        backend = _ProjectionBackend()
        with patch("src.services.battle_marginal_formula_scope.BattleTargetInstanceMappingService.analysis_for_hit",
                   return_value=SimpleNamespace(target_condition=None)):
            scope = prepare_marginal_formula_scope(analysis, 2, projection_memo=BattleBuffProjectionMemo(backend))
        self.assertEqual(len(backend.batches), 1)
        self.assertEqual(len(backend.batches[0]["jobs"]), 2)
        self.assertEqual({row["character_id"] for row in backend.batches[0]["hits"]}, {1, 2})
        self.assertEqual(scope.role_hits, (hit,))
        self.assertEqual(scope.raw_projections[hit.event_id].event_id, hit.event_id)
        self.assertEqual(scope.formula_projections[hit.event_id].event_id, hit.event_id)

    def test_load_returns_panel_using_same_request_memo_and_frozen_units(self):
        history = Mock(native_page_loader=None)
        memo = BattleBuffProjectionMemo()
        history.new_projection_memo.return_value = memo
        analysis = SimpleNamespace(timeline_hits=(object(),))
        history.load_analysis.return_value = analysis
        history.load_target_catalog.return_value = {"kinds": ()}
        units = {"CritBase": 0.032}
        request = BattleReportAnalysisLoadRequest(
            battle_record_id=1, detail_level="marginal", selected_character_id=2,
            marginal_drive_units=tuple(units.items()),
        )
        units["CritBase"] = 0.5
        panel = BattleMarginalPanelResult(2, (), ("CritBase",))
        progress = Mock()
        with (patch.object(BattleReportAnalysisLoadService, "_materialize_marginal_baseline", return_value=analysis),
              patch.object(BattleMarginalPanelService, "calculate", return_value=panel) as calculate):
            result = BattleReportAnalysisLoadService.load_legacy_for_differential(history, request, progress_callback=progress)
        calculate.assert_called_once_with(analysis=analysis, character_id=2, drive_units={"CritBase": 0.032}, projection_memo=memo, progress_callback=progress)
        history.new_projection_memo.assert_called_once_with(progress_callback=progress)
        self.assertIs(result.marginal_panel, panel)
        self.assertFalse(hasattr(analysis, "marginal_panel"))

    def test_cancel_at_panel_boundary_prevents_calculation(self):
        history = Mock(native_page_loader=None)
        analysis = SimpleNamespace(timeline_hits=(object(),))
        history.load_analysis.return_value = analysis
        history.new_projection_memo.return_value = BattleBuffProjectionMemo()

        def cancel(progress):
            if progress.phase == "marginal_panel":
                raise CancelledError

        with (patch.object(BattleReportAnalysisLoadService, "_materialize_marginal_baseline", return_value=analysis),
              patch.object(BattleMarginalPanelService, "calculate") as calculate):
            with self.assertRaises(CancelledError):
                BattleReportAnalysisLoadService.load_legacy_for_differential(history, BattleReportAnalysisLoadRequest(
                    battle_record_id=1, detail_level="marginal", selected_character_id=2,
                    marginal_drive_units=(("CritBase", 0.032),),
                ), progress_callback=cancel)
        calculate.assert_not_called()
        history.load_target_catalog.assert_not_called()

    def test_ui_drops_another_roles_panel_without_synchronous_calculation(self):
        baseline = BattleCharacterBaseline(1, "角色", "fixture", ())
        analysis = SimpleNamespace(hits=(), hit_replays=())
        panel, table = Mock(), Mock()
        wrong_role = BattleMarginalPanelResult(2, (object(),), ("CritBase",))
        with (patch("src.features.battle_report.marginal_character_panel.render_attribute_results") as render,
              patch("src.services.battle_marginal_calculation_service.BattleMarginalCalculationService.calculate") as calculate):
            render_character_panel_and_margins(panel, table, analysis=analysis, baseline=baseline, marginal_panel=wrong_role)
        calculate.assert_not_called()
        panel.render.assert_called_once_with(baseline, (), current_element_property=None)
        render.assert_called_once_with(table, ())

    def test_ui_uses_returned_panel_and_filters_only_real_drive_properties(self):
        baseline = BattleCharacterBaseline(1, "角色", "fixture", ())
        analysis = SimpleNamespace(hits=(), hit_replays=())
        dynamic = SimpleNamespace(property_id="DefIgnore")
        drive = SimpleNamespace(property_id="CritBase")
        result = BattleMarginalPanelResult(1, (dynamic, drive), ("CritBase",))
        panel, table = Mock(), Mock()
        with patch("src.features.battle_report.marginal_character_panel.render_attribute_results") as render:
            render_character_panel_and_margins(panel, table, analysis=analysis, baseline=baseline, marginal_panel=result)
        panel.render.assert_called_once_with(baseline, result.results, current_element_property=None)
        render.assert_called_once_with(table, (drive,))

    def test_empty_drive_units_still_calculate_dynamic_panel_and_none_omits_it(self):
        history = Mock(native_page_loader=None)
        analysis = SimpleNamespace(timeline_hits=())
        history.load_analysis.return_value = analysis
        history.new_projection_memo.side_effect = lambda **_kwargs: BattleBuffProjectionMemo()
        with (patch.object(BattleReportAnalysisLoadService, "_materialize_marginal_baseline", return_value=analysis),
              patch("src.services.battle_marginal_calculation_service.BattleMarginalCalculationService.calculate", return_value=()) as calculate):
            result = BattleReportAnalysisLoadService.load_legacy_for_differential(history, BattleReportAnalysisLoadRequest(
                battle_record_id=1, detail_level="marginal", selected_character_id=2, marginal_drive_units=(),
            ))
            self.assertIsNotNone(result.marginal_panel)
            self.assertEqual(result.marginal_panel.drive_property_ids, ())
            units = calculate.call_args.kwargs["units"]
            self.assertIn("CritBase", units)
            self.assertIn("DefIgnore", units)
            self.assertTrue(all(unit == 0.0 for unit in units.values()))
            calculate.reset_mock()
            omitted = BattleReportAnalysisLoadService.load_legacy_for_differential(history, BattleReportAnalysisLoadRequest(
                battle_record_id=1, detail_level="marginal", selected_character_id=2,
            ))
            self.assertIsNone(omitted.marginal_panel)
            calculate.assert_not_called()

    def test_same_full_scope_role_switch_loads_missing_panel_despite_current_models(self):
        analysis = SimpleNamespace(
            buff_counterfactual_model_version=BUFF_COUNTERFACTUAL_MODEL_VERSION,
            passive_counterfactual_model_version=PASSIVE_COUNTERFACTUAL_MODEL_VERSION,
        )
        marginal = SimpleNamespace(
            _analysis=analysis, _marginal_panel=BattleMarginalPanelResult(1, (), ()),
            selected_character_id=lambda: 2, allows_automatic_recalculation=lambda: True,
        )
        marginal.has_current_panel = lambda: BattleMarginalPage.has_current_panel(marginal)
        page = SimpleNamespace(
            marginal_page=marginal, _stack=SimpleNamespace(currentWidget=lambda: marginal),
            _marginal_baseline_by_scope={}, _source_analysis=analysis,
            _marginal_result_scope=None, marginal_detail_scope=lambda: None,
            marginal_baseline_requested=Mock(),
        )
        page._request_marginal_lazy_recalculation = lambda **kwargs: BattleReportPage._request_marginal_lazy_recalculation(page, **kwargs)
        BattleReportPage._marginal_role_changed(page, None)
        page.marginal_baseline_requested.emit.assert_called_once_with()
        page.marginal_baseline_requested.reset_mock()
        marginal._marginal_panel = BattleMarginalPanelResult(2, (), ())
        BattleReportPage._marginal_role_changed(page, None)
        page.marginal_baseline_requested.emit.assert_not_called()
        marginal._analysis = None
        self.assertFalse(marginal.has_current_panel())

    def test_cancellation_inside_unit_or_hit_loop_never_returns_page_result(self):
        from tests.test_battle_marginal_calculation_service import (
            CHARACTER_ID, _analysis, _critical_replay, _hit,
        )
        for stop_at, count in ((1, 1), (64, 65)):
            with self.subTest(stop_at=stop_at):
                hits = tuple(_hit(event_id=f"fixture-{index}") for index in range(count))
                replays = tuple(_critical_replay(hit, "character", 0.5) for hit in hits)
                analysis = _analysis(hits[0], replays[0])
                analysis.hits, analysis.hit_replays = hits, replays
                analysis.timeline_hits = hits
                history = Mock(native_page_loader=None)
                history.load_analysis.return_value = analysis
                history.new_projection_memo.return_value = BattleBuffProjectionMemo()
                seen = []

                def cancel(progress):
                    if progress.phase == "marginal_panel_calculation":
                        seen.append(progress.completed)
                        if progress.completed == stop_at:
                            raise CancelledError

                with patch.object(BattleReportAnalysisLoadService, "_materialize_marginal_baseline", return_value=analysis):
                    with self.assertRaises(CancelledError):
                        BattleReportAnalysisLoadService.load_legacy_for_differential(history, BattleReportAnalysisLoadRequest(
                            battle_record_id=1, detail_level="marginal", selected_character_id=CHARACTER_ID,
                            marginal_drive_units=(("CritBase", 0.032), ("AtkUp", 0.04)),
                        ), progress_callback=cancel)
                self.assertEqual(seen[-1], stop_at)
                history.load_target_catalog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
