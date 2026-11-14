# 展示战报原始角色配置与单一修改副本的切换状态。
"""Battle build edit control card."""

from __future__ import annotations

from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import TextActionButton, plain_label, plain_tooltip, resolve_text_catalog
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QWidget,
)

class BattleBuildSnapshotControl(QWidget):
    edit_requested = Signal()
    activation_requested = Signal(bool)
    role_page_import_requested = Signal()
    environment_requested = Signal()

    def __init__(self, parent: QWidget | None = None, *, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._active = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # 未放入布局的隐藏控制仍归本页，不随 Python 回调引用残留为顶层控件。
        self.status = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.init.status'), self)
        self.status.hide()
        self.edit_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.battle_report.init.edit_button'))
        self.edit_button.clicked.connect(self.edit_requested)
        self.edit_button.setEnabled(False)
        layout.addWidget(self.edit_button)
        self.import_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.battle_report.init.import_button'), self)
        self.import_button.setToolTip(
            plain_tooltip(resolve_text_catalog(text_catalog).render('ui.battle_report.init.import_button_2'))
        )
        self.import_button.clicked.connect(self.role_page_import_requested)
        self.import_button.setEnabled(False)
        self.import_button.hide()
        self.environment_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.battle_report.init.environment_button'), self)
        self.environment_button.setToolTip(
            plain_tooltip(resolve_text_catalog(text_catalog).render('ui.battle_report.init.environment_button_2'))
        )
        self.environment_button.clicked.connect(self.environment_requested)
        self.environment_button.setEnabled(False)
        self.environment_button.hide()
        self.activation_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.battle_report.init.activation_button'), self)
        self.activation_button.clicked.connect(self._request_activation)
        self.activation_button.setEnabled(False)
        self.activation_button.hide()

    def set_state(
        self,
        *,
        has_edit: bool,
        active: bool,
        available: bool = True,
    ) -> None:
        self._active = bool(active)
        self.edit_button.setEnabled(available)
        self.import_button.setEnabled(available)
        self.activation_button.setEnabled(available and has_edit)
        if not available:
            text = "当前记录没有可编辑的角色配置快照。"
        elif not has_edit:
            text = "当前使用原始快照；首次编辑会复制本场原始冻结配置。"
        elif active:
            text = "当前使用修改副本；它只用于逐击重放和边际计算，主页面实测数据不变。"
        else:
            text = "当前已恢复原始快照；修改副本仍保留，可继续编辑或重新启用。"
        self.status.setText(ui_enum_text(getattr(self, 'text_catalog', None), text))
        self.edit_button.setToolTip(plain_tooltip(ui_enum_text(getattr(self, 'text_catalog', None), text)))
        self.activation_button.setText(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.init.activation_button') if active else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_state.activation_button')
        )

    def set_environment_state(self, *, status: str, summary: str = "") -> None:
        labels = {
            "configured": "已配置",
            "inferred": "已推理",
            "unconfigured": "未配置",
        }
        label = labels.get(status, "未配置")
        self.environment_button.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_environment_state.environment_button', label=ui_enum_text(getattr(self, 'text_catalog', None), f'{label}')))
        self.environment_button.setToolTip(
            plain_tooltip(summary or resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.init.environment_button_2'))
        )

    def _request_activation(self) -> None:
        self.activation_requested.emit(
            not self._active
        )
