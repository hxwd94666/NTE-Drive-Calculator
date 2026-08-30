# 战斗机制图鉴的效果与 Buff 详情面板。
"""Player-facing effect and Buff detail panel."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.mechanics_widgets import (
    CollapsiblePanel,
    FieldCard,
    LinkButton,
    pill,
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

        if detail.redirect_only and detail.owner_link is not None:
            root.addWidget(self._owner_card(detail, open_link))
        else:
            for section in detail.sections:
                root.addWidget(FieldCard(section.title, section.fields, parent=self))

        if detail.identity_fields:
            disclosure = CollapsiblePanel(
                "更多信息",
                "专业身份",
                expanded=False,
                parent=self,
            )
            disclosure.setProperty("identityDisclosure", True)
            disclosure.body_layout.addWidget(FieldCard(
                "可复制身份",
                detail.identity_fields,
                accent="#30363d",
                parent=disclosure.body,
            ))
            root.addWidget(disclosure)

        if detail.related_links:
            related = QFrame(self)
            related.setObjectName("mechanicsRelatedLinks")
            related.setStyleSheet(themed_style(
                "QFrame#mechanicsRelatedLinks{background:#161b22;"
                "border:1px solid #30363d;border-radius:13px;}"
            ))
            layout = QVBoxLayout(related)
            layout.setContentsMargins(13, 11, 13, 11)
            heading = QLabel("继续探索", related)
            heading.setStyleSheet(themed_style(
                "color:#f0f6fc;font-size:12px;font-weight:900;"
            ))
            layout.addWidget(heading)
            for label, link in detail.related_links:
                button = LinkButton(label, related)
                button.clicked.connect(
                    lambda _checked=False, target=link: open_link(target)
                )
                layout.addWidget(button)
            root.addWidget(related)
        root.addStretch(1)

    @staticmethod
    def _owner_card(
        detail: MechanicsDetail,
        open_link: Callable[[CatalogLink], None],
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("mechanicsOwnerCard")
        card.setStyleSheet(themed_style(
            "QFrame#mechanicsOwnerCard{background:#161b22;"
            "border:1px solid #58a6ff;border-radius:14px;}"
        ))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)
        top = QHBoxLayout()
        top.addWidget(pill("唯一归属", color="#58a6ff", parent=card))
        top.addStretch(1)
        layout.addLayout(top)
        title = QLabel(detail.owner_label, card)
        title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:17px;font-weight:900;"
        ))
        layout.addWidget(title)
        copy = QLabel("技能、层数、持续时间和目标范围在所属对象页集中阅读。", card)
        copy.setWordWrap(True)
        copy.setStyleSheet(themed_style("color:#8b949e;font-size:10px;"))
        layout.addWidget(copy)
        button = LinkButton("前往所属对象", card)
        button.clicked.connect(
            lambda _checked=False, link=detail.owner_link: open_link(link)
        )
        layout.addWidget(button, alignment=Qt.AlignLeft)
        return card
