# 验证战报角色快照编辑与固定轴边际保持独立入口。
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QDialog

from src.features.battle_report.build_snapshot_controller import (
    BattleBuildSnapshotController,
)
from src.features.battle_report.marginal_session_controller import (
    BattleMarginalSessionController,
)
from src.features.battle_report.page import BattleReportPage
from src.services.battle_buff_counterfactual_service import (
    BUFF_COUNTERFACTUAL_MODEL_VERSION,
)
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidateService,
)
from src.services.battle_passive_counterfactual_service import (
    PASSIVE_COUNTERFACTUAL_MODEL_VERSION,
)


class BattleBuildSnapshotRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_edit_role_and_marginal_buttons_emit_distinct_requests(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        requests = []
        page.build_edit_requested.connect(lambda: requests.append("edit"))
        page.marginal_requested.connect(lambda: requests.append("marginal"))
        page.long_analysis_view.build_edit_control.edit_button.setEnabled(True)

        page.long_analysis_view.build_edit_control.edit_button.click()
        page.long_analysis_view.audit_buttons["marginal"].click()

        self.assertEqual(["edit", "marginal"], requests)

    def test_edit_role_opens_snapshot_dialog_while_marginal_opens_page(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")

        class FakeService:
            def __init__(self) -> None:
                self.saved_profiles = None

            @staticmethod
            def load_build_editor_data(_record_id):
                return {"has_edit": False, "details": []}

            def save_build_edit(self, _record_id, profiles):
                self.saved_profiles = profiles

            @staticmethod
            def sync_build_edit_to_role_page(_record_id):
                raise AssertionError("未选择同步时不应写角色页")

        class FakeEditorDialog:
            ACTION_IMPORT_CULTIVATION = "import_cultivation"
            ACTION_IMPORT_CULTIVATION_AND_EQUIPMENT = (
                "import_cultivation_and_equipment"
            )
            ACTION_SAVE_AND_SYNC_CULTIVATION = "save_and_sync_cultivation"

            def __init__(self, editor_data, parent) -> None:
                self.editor_data = editor_data
                self.parent = parent

            @staticmethod
            def exec():
                return QDialog.Accepted

            @staticmethod
            def profiles():
                return [{"character_id": 1001}]

            @staticmethod
            def action():
                return "save"

        service = FakeService()
        reload_analysis = Mock()
        errors = []
        with (
            patch(
                "src.features.battle_report.build_snapshot_controller."
                "BattleBuildSnapshotEditorDialog",
                FakeEditorDialog,
            ),
            patch.object(page, "show_marginal") as show_marginal,
        ):
            controller = BattleBuildSnapshotController(
                page=page,
                dialog_parent=page,
                service_provider=lambda: service,
                record_id_provider=lambda: 7,
                is_running=lambda: False,
                reload_analysis=reload_analysis,
                show_error=lambda title, error: errors.append((title, error)),
            )
            marginal = BattleMarginalSessionController(
                page=page,
                service_provider=lambda: service,
                record_id_provider=lambda: 7,
                is_running=lambda: False,
                reload_analysis=reload_analysis,
                invalidate_analysis=Mock(),
                show_error=lambda title, error: errors.append((title, error)),
            )

            controller.edit()
            self.assertEqual([{"character_id": 1001}], service.saved_profiles)
            show_marginal.assert_not_called()
            marginal.open()
            marginal_data = show_marginal.call_args.args[0]
            self.assertEqual([], marginal_data["details"])
            self.assertEqual("battle_frozen", marginal_data["marginal_baseline_kind"])

        self.assertEqual([], errors)

    def test_marginal_recalculation_uses_selected_role_half(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        page._source_analysis = SimpleNamespace(
            buff_counterfactual_model_version=BUFF_COUNTERFACTUAL_MODEL_VERSION,
            passive_counterfactual_model_version=(
                PASSIVE_COUNTERFACTUAL_MODEL_VERSION
            ),
        )
        reload_analysis = Mock()
        service = Mock()
        service.load_build_editor_data.return_value = {
            "equipment_editable": True,
            "details": [],
        }
        controller = BattleMarginalSessionController(
            page=page,
            service_provider=lambda: service,
            record_id_provider=lambda: 7,
            is_running=lambda: False,
            reload_analysis=reload_analysis,
            invalidate_analysis=Mock(),
            show_error=lambda _title, error: self.fail(str(error)),
        )
        with patch.object(page.marginal_page, "set_source_analysis"):
            controller.open()
        page.marginal_page._details = [{"analysis_detail_scope": "first"}]
        page.marginal_page.character_combo.addItem("上半场角色", 1001)
        service.load_build_editor_data.reset_mock()

        profiles = [{"character_id": 1001}]
        page._source_analysis = None
        controller.draft_changed()
        controller.recalculate(profiles)

        candidate = BattleMarginalCandidateService.freeze(
            7,
            profiles,
            equipment_editable=True,
        )

        reload_analysis.assert_called_once_with(
            7,
            selected_character_id=1001,
            detail_scope="first",
            detail_level="marginal",
            marginal_candidate=candidate,
            comparison_baseline=None,
            completion_kind="marginal",
        )
        service.load_build_editor_data.assert_not_called()

    def test_open_marginal_lazily_recalculates_missing_buff_counterfactuals(
        self,
    ) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        page._source_analysis = SimpleNamespace(
            buff_counterfactual_model_version="",
        )
        requests = []
        page.marginal_baseline_requested.connect(lambda: requests.append("baseline"))

        with patch.object(
            page.long_analysis_view,
            "selected_character_id",
            return_value=1001,
        ), patch.object(
            page.marginal_page,
            "set_editor_data",
        ), patch.object(
            page.marginal_page,
            "set_source_analysis",
        ):
            page.show_marginal({"details": []})

        self.assertEqual(["baseline"], requests)

    def test_open_marginal_reuses_completed_buff_counterfactuals(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        page._source_analysis = SimpleNamespace(
            buff_counterfactual_model_version=BUFF_COUNTERFACTUAL_MODEL_VERSION,
            passive_counterfactual_model_version=(
                PASSIVE_COUNTERFACTUAL_MODEL_VERSION
            ),
        )
        requests = []
        page.marginal_baseline_requested.connect(lambda: requests.append("baseline"))

        with patch.object(
            page.long_analysis_view,
            "selected_character_id",
            return_value=1001,
        ), patch.object(
            page.marginal_page,
            "set_editor_data",
        ), patch.object(
            page.marginal_page,
            "set_source_analysis",
        ), patch.object(
            page.marginal_page,
            "profiles",
        ) as profiles:
            page.show_marginal({"details": []})

        self.assertEqual([], requests)
        profiles.assert_not_called()

    def test_unchanged_marginal_recalculate_uses_baseline_request(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        reload_analysis = Mock()
        service = Mock()
        service.load_build_editor_data.return_value = {
            "equipment_editable": True,
            "details": [],
        }
        controller = BattleMarginalSessionController(
            page=page,
            service_provider=lambda: service,
            record_id_provider=lambda: 7,
            is_running=lambda: False,
            reload_analysis=reload_analysis,
            invalidate_analysis=Mock(),
            show_error=lambda _title, error: self.fail(str(error)),
        )
        controller.open()
        reload_analysis.reset_mock()

        controller.recalculate([{"character_id": 1001}])

        reload_analysis.assert_called_once_with(
            7,
            selected_character_id=page.analysis_character_id(),
            detail_scope=page.marginal_detail_scope(),
            detail_level="marginal",
            marginal_candidate=None,
            completion_kind="marginal",
        )

    def test_role_page_import_with_equipment_never_writes_back_to_role_page(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")

        class FakeService:
            include_equipment = None

            @staticmethod
            def load_build_editor_data(_record_id):
                return {"has_edit": True, "details": []}

            def sync_role_page_to_build_edit(
                self,
                _record_id,
                *,
                include_equipment=False,
            ):
                self.include_equipment = include_equipment

            @staticmethod
            def sync_build_edit_to_role_page(_record_id):
                raise AssertionError("从角色页导入不得反向写回角色页")

        class FakeEditorDialog:
            ACTION_IMPORT_CULTIVATION = "import_cultivation"
            ACTION_IMPORT_CULTIVATION_AND_EQUIPMENT = (
                "import_cultivation_and_equipment"
            )
            ACTION_SAVE_AND_SYNC_CULTIVATION = "save_and_sync_cultivation"

            def __init__(self, _editor_data, _parent) -> None:
                pass

            @staticmethod
            def exec():
                return QDialog.Accepted

            @staticmethod
            def action():
                return "import_cultivation_and_equipment"

            @staticmethod
            def profiles():
                raise AssertionError("导入角色页时不应读取弹窗草稿")

        service = FakeService()
        reload_analysis = Mock()
        with (
            patch(
                "src.features.battle_report.build_snapshot_controller."
                "BattleBuildSnapshotEditorDialog",
                FakeEditorDialog,
            ),
            patch(
                "src.features.battle_report.build_snapshot_controller."
                "QMessageBox.information",
            ),
        ):
            controller = BattleBuildSnapshotController(
                page=page,
                dialog_parent=page,
                service_provider=lambda: service,
                record_id_provider=lambda: 7,
                is_running=lambda: False,
                reload_analysis=reload_analysis,
                show_error=lambda _title, error: self.fail(str(error)),
            )
            controller.edit()

        self.assertTrue(service.include_equipment)
        reload_analysis.assert_called_once()

    def test_leaving_marginal_page_discards_candidate_session(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        page.show_marginal(
            {"details": [], "marginal_equipment_editable": False}
        )
        self.assertFalse(page.marginal_equipment_editable())

        page.show_report()

        self.assertEqual([], page.marginal_page._details)
        self.assertEqual([], page.marginal_page.profiles())

    def test_open_marginal_reuses_source_analysis_and_selected_role(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        source = object()
        page._source_analysis = source
        editor_data = {"details": []}

        with patch.object(
            page.long_analysis_view,
            "selected_character_id",
            return_value=1002,
        ), patch.object(
            page.marginal_page,
            "set_editor_data",
        ) as set_editor_data, patch.object(
            page.marginal_page,
            "set_source_analysis",
        ) as set_source_analysis:
            page.show_marginal(editor_data)

        set_editor_data.assert_called_once_with(
            editor_data,
            selected_character_id=1002,
        )
        set_source_analysis.assert_called_once_with(source)

    def test_source_analysis_keeps_baseline_timeline_visible(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        source = SimpleNamespace(
            build_counterfactual=None,
            effective_dps=100.0,
            effective_damage=1_000.0,
        )

        with patch.object(
            page.marginal_page.counterfactual_timeline,
            "set_analysis",
        ) as set_timeline, patch.object(
            page.marginal_page,
            "_render_selected_role",
        ):
            page.marginal_page.set_source_analysis(source)

        set_timeline.assert_called_once_with(source)
        self.assertEqual("100", page.marginal_page.metric_labels["dps"].text())
        self.assertEqual("1,000", page.marginal_page.metric_labels["damage"].text())

    def test_marginal_reset_uses_entry_snapshot_without_reloading_service(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        service = Mock()
        service.load_build_editor_data.return_value = {
            "battle_record_id": 7,
            "is_active": False,
            "equipment_editable": True,
            "details": [],
        }
        controller = BattleMarginalSessionController(
            page=page,
            service_provider=lambda: service,
            record_id_provider=lambda: 7,
            is_running=lambda: False,
            reload_analysis=Mock(),
            invalidate_analysis=Mock(),
            show_error=lambda _title, error: self.fail(str(error)),
        )

        controller.open()
        service.load_build_editor_data.reset_mock()
        page.marginal_page.reset_button.click()

        self.assertIsNotNone(controller)
        service.load_build_editor_data.assert_not_called()
        service.save_build_edit.assert_not_called()

    def test_marginal_draft_change_invalidates_inflight_analysis(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        service = Mock()
        service.load_build_editor_data.return_value = {
            "equipment_editable": True,
            "details": [],
        }
        invalidate_analysis = Mock()
        controller = BattleMarginalSessionController(
            page=page,
            service_provider=lambda: service,
            record_id_provider=lambda: 7,
            is_running=lambda: False,
            reload_analysis=Mock(),
            invalidate_analysis=invalidate_analysis,
            show_error=lambda _title, error: self.fail(str(error)),
        )
        controller.open()

        page.marginal_draft_changed.emit()

        invalidate_analysis.assert_called_once_with()

    def test_switching_half_discards_result_from_other_half(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        source = object()
        page._source_analysis = source
        page._marginal_result_scope = "first"
        with patch.object(
            page.marginal_page,
            "set_source_analysis",
        ) as set_source:
            page._marginal_role_changed("second")

        self.assertIsNone(page._marginal_result_scope)
        set_source.assert_called_once_with(source)


if __name__ == "__main__":
    unittest.main()
