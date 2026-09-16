# 验证候选显示轴只在后台物化，原生解码和页面只传递结果。
from dataclasses import asdict, replace
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from src.domain.battle_report import BattleDamageComposition, RoleDamageComposition, DamageCompositionEntry
from src.domain.battle_counterfactual_quantification import BattleDamageQuantification
from src.features.battle_report.page import BattleReportPage
from src.features.battle_report.analysis_controller_mixin import BattleReportAnalysisControllerMixin
from src.integrations.native_battle_page_wire import decode_page
from src.integrations.nte_analysis_core import NativeAnalysisError
from src.services.battle_build_counterfactual_service import BattleBuildCounterfactualService
from src.services.battle_build_timeline_projection_service import BattleBuildTimelineProjectionService
from src.services.battle_report_analysis_load_service import (
    BattleReportAnalysisLoadRequest, BattleReportAnalysisLoadResult, BattleReportAnalysisLoadService,
)
from tests.test_battle_marginal_benefit_service import _snapshot


class CandidateDisplayAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def fixture(self):
        source = _snapshot()
        comparison = BattleBuildCounterfactualService.compare(original=source, candidate=source)
        analysis = replace(source, build_counterfactual=comparison)
        display = BattleBuildTimelineProjectionService.project(analysis, comparison)
        return analysis, display

    def test_worker_projects_once_and_keeps_original_analysis(self):
        analysis, display = self.fixture()
        history = Mock(native_page_loader=None)
        history.load_analysis.return_value = analysis
        with patch.object(BattleBuildTimelineProjectionService, 'project', return_value=display) as project:
            result = BattleReportAnalysisLoadService.load_legacy_for_differential(
                history, BattleReportAnalysisLoadRequest(7, detail_level='hit'),
            )
        project.assert_called_once_with(analysis, analysis.build_counterfactual)
        self.assertIs(result.analysis, analysis)
        self.assertIs(result.candidate_display_analysis, display)

    def test_baseline_has_no_second_projection(self):
        history = Mock(native_page_loader=None)
        history.load_analysis.return_value = _snapshot()
        with patch.object(BattleBuildTimelineProjectionService, 'project') as project:
            result = BattleReportAnalysisLoadService.load_legacy_for_differential(history, BattleReportAnalysisLoadRequest(7))
        project.assert_not_called()
        self.assertIsNone(result.candidate_display_analysis)

    def test_wire_requires_and_preserves_separate_display(self):
        analysis, display = self.fixture()
        wire = json.loads(json.dumps(dict(
            analysis=asdict(analysis), candidate_display_analysis=asdict(display),
            target_catalog=None, target_catalog_error=None, marginal_benefits=None,
            marginal_panel=None, derived_snapshot=None,
        )))
        result = decode_page(wire)
        self.assertEqual(wire["analysis"], json.loads(json.dumps(asdict(result.analysis))))
        self.assertEqual(wire["candidate_display_analysis"],
                         json.loads(json.dumps(asdict(result.candidate_display_analysis))))
        del wire['candidate_display_analysis']
        with self.assertRaises(NativeAnalysisError):
            decode_page(wire)

    def test_empty_native_page_preserves_null_display(self):
        result = decode_page(dict(
            analysis=None, candidate_display_analysis=None, target_catalog=None,
            target_catalog_error=None, marginal_benefits=None, marginal_panel=None,
        ))
        self.assertIsNone(result.analysis)
        self.assertIsNone(result.candidate_display_analysis)

    def composition_wire(self):
        analysis, display = self.fixture()
        entry = DamageCompositionEntry('direct', '直伤', 100.0, 100.0, 100.0)
        composition = BattleDamageComposition(
            roles=(RoleDamageComposition(1003, '角色', 100.0, (entry,), 100.0),),
            other_total_damage=0.0, other_share_percent=0.0, other_entries=(),
        )
        comparison = replace(analysis.build_counterfactual, composition=composition,
            quantification=BattleDamageQuantification('complete', 100.0, 100.0, 0.0, 0.0, 0.0, 0.0))
        analysis = replace(analysis, build_counterfactual=comparison)
        display = replace(display, build_counterfactual=comparison)
        return json.loads(json.dumps(dict(
            analysis=asdict(analysis), candidate_display_analysis=asdict(display),
            target_catalog=None, target_catalog_error=None, marginal_benefits=None, marginal_panel=None,
        )))

    def test_decoded_candidate_composition_can_render_in_real_page(self):
        result = decode_page(self.composition_wire())
        page = BattleReportPage(game_ui_asset_root='data/game_ui')
        self.addCleanup(page.deleteLater)
        page.set_marginal_analysis(result.analysis, is_candidate=True,
            candidate_display_analysis=result.candidate_display_analysis)
        self.assertEqual(page.marginal_page.composition_panel._grid.count(), 1)
        for analysis in (result.analysis, result.candidate_display_analysis):
            composition = analysis.build_counterfactual.composition
            self.assertIsInstance(composition, BattleDamageComposition)
            self.assertIsInstance(composition.roles[0], RoleDamageComposition)
            self.assertIsInstance(composition.roles[0].entries[0], DamageCompositionEntry)

    def test_invalid_nested_composition_is_rejected_before_ui(self):
        for change in ('missing_roles', 'boolean_damage'):
            wire = self.composition_wire()
            composition = wire['analysis']['build_counterfactual']['composition']
            if change == 'missing_roles':
                del composition['roles']
            else:
                composition['roles'][0]['entries'][0]['damage'] = True
            with self.subTest(change=change), self.assertRaises(NativeAnalysisError):
                decode_page(wire)

    def test_controller_forwards_worker_display_unchanged(self):
        analysis = replace(_snapshot(), timeline_hits=(object(),))
        display = _snapshot()
        request = SimpleNamespace(
            load=BattleReportAnalysisLoadRequest(7, detail_level='marginal'),
            completion_kind=None, persist_full_range=False,
        )
        host = SimpleNamespace(
            _desired_analysis_load_token=4,
            _analysis_request_is_current=lambda value: value is request,
            _page=Mock(), _build_snapshot_controller=Mock(),
        )
        result = BattleReportAnalysisLoadResult(analysis, None, candidate_display_analysis=display)
        BattleReportAnalysisControllerMixin._analysis_load_ready(host, 4, request, result)
        self.assertIs(display, host._page.set_marginal_analysis.call_args.kwargs['candidate_display_analysis'])

    def test_page_renders_supplied_axis_without_calling_projection(self):
        analysis, display = self.fixture()
        page = BattleReportPage(game_ui_asset_root='data/game_ui')
        self.addCleanup(page.deleteLater)
        with (
            patch.object(BattleBuildTimelineProjectionService, 'project', side_effect=AssertionError('UI computation')),
            patch.object(page.marginal_page.counterfactual_timeline, 'set_analysis') as timeline,
            patch.object(page.marginal_page, '_render_selected_role'),
        ):
            page.set_marginal_analysis(analysis, is_candidate=True, candidate_display_analysis=display)
        timeline.assert_called_once_with(display)
        self.assertIs(page.marginal_page._analysis, analysis)
        self.assertIs(page.marginal_page._candidate_analysis, display)


if __name__ == '__main__':
    unittest.main()
