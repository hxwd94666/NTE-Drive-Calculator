# 将自动装配的准备事项、条件提示和主角名字放在同一确认页，确认后原窗隐藏待结果复用。
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.ui.common.notice import notice_panel
from src.ui.common.text_catalog import ElidedTextLabel, TextActionButton, plain_label, set_button_text
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.preferences.work_mode import WorkMode
from src.features.inventory.equipment.assembly.assembly_surface import AssemblySurface, release_assembly_surface

BACK_TO_SELECTION = object()


def _divider(layout):
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(themed_style("background:#30363d;border:0;"))
    layout.addWidget(line)


def _heading(message):
    label = plain_label(message)
    label.setWordWrap(True)
    label.setStyleSheet(themed_style("color:#f0f6fc;font-size:14px;font-weight:700;"))
    return label


class _PreparationScrollArea(QScrollArea):
    """按实际视口宽度保留换行后的说明高度，避免缩窄窗口时沿用旧高度而截字。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type() in (QEvent.Resize, QEvent.Show):
            content = self.widget()
            if content is not None and content.layout() is not None:
                height = content.layout().totalHeightForWidth(self.viewport().width())
                content.setMinimumHeight(max(0, height))
        return super().eventFilter(watched, event)


class AutomaticAssemblyDialog(AssemblySurface):
    def __init__(self, parent, count, *, stop_hotkey, needs_name=False, name="", medium=False, catalog=None):
        super().__init__(parent, catalog=catalog)
        self.setWindowTitle(self.text("assembly.auto.confirm_title"))
        self.setModal(True)
        root = self.body_layout
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)
        # 准备信息可滚动，执行按钮固定在视口外；小屏和长译文不得挤掉停止键等关键说明。
        scroll = _PreparationScrollArea(self.body_page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget(scroll)
        details = QVBoxLayout(content)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(12)
        details.setSizeConstraint(QLayout.SetNoConstraint)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        self.summary = plain_label(self.text("assembly.auto.summary", count=count), self.body_page)
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(themed_style("color:#58a6ff;font-weight:700;"))
        details.addWidget(self.summary)
        _divider(details)
        if medium:
            details.addWidget(notice_panel(content, self.text("assembly.auto.medium_hint"), object_name="assemblyInfoNotice"))
        details.addWidget(_heading(self.text("assembly.auto.preparation_title")))
        steps = QVBoxLayout()
        steps.setSpacing(8)
        for message in (
            self.text("assembly.auto.prepare_game"),
            self.text("assembly.auto.prepare_focus"),
            self.text("assembly.auto.prepare_stop", hotkey=stop_hotkey),
        ):
            label = plain_label(message)
            label.setWordWrap(True)
            steps.addWidget(label)
        details.addLayout(steps)
        details.addWidget(notice_panel(content, self.text("assembly.auto.duplicate_hint"), warning=True, object_name="assemblyWarningNotice"))
        self.name_edit = None
        self.remember_name = None
        if needs_name:
            _divider(details)
            name_heading = QHBoxLayout()
            name_label = _heading(self.text("assembly.auto.protagonist_label"))
            name_heading.addWidget(name_label)
            required = ElidedTextLabel(self.text("assembly.auto.protagonist_required"))
            required.setStyleSheet(themed_style("color:#f85149;font-weight:600;"))
            required.setMinimumWidth(min(90, required.fontMetrics().horizontalAdvance(required.text()) + 6))
            required.setMaximumWidth(90)
            name_heading.addWidget(required)
            name_heading.addStretch()
            details.addLayout(name_heading)
            helper = plain_label(self.text("assembly.auto.protagonist_help"))
            helper.setWordWrap(True)
            helper.setStyleSheet(themed_style("color:#8b949e;font-size:12px;"))
            details.addWidget(helper)
            self.name_edit = QLineEdit(self.body_page)
            self.name_edit.setAccessibleName(self.text("assembly.auto.protagonist_label"))
            self.name_edit.setAccessibleDescription(self.text("assembly.auto.protagonist_help"))
            name_label.setBuddy(self.name_edit)
            self.name_edit.setPlaceholderText(self.text("assembly.auto.protagonist_placeholder"))
            self.name_edit.setText(name)
            details.addWidget(self.name_edit)
            self.remember_name = QCheckBox(self.text("assembly.auto.remember_name"), self.body_page)
            set_button_text(self.remember_name, self.text("assembly.auto.remember_name"))
            self.remember_name.setChecked(True)
            details.addWidget(self.remember_name)
        details.addStretch()
        _divider(root)
        actions = QHBoxLayout()
        self.back_button = TextActionButton(self.text("common.back_to_selection"), self.body_page)
        self.back_button.setAutoDefault(False)
        self.back_button.clicked.connect(lambda: self.done(2))
        actions.addWidget(self.back_button)
        actions.addStretch()
        cancel = TextActionButton(self.text("common.cancel"), self.body_page)
        cancel.setDefault(True)
        cancel.clicked.connect(self.reject)
        self.start_button = TextActionButton(self.text("assembly.auto.start"), self.body_page)
        self.start_button.setAutoDefault(False)
        self.start_button.setStyleSheet(
            themed_style("background:#1f6feb;color:#fff;border:1px solid #58a6ff;padding:6px 16px;")
        )
        self.start_button.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(self.start_button)
        root.addLayout(actions)
        if self.name_edit is not None:
            self.name_edit.textChanged.connect(self._validate)
        self._validate()
        fit_dialog_to_available_screen(self, QSize(720, 520 if needs_name else 400))

    def _validate(self):
        self.start_button.setEnabled(self.name_edit is None or bool(self.name_edit.text().strip()))

    def protagonist_name(self):
        return self.name_edit.text().strip() if self.name_edit is not None else ""


def confirm_automatic_execution(window, role_names, stop_hotkey):
    """只读取已有偏好作草稿，取消不保存名字；保存由派发 owner 在校验通过后执行。"""
    preferences = getattr(window, "_ui_preferences", {}) or {}
    protagonists = set(role_names).intersection({"主角", "零", "「零」"})
    known_name = str(
        preferences.get("protagonist_game_name") or getattr(window, "_drive_assembly_protagonist_name", "") or ""
    ).strip()
    policy = getattr(window, "work_mode_service", None)
    medium = getattr(getattr(policy, "settings", None), "mode", None) == WorkMode.MEDIUM
    release_assembly_surface(window)
    dialog = AutomaticAssemblyDialog(
        window,
        len(role_names),
        stop_hotkey=stop_hotkey,
        needs_name=bool(protagonists) and not known_name,
        medium=medium,
        catalog=getattr(window, "text_catalog", None),
    )
    answer = dialog.exec()
    if answer != QDialog.Accepted:
        dialog.deleteLater()
        return BACK_TO_SELECTION if answer == 2 else None
    window._assembly_surface = dialog
    name = known_name or dialog.protagonist_name()
    aliases = {role: name for role in protagonists} if name else {}
    return dialog, aliases
