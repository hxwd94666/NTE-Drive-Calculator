# 战斗机制图鉴的伤害公式详情面板。
"""Formula cards, multiplier flow, and variable badges."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.mechanics_widgets import (
    FieldCard,
    LinkButton,
    pill,
)
from src.features.static_catalog.contracts import CatalogLink
from src.services.static_catalog_mechanics_service import MechanicsDetail


FLOW = (
    "面板", "倍率", "通伤", "属性伤", "防御", "抗性", "暴击", "专属结算",
)


class MechanicsFormulaPanel(QWidget):
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

        root.addWidget(self._flow_card(detail))
        for section in detail.sections:
            accent = "#d2a8ff" if section.title == "公式" else "#30363d"
            root.addWidget(FieldCard(section.title, section.fields, accent=accent, parent=self))
        if detail.related_links:
            links = QFrame(self)
            links.setObjectName("formulaVariableLinks")
            links.setStyleSheet(themed_style(
                "QFrame#formulaVariableLinks{background:#161b22;"
                "border:1px solid #30363d;border-radius:13px;}"
            ))
            layout = QVBoxLayout(links)
            layout.setContentsMargins(13, 11, 13, 11)
            title = QLabel("从变量追到效果", links)
            title.setStyleSheet(themed_style(
                "color:#f0f6fc;font-size:12px;font-weight:900;"
            ))
            layout.addWidget(title)
            for label, link in detail.related_links:
                button = LinkButton(label, links)
                button.clicked.connect(
                    lambda _checked=False, target=link: open_link(target)
                )
                layout.addWidget(button)
            root.addWidget(links)
        root.addStretch(1)

    @staticmethod
    def _flow_card(detail: MechanicsDetail) -> QFrame:
        active = detail.badges[0] if detail.badges else ""
        card = QFrame()
        card.setObjectName("formulaFlowCard")
        card.setStyleSheet(themed_style(
            "QFrame#formulaFlowCard{background:#0d1117;"
            "border:1px solid #30363d;border-radius:13px;}"
        ))
        root = QVBoxLayout(card)
        root.setContentsMargins(13, 10, 13, 10)
        label = QLabel("伤害乘区流程", card)
        label.setStyleSheet(themed_style(
            "color:#8b949e;font-size:9px;font-weight:900;letter-spacing:1px;"
        ))
        root.addWidget(label)
        flow = QHBoxLayout()
        flow.setSpacing(4)
        for index, name in enumerate(FLOW):
            if index:
                arrow = QLabel("›", card)
                arrow.setStyleSheet(themed_style("color:#484f58;font-weight:900;"))
                flow.addWidget(arrow)
            color = "#d2a8ff" if name == active or (
                active in {"DOT", "倾陷", "特殊机制"} and name == "专属结算"
            ) else "#8b949e"
            flow.addWidget(pill(name, color=color, parent=card))
        flow.addStretch(1)
        root.addLayout(flow)
        return card
