# 提供纯文本、有色背景和左侧标记的公共提示卡，统一适配三种主题。
from PySide6.QtWidgets import QFrame, QVBoxLayout

from src.app.theme import current_theme_name, theme_color, theme_rgba
from src.ui.common.text_catalog import plain_label


def notice_panel(parent, message, *, warning=False, object_name=None):
    """用有色底、左侧标记及完整正文区分建议与警告，不靠单独改字色暗示层级。"""
    frame = QFrame(parent)
    frame.setObjectName(object_name or ("warningNotice" if warning else "infoNotice"))
    accent = "#d29922" if warning else "#58a6ff"
    foreground = ("#7d4e00" if current_theme_name() == "light" else "#e3b341") if warning else theme_color("#58a6ff")
    selector = "QFrame#" + frame.objectName()
    frame.setStyleSheet(
        f"{selector}{{background:{theme_rgba(accent, 0.12)};border:0;"
        f"border-left:3px solid {theme_color(accent)};border-radius:0;}}"
        f"{selector} QLabel{{background:transparent;border:0;color:{foreground};font-size:12px;}}"
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 11, 14, 11)
    label = plain_label(message, frame)
    label.setWordWrap(True)
    layout.addWidget(label)
    frame.setAccessibleName(message)
    return frame

