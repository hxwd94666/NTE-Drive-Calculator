# 角色图鉴的默认折叠审计信息。
"""Collapsed raw-identity details for player-facing character views."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from src.app.theme import themed_style


def build_more_info(
    rows: Iterable[tuple[str, str | None]],
    *,
    parent: QWidget | None = None,
) -> QFrame | None:
    """Build a default-collapsed copy area for raw professional identities."""

    values = tuple((label, value.strip()) for label, raw in rows if (
        value := str(raw or "").strip()
    ))
    if not values:
        return None
    frame = QFrame(parent)
    frame.setProperty("characterMoreInfo", True)
    frame.setStyleSheet(themed_style(
        "QFrame[characterMoreInfo='true']{background:transparent;border:none;}"
    ))
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 2, 0, 0)
    layout.setSpacing(5)
    toggle = QPushButton("更多信息  ▾", frame)
    toggle.setObjectName("characterMoreInfoToggle")
    toggle.setCheckable(True)
    toggle.setStyleSheet(themed_style(
        "QPushButton#characterMoreInfoToggle{background:transparent;"
        "color:#8b949e;border:1px solid #30363d;border-radius:8px;"
        "padding:4px 9px;text-align:left;font-size:10px;font-weight:700;}"
        "QPushButton#characterMoreInfoToggle:checked{color:#58a6ff;"
        "border-color:#58a6ff;}"
    ))
    content = QFrame(frame)
    content.setObjectName("characterMoreInfoContent")
    content.setStyleSheet(themed_style(
        "QFrame#characterMoreInfoContent{background:#0d1117;"
        "border:1px solid #30363d;border-radius:8px;}"
    ))
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(9, 7, 9, 7)
    content_layout.setSpacing(5)
    for label, value in values:
        row = QLabel(f"{label}  ·  {value}", content)
        row.setWordWrap(True)
        row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.setStyleSheet(themed_style(
            "color:#8b949e;background:transparent;border:none;font-size:10px"
        ))
        content_layout.addWidget(row)
    content.setVisible(False)

    def toggle_content(expanded: bool) -> None:
        content.setVisible(expanded)
        toggle.setText("更多信息  ▴" if expanded else "更多信息  ▾")

    toggle.toggled.connect(toggle_content)
    layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
    layout.addWidget(content)
    return frame
