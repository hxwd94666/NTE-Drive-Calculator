# 计算页独立战报增强入口，不修改原角色顺序和属性权重。
from concurrent.futures import CancelledError

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGridLayout, QMessageBox, QPushButton, QSizePolicy, QWidget,
)

from src.app.theme import themed_style
from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import ElidedTextLabel, plain_tooltip, resolve_text_catalog, set_button_text
from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog
from src.services.allocation.inputs.search_settings import SEARCH_MODES, load_search_mode, save_search_mode


class _EnhancementRow(QWidget):
    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.controls, self._wide = (), None

    def arrange(self, controls):
        self.controls = controls
        self._reflow(True)

    def _reflow(self, wide):
        if not self.controls or wide == self._wide:
            return
        self._wide = wide
        while self.grid.count():
            self.grid.takeAt(0)
        for column in range(5):
            self.grid.setColumnStretch(column, 0)
        for index, widget in enumerate(self.controls):
            if wide:
                self.grid.addWidget(widget, 0, index)
            else:
                self.grid.addWidget(widget, index // 2, index % 2)
        self.grid.setColumnStretch(4 if wide else 1, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        minimum = sum(widget.minimumSizeHint().width() for widget in self.controls) + 24
        self._reflow(self.width() >= minimum)


def build_team_axis_controls(owner, layout, *, text_catalog=None):
    text = resolve_text_catalog(getattr(owner, 'text_catalog', None)).render
    owner.team_axis_selection = None
    owner.team_axis_row = _EnhancementRow()
    owner.team_axis_check = QCheckBox(text('teams.axis.label'))
    owner.team_axis_check.setAccessibleName(text('teams.axis.enable_accessible'))
    owner.team_axis_check.setToolTip(plain_tooltip(text('teams.axis.enable_hint')))
    owner.team_axis_configure = QPushButton(text('teams.axis.configure'))
    owner.team_axis_configure.setObjectName("allocationFilterSettingsButton")
    owner.team_axis_configure.setFixedHeight(28)
    owner.team_axis_configure.setMinimumWidth(
        owner.team_axis_configure.fontMetrics().horizontalAdvance(text('teams.axis.configure')) + 24)
    # 复用标题设置按钮的启用样式；关闭状态保留置灰，不让蓝色底误导为可操作。
    owner.team_axis_configure.setStyleSheet(themed_style(
        "QPushButton:disabled{background:#161b22;color:#6e7681;border-color:#21262d;}"))
    owner.team_axis_configure.setAccessibleName(text('teams.axis.configure'))
    owner.team_axis_configure.setToolTip(plain_tooltip(text('teams.axis.configure_hint')))
    owner.team_axis_search_label = ElidedTextLabel(text('teams.axis.search_label'))
    owner.team_axis_search_label.setTextFormat(Qt.PlainText)
    owner.team_axis_search_label.setStyleSheet(themed_style("QLabel:disabled{color:#6e7681;}"))
    owner.team_axis_search = QComboBox()
    owner.team_axis_search.setAccessibleName(text('teams.axis.search_accessible'))
    owner.team_axis_search.setStyleSheet(themed_style(
        "QComboBox:disabled{background:#161b22;color:#6e7681;border-color:#21262d;}"))
    owner.team_axis_search.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    owner.team_axis_search.setMinimumContentsLength(10)
    owner.team_axis_search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    owner.team_axis_search.setMaximumWidth(240)
    owner.team_axis_search_label.setBuddy(owner.team_axis_search)
    for key, _label in SEARCH_MODES:
        owner.team_axis_search.addItem(text('teams.axis.search.' + key), key)
    owner.team_axis_search.setToolTip(plain_tooltip(text('teams.axis.search_hint')))
    reload_search_mode(owner)
    owner.team_axis_row.arrange((owner.team_axis_check, owner.team_axis_configure, owner.team_axis_search_label,
                                owner.team_axis_search))
    set_button_text(owner.team_axis_configure, text('teams.axis.configure'))
    set_button_text(owner.team_axis_check, text('teams.axis.label'))
    layout.addWidget(owner.team_axis_row)
    owner.team_axis_search.currentIndexChanged.connect(lambda: persist_search_mode(owner, text_catalog=text_catalog))
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


def persist_search_mode(owner, *, text_catalog=None):
    try:
        save_search_mode(owner.app_context.account.user_database_path, owner.team_axis_search.currentData())
    except Exception as error:
        reload_search_mode(owner)
        QMessageBox.warning(owner.dialog_parent, resolve_text_catalog(text_catalog).render('ui.allocation.persist_search_mode.qmessagebox'), ui_enum_text(text_catalog, str(error)))


def configure_team_axes(owner, *, initial_request=None, initial_team_id=None, initial_match=None):
    selection = choose_team_axes(owner, initial_request=initial_request,
                                 initial_team_id=initial_team_id, initial_match=initial_match)
    if selection is not None:
        apply_team_axis_selection(owner, selection)
    return selection


def apply_team_axis_selection(owner, selection):
    """应用已确认草稿时抑制开关自动弹窗，确保一次配置只打开一层。"""
    owner.team_axis_selection = selection
    with QSignalBlocker(owner.team_axis_check):
        owner.team_axis_check.setChecked(True)
    _sync_team_axis_controls(owner)


def choose_team_axes(owner, *, initial_request=None, initial_team_id=None, initial_match=None, required_team=None):
    """只返回确认选择，不改选人或增强开关；取消与前往管理均不提交草稿。"""
    provider = owner._axis_selection_service_provider
    if provider is None:
        return None
    try:
        service = provider()
        matcher = owner._team_report_service_provider()
        dialog = TeamAxisDialog(service=service, matching_service=matcher,
                                role_names=tuple(owner.role_selector.all_roles),
                                initial=owner.team_axis_selection if required_team is None else None,
                                initial_request=initial_request,
                                initial_team_id=initial_team_id, initial_match=initial_match,
                                parent=owner.dialog_parent, catalog=getattr(owner, "text_catalog", None),
                                required_team=required_team)
    except CancelledError:
        return None
    except (ValueError, RuntimeError) as error:
        QMessageBox.warning(owner.dialog_parent, resolve_text_catalog(getattr(owner, "text_catalog", None)).render("teams.axis.label"), ui_enum_text(getattr(owner, "text_catalog", None), str(error)))
        return None
    try:
        owner._team_axis_dialog = dialog
        if dialog.exec() != QDialog.Accepted:
            if dialog.manage_teams:
                owner.navigate("teams")
            return None
        return dialog.selection
    finally:
        owner._team_axis_dialog = None
        dialog.close()
        dialog.deleteLater()
