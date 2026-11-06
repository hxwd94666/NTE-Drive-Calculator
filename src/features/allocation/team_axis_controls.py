# 计算页独立战报增强入口，不修改原角色顺序和属性权重。
from concurrent.futures import CancelledError

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy, QWidget,
)

from src.app.theme import themed_style
from src.features.allocation.team_axis_dialog import TeamAxisDialog
from src.services.allocation.search_settings import SEARCH_MODES, load_search_mode, save_search_mode


def build_team_axis_controls(owner, layout):
    owner.team_axis_selection = None
    owner.team_axis_row = QWidget()
    row = QHBoxLayout(owner.team_axis_row)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)
    owner.team_axis_check = QCheckBox("战报增强")
    owner.team_axis_check.setAccessibleName("启用战报增强")
    owner.team_axis_check.setToolTip("先优化一支或两支互斥队伍，其余角色继续按原策略分配")
    row.addWidget(owner.team_axis_check)
    owner.team_axis_configure = QPushButton("进行配置")
    owner.team_axis_configure.setObjectName("allocationFilterSettingsButton")
    owner.team_axis_configure.setFixedHeight(28)
    owner.team_axis_configure.setMinimumWidth(
        owner.team_axis_configure.fontMetrics().horizontalAdvance("进行配置") + 24)
    # 复用标题设置按钮的启用样式；关闭状态保留置灰，不让蓝色底误导为可操作。
    owner.team_axis_configure.setStyleSheet(themed_style(
        "QPushButton:disabled{background:#161b22;color:#6e7681;border-color:#21262d;}"))
    owner.team_axis_configure.setAccessibleName("配置队伍与战报")
    owner.team_axis_configure.setToolTip("选择队伍、战报与事件范围；不会立即开始计算。")
    row.addWidget(owner.team_axis_configure)
    owner.team_axis_search_label = QLabel("搜索档位")
    owner.team_axis_search_label.setStyleSheet(themed_style("QLabel:disabled{color:#6e7681;}"))
    row.addWidget(owner.team_axis_search_label)
    owner.team_axis_search = QComboBox()
    owner.team_axis_search.setAccessibleName("战报增强搜索档位")
    owner.team_axis_search.setStyleSheet(themed_style(
        "QComboBox:disabled{background:#161b22;color:#6e7681;border-color:#21262d;}"))
    owner.team_axis_search.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    owner.team_axis_search.setMinimumContentsLength(10)
    owner.team_axis_search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    owner.team_axis_search.setMaximumWidth(240)
    owner.team_axis_search_label.setBuddy(owner.team_axis_search)
    for key, label in SEARCH_MODES:
        owner.team_axis_search.addItem(label, key)
    owner.team_axis_search.setToolTip("快搜适合日常预览；深搜检查更多组合。两档公式相同，实际耗时取决于战报和电脑性能，可随时取消。")
    reload_search_mode(owner)
    row.addWidget(owner.team_axis_search, 1)
    row.addStretch()
    layout.addWidget(owner.team_axis_row)
    owner.team_axis_search.currentIndexChanged.connect(lambda: persist_search_mode(owner))
    owner.team_axis_configure.clicked.connect(lambda: configure_team_axes(owner))

    def toggled(enabled):
        if enabled and owner.team_axis_selection is None:
            configure_team_axes(owner)
            if owner.team_axis_selection is None:
                owner.team_axis_check.setChecked(False)
        _sync_team_axis_controls(owner)

    owner.team_axis_check.toggled.connect(toggled)
    _sync_team_axis_controls(owner)


def _sync_team_axis_controls(owner):
    # 开关只决定是否使用增强；关闭保留本账号草稿，但不允许误点配置或搜索档位。
    enabled = owner.team_axis_check.isChecked()
    owner.team_axis_configure.setEnabled(enabled)
    owner.team_axis_search.setEnabled(enabled)
    owner.team_axis_search_label.setEnabled(enabled)


def reset_team_axis_controls(owner):
    # 账号切换即使发生在开关已关闭时，也须清掉旧配置，不能只依赖 toggled 信号。
    owner.team_axis_selection = None
    if getattr(owner, "team_axis_check", None) is not None:
        owner.team_axis_check.setChecked(False)
        _sync_team_axis_controls(owner)


def reload_search_mode(owner):
    combo = getattr(owner, "team_axis_search", None)
    if combo is None:
        return
    mode = load_search_mode(owner.app_context.account.user_database_path)
    previous = combo.blockSignals(True)
    combo.setCurrentIndex(combo.findData(mode))
    combo.blockSignals(previous)


def persist_search_mode(owner):
    try:
        save_search_mode(owner.app_context.account.user_database_path, owner.team_axis_search.currentData())
    except Exception as error:
        reload_search_mode(owner)
        QMessageBox.warning(owner.dialog_parent, "保存搜索档位失败", str(error))


def configure_team_axes(owner, *, initial_request=None, initial_team_id=None, initial_match=None):
    provider = owner._axis_selection_service_provider
    if provider is None:
        return None
    try:
        service = provider()
        matcher = owner._team_report_service_provider()
        dialog = TeamAxisDialog(service=service, matching_service=matcher,
                                role_names=tuple(owner.role_selector.all_roles),
                                initial=owner.team_axis_selection, initial_request=initial_request,
                                initial_team_id=initial_team_id, initial_match=initial_match,
                                parent=owner.dialog_parent)
    except CancelledError:
        return None
    except (ValueError, RuntimeError) as error:
        QMessageBox.warning(owner.dialog_parent, "战报增强", str(error))
        return None
    try:
        owner._team_axis_dialog = dialog
        if dialog.exec() != QDialog.Accepted:
            if dialog.manage_teams:
                owner.navigate("teams")
            return None
        owner.team_axis_selection = dialog.selection
        owner.team_axis_check.setChecked(True)
        _sync_team_axis_controls(owner)
        return dialog.selection
    finally:
        owner._team_axis_dialog = None
        dialog.close()
        dialog.deleteLater()
