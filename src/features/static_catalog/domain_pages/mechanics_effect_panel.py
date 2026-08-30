# 战斗机制图鉴的效果与 Buff 详情面板。
"""Player-facing effect and Buff detail panel."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.mechanics_widgets import (
    FieldCard,
)
from src.features.static_catalog.contracts import CatalogLink
from src.services.static_catalog_mechanics_service import MechanicsDetail


class MechanicsEffectPanel(QWidget):
    def __init__(
        self,
        detail: MechanicsDetail,
        open_link: Callable[[CatalogLink], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.detail = detail
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        if detail.notice:
            notice = QLabel(detail.notice, self)
            notice.setObjectName("mechanicsPlayerNotice")
            notice.setWordWrap(True)
            notice.setStyleSheet(themed_style(
                "QLabel#mechanicsPlayerNotice{color:#e3b341;background:#161b22;"
                "border:1px solid #d29922;border-radius:10px;padding:9px 11px;"
                "font-size:10px;font-weight:700;}"
            ))
            root.addWidget(notice)

        for section in detail.sections:
            root.addWidget(FieldCard(section.title, section.fields, parent=self))
        del open_link
        root.addStretch(1)
