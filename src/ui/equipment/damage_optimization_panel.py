# 计算预览与已保存配装共用优化摘要控件，详情由用户展开且支持键盘操作。
from __future__ import annotations

from src.ui.common.text_catalog import plain_label, resolve_text_catalog
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.features.allocation.results.damage_result_summary import damage_optimization_presentation


def damage_optimization_panel(value, *, object_name: str, parent=None, save_state=None, text_catalog=None):
    presentation = damage_optimization_presentation(value, text_catalog=resolve_text_catalog(text_catalog))
    if presentation is None:
        return None
    panel = QWidget(parent)
    panel.setObjectName(object_name)
    panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    # 结果页祖先可能设置通用卡片样式；摘要及子标签不再继承边框、背景和大内边距。
    panel.setStyleSheet("QWidget{background:transparent;border:none;border-radius:0;"
                       "padding:0;margin:0;min-height:0;}")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    if save_state is not None:
        state = plain_label(save_state, panel)
        state.setTextFormat(Qt.PlainText)
        state.setWordWrap(True)
        layout.addWidget(state)
    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(10)
    layout.addLayout(header)
    headline = plain_label(presentation.headline, panel)
    headline.setTextFormat(Qt.PlainText)
    headline.setWordWrap(True)
    headline.setToolTip(presentation.note)
    color = "#3fb950" if presentation.improved else "#d29922"
    headline.setStyleSheet(themed_style(f"color:{color};font-weight:700;font-size:13px;"
                                       "border:none;background:transparent;padding:0;margin:0"))
    header.addWidget(headline, 1)
    toggle = QToolButton(panel)
    toggle.setText(resolve_text_catalog(text_catalog).render('warehouse.management.conflict.details'))
    toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    toggle.setArrowType(Qt.DownArrow)
    toggle.setFocusPolicy(Qt.StrongFocus)
    toggle.setCheckable(True)
    toggle.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.equipment.damage_optimization_panel.toggle'))
    toggle.setStyleSheet(themed_style(
        "QToolButton{color:#8b949e;background:transparent;border:1px solid transparent;"
        "border-radius:3px;padding:2px 4px;font-size:12px;}"
        "QToolButton:hover{color:#58a6ff;}QToolButton:focus{border-color:#58a6ff;}"
    ))
    header.addWidget(toggle)
    header.addStretch()
    # 默认展示收益、量化/估算覆盖率并按可用宽度换行；完整口径和原因随详情展开。
    details = plain_label(presentation.note + "\n" + presentation.details, panel)
    details.setTextFormat(Qt.PlainText)
    details.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    details.setWordWrap(True)
    layout.addWidget(details)
    details.hide()

    def expand(checked):
        details.setVisible(checked)
        toggle.setText(resolve_text_catalog(text_catalog).render('ui.equipment.expand.toggle') if checked else resolve_text_catalog(text_catalog).render('warehouse.management.conflict.details'))
        toggle.setArrowType(Qt.UpArrow if checked else Qt.DownArrow)
        toggle.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.equipment.expand.toggle_2') if checked else resolve_text_catalog(text_catalog).render('ui.equipment.damage_optimization_panel.toggle'))

    toggle.toggled.connect(expand)
    return panel
