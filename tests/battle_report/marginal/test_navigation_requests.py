# 验证边际页退出、编辑器重建和重复半场点击不会提交多余分析请求。
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QWidget

from src.features.battle_report.page import BattleReportPage


class _Editor(QWidget):
    changed = Signal()

    def __init__(self, detail, *_args, **_kwargs):
        super().__init__()
        self.detail = detail

    def profile(self):
        return dict(self.detail["profile"])

    def selected_equipment_context(self):
        return "battle", {"items": []}


def _editor_data():
    return {"details": [
        {"character": {"character_id": character_id, "name_zh": f"角色{character_id}"},
         "profile": {"character_id": character_id}, "analysis_detail_scope": scope}
        for character_id, scope in ((1001, "first"), (1002, "second"))
    ]}


class MarginalNavigationRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        editor = patch("src.features.battle_report.marginal_page.OfficialRoleProfileEditor", _Editor)
        editor.start()
        self.addCleanup(editor.stop)
        self.page = BattleReportPage(game_ui_asset_root="data/game_ui")
        self.addCleanup(self.page.close)
        self.addCleanup(self.page.deleteLater)

    def test_exit_closes_session_before_clearing_without_empty_role_request(self):
        for scope in (None, "first"):
            for candidate in (False, True):
                with self.subTest(scope=scope, candidate=candidate):
                    page = self.page
                    page.marginal_page.set_editor_data(_editor_data())
                    page._marginal_result_scope = scope
                    page._marginal_result_is_candidate = candidate
                    page._stack.setCurrentWidget(page.marginal_page)
                    events = []

                    def closed():
                        self.assertIsNot(page._stack.currentWidget(), page.marginal_page)
                        self.assertEqual(page.marginal_page.selected_character_id(), 1001)
                        events.append("closed")

                    def requested():
                        events.append("baseline")

                    page.marginal_closed.connect(closed)
                    page.marginal_baseline_requested.connect(requested)
                    page.show_report()
                    page.marginal_closed.disconnect(closed)
                    page.marginal_baseline_requested.disconnect(requested)
                    self.assertEqual(events, ["closed"])
                    self.assertIsNone(page.marginal_page.selected_character_id())

    def test_editor_rebuild_is_silent_but_real_role_selection_notifies_once(self):
        marginal = self.page.marginal_page
        scopes = []
        marginal.role_changed.connect(scopes.append)
        marginal.set_editor_data(_editor_data(), selected_character_id=1001)
        self.assertEqual(scopes, [])
        self.assertEqual(marginal.editor_stack.currentIndex(), 0)
        marginal.character_combo.setCurrentIndex(1)
        self.assertEqual(scopes, ["second"])
        scopes.clear()
        marginal.set_editor_data(_editor_data(), selected_character_id=1002)
        self.assertEqual(scopes, [])
        self.assertEqual(marginal.selected_character_id(), 1002)
        self.assertEqual(marginal.editor_stack.currentIndex(), 1)
        marginal.clear_candidate()
        self.assertEqual(scopes, [])

    def test_reset_requests_only_final_selected_role_baseline(self):
        page = self.page
        page.marginal_page.set_editor_data(_editor_data(), selected_character_id=1002)
        page._marginal_result_scope = "second"
        page._stack.setCurrentWidget(page.marginal_page)
        requests = []
        page.marginal_baseline_requested.connect(
            lambda: requests.append((page.analysis_character_id(), page.marginal_detail_scope())))
        page.reset_marginal_draft(_editor_data())
        self.assertEqual(requests, [(1002, "second")])

    def test_show_editor_can_suppress_automatic_request_even_when_already_open(self):
        page = self.page
        page.marginal_page.set_editor_data(_editor_data())
        page._marginal_result_scope = "first"
        page._stack.setCurrentWidget(page.marginal_page)
        requests = []
        page.marginal_baseline_requested.connect(lambda: requests.append("baseline"))
        with patch.object(page.long_analysis_view, "selected_character_id", return_value=1001):
            page.show_marginal(_editor_data(), request_automatic_recalculation=False)
            self.assertEqual(requests, [])
            page.show_marginal(_editor_data())
        self.assertEqual(requests, ["baseline"])

    def test_reclick_selected_half_is_noop_and_different_half_notifies(self):
        view = self.page.long_analysis_view
        view.set_detail_scope("first", first_available=True, second_available=True)
        scopes = []
        view.detail_scope_changed.connect(scopes.append)
        view.scope_buttons["first"].click()
        self.assertEqual(scopes, [])
        view.scope_buttons["second"].click()
        view.scope_buttons["second"].click()
        self.assertEqual(scopes, ["second"])
        self.assertEqual(view.detail_scope(), "second")
        view.scope_buttons["current"].click()
        self.assertEqual(scopes, ["second", "current"])
