# 验证候选显示轴只在后台物化，原生解码和页面只传递结果。
from dataclasses import asdict, replace
import json
import unittest
from unittest.mock import patch

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from src.domain.battle_report.analysis.composition import (
    BattleDamageComposition, RoleDamageComposition, DamageCompositionEntry,
)
from src.domain.battle_report.counterfactual.quantification import BattleDamageQuantification
from src.features.battle_report.page import BattleReportPage
from src.features.battle_report.marginal.page import BattleMarginalPage
from src.integrations.battle_analysis.page_wire import decode_page
from src.integrations.nte_analysis_core import NativeAnalysisError
from src.services.battle_report.build.counterfactual import BattleBuildCounterfactualService
from src.services.battle_report.build.timeline_projection import BattleBuildTimelineProjectionService
from tests.battle_report.marginal.test_benefits import _snapshot


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
        with patch.object(BattleBuildTimelineProjectionService, 'project', side_effect=AssertionError('UI computation')):
            page.set_marginal_analysis(result.analysis, is_candidate=True,
                candidate_display_analysis=result.candidate_display_analysis)
        self.assertIs(page.marginal_page._analysis, result.analysis)
        self.assertIs(page.marginal_page._candidate_analysis, result.candidate_display_analysis)
        self.assertIs(page.marginal_page.counterfactual_timeline._analysis, result.candidate_display_analysis)
        self.assertIn('角色', tuple(label.text() for label in page.marginal_page.composition_panel.findChildren(QLabel)))
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

    def test_marginal_page_lazily_builds_only_the_selected_role_editor(self) -> None:
        created: list[int] = []

        class FakeEditor(QWidget):
            changed = Signal()

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
            "src.features.battle_report.marginal.page.OfficialRoleProfileEditor",
            FakeEditor,
        ):
            page = BattleMarginalPage()
            self.addCleanup(page.close)
            page.set_editor_data({"details": [detail(1001), detail(1002)]})
            self.assertEqual([1001], created)

            page.character_combo.setCurrentIndex(1)

        self.assertEqual([1001, 1002], created)

