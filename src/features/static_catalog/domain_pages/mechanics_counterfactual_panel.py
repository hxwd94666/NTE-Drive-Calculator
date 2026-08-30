# 战斗机制图鉴的反事实覆盖详情面板。
"""Counterfactual status cards with a collapsed evidence chain."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.mechanics_widgets import (
    CollapsiblePanel,
    FieldCard,
    LinkButton,
    status_pill,
)
from src.features.static_catalog.contracts import CatalogLink
from src.services.static_catalog_mechanics_service import MechanicsDetail


class MechanicsCounterfactualPanel(QWidget):
    def __init__(
        self,
        detail: MechanicsDetail,
        open_link: Callable[[CatalogLink], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        root.addWidget(self._status_card(detail))
        for section in detail.sections:
            root.addWidget(FieldCard(section.title, section.fields, parent=self))
        if detail.evidence_stages:
            disclosure = CollapsiblePanel(
                "证据链",
                "正式定义 → 触发 → 状态轴 → 逐击 → 公式 → 生产反事实",
                expanded=False,
                parent=self,
            )
            disclosure.setProperty("evidenceChain", True)
            for index, stage in enumerate(detail.evidence_stages, start=1):
                row = QFrame(disclosure.body)
                row.setObjectName("evidenceStageCard")
                row.setStyleSheet(themed_style(
                    "QFrame#evidenceStageCard{background:#161b22;"
                    "border:1px solid #30363d;border-radius:9px;}"
                ))
                layout = QHBoxLayout(row)
                layout.setContentsMargins(10, 8, 10, 8)
                number = QLabel(f"{index:02d}", row)
                number.setStyleSheet(themed_style(
                    "color:#58a6ff;font-size:10px;font-weight:900;"
                ))
                layout.addWidget(number)
                copy = QVBoxLayout()
                title = QLabel(stage.label, row)
                title.setStyleSheet(themed_style(
                    "color:#f0f6fc;font-size:10px;font-weight:900;"
                ))
                summary = QLabel(stage.summary, row)
                summary.setWordWrap(True)
                summary.setStyleSheet(themed_style("color:#8b949e;font-size:9px;"))
                copy.addWidget(title)
                copy.addWidget(summary)
                layout.addLayout(copy, 1)
                layout.addWidget(status_pill(stage.status, row))
                disclosure.body_layout.addWidget(row)
            root.addWidget(disclosure)

        if detail.related_links:
            for label, link in detail.related_links:
                button = LinkButton(label, self)
                button.clicked.connect(
                    lambda _checked=False, target=link: open_link(target)
                )
                root.addWidget(button)
        root.addStretch(1)

    @staticmethod
    def _status_card(detail: MechanicsDetail) -> QFrame:
        card = QFrame()
        card.setObjectName("counterfactualStatusCard")
        card.setStyleSheet(themed_style(
            "QFrame#counterfactualStatusCard{background:#161b22;"
            "border:1px solid #30363d;border-radius:13px;}"
        ))
        layout = QHBoxLayout(card)
        layout.setContentsMargins(13, 10, 13, 10)
        copy = QVBoxLayout()
        label = QLabel("战报反事实覆盖", card)
        label.setStyleSheet(themed_style(
            "color:#8b949e;font-size:9px;font-weight:900;letter-spacing:1px;"
        ))
        notice = QLabel(detail.notice or "尚未接入生产反事实", card)
        notice.setWordWrap(True)
        notice.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:11px;font-weight:800;"
        ))
        copy.addWidget(label)
        copy.addWidget(notice)
        layout.addLayout(copy, 1)
        if detail.status:
            layout.addWidget(status_pill(detail.status, card))
        return card
