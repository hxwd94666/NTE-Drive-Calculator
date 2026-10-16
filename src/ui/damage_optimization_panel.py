# 计算预览与已保存配装共用优化摘要控件，详情由用户展开且支持键盘操作。
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.features.allocation.damage_result_summary import damage_optimization_presentation


def damage_optimization_panel(value, *, object_name: str, parent=None):
    presentation = damage_optimization_presentation(value)
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
    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(10)
    layout.addLayout(header)
    headline = QLabel(presentation.headline, panel)
    headline.setTextFormat(Qt.PlainText)
    headline.setToolTip(presentation.note)
    color = "#3fb950" if presentation.improved else "#d29922"
    headline.setStyleSheet(themed_style(f"color:{color};font-weight:700;font-size:13px;"
                                       "border:none;background:transparent;padding:0;margin:0"))
    header.addWidget(headline)
    toggle = QToolButton(panel)
    toggle.setText("查看详情")
    toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    toggle.setArrowType(Qt.DownArrow)
    toggle.setFocusPolicy(Qt.StrongFocus)
    toggle.setCheckable(True)
    toggle.setAccessibleName("查看优化详情")
    toggle.setStyleSheet(themed_style(
        "QToolButton{color:#8b949e;background:transparent;border:1px solid transparent;"
        "border-radius:3px;padding:2px 4px;font-size:12px;}"
        "QToolButton:hover{color:#58a6ff;}QToolButton:focus{border-color:#58a6ff;}"
    ))
    header.addWidget(toggle)
    header.addStretch()
    # 默认只占一行；适用范围和原因随详情向下展开，搜索未完成等状态仍保留在摘要。
    details = QLabel(presentation.note + "\n" + presentation.details, panel)
    details.setTextFormat(Qt.PlainText)
    details.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    details.setWordWrap(True)
    layout.addWidget(details)
    details.hide()

    def expand(checked):
        details.setVisible(checked)
        toggle.setText("收起详情" if checked else "查看详情")
        toggle.setArrowType(Qt.UpArrow if checked else Qt.DownArrow)
        toggle.setAccessibleName("收起优化详情" if checked else "查看优化详情")

    toggle.toggled.connect(expand)
    return panel
