# 验证匹配编辑器的用途提示按业务域隔离及搜索入口的可访问性。

from PySide6.QtWidgets import QDialog, QLabel

from tests.support.auto_sync_ui_fixture import dispose
from tests.teams.matching.team_report_fixture import MatchingFixture
from tests.teams.matching.team_report_fixture import drain_dialog
from tests.allocation.test_team_axis_enhancement import choices, teams
from tests.support.theme import themed_app as themed_app
from src.domain.equipment.eligibility.management_settings import MatchingScope
from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog
from src.integrations.preferences.locale_files import bundled_text_catalog
from src.ui.role_suit_matching.dialog import MatchingEditorDialog

def test_search_caption_and_warehouse_only_footer_removal(themed_app):
    dialog = TeamAxisDialog(service=None, matching_service=MatchingFixture(), teams=teams(), choices=choices())
    warehouse = MatchingEditorDialog(bundled_text_catalog(), embedded=True, scope=MatchingScope.WAREHOUSE)
    identification = MatchingEditorDialog(bundled_text_catalog(), embedded=True, scope=MatchingScope.IDENTIFICATION)
    try:
        assert dialog.heading.text() == dialog._text('teams.selection.search_label')
        assert dialog.heading.buddy() is dialog.search
        assert not any(label.text() == bundled_text_catalog().render('matching.scope.warehouse_management')
                       for label in warehouse.findChildren(QLabel))
        assert any(label.text() == bundled_text_catalog().render('matching.scope.identification')
                   for label in identification.findChildren(QLabel))
    finally:
        drain_dialog(dialog);dispose(dialog)
        for widget in (warehouse, identification):
            widget.done(QDialog.Rejected);dispose(widget)

