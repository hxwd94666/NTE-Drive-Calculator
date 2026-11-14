# 验证仓库顶栏冲突数量与查看详情动作分离，正文不重复冲突文案。
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from src.features.inventory.warehouse.management.management_projection import refresh_conflict_summary
from src.integrations.preferences.locale_files import bundled_text_catalog


def test_topbar_conflict_count_and_details_are_separate():
    QApplication.instance() or QApplication([])
    summary = QLabel()
    details = QPushButton()
    container = QWidget()
    issue = QLabel()
    owner = SimpleNamespace(
        conflicts=[{"uid": "one"}, {"uid": "two"}],
        conflict_button=details,
        conflict_summary_label=summary,
        conflict_summary_container=container,
        conflict_container=None,
        issue_label=issue,
        management_issues=[],
    )
    host = SimpleNamespace(warehouse_read_controller=owner, text_catalog=bundled_text_catalog())
    refresh_conflict_summary(host)
    assert summary.text() == "本次处理有 2 处冲突"
    assert details.text() == "查看详情"
    assert container.isVisible()
    assert not issue.isVisible()
    owner.conflicts = []
    refresh_conflict_summary(host)
    assert not container.isVisible() and not details.isVisible()
