# 战斗机制图鉴的伤害公式详情面板。
"""Formula cards, multiplier flow, and variable badges."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.mechanics_widgets import (
    FieldCard,
)
from src.features.static_catalog.contracts import CatalogLink
from src.services.static_catalog_mechanics_service import MechanicsDetail


FLOW = (
    "来源输入", "状态/层数", "面板或等级基础", "增伤/专属区",
    "防御", "抗性", "暴击策略", "最终结算",
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
        if detail.notice:
            notice = QLabel(detail.notice, self)
            notice.setObjectName("mechanicsFormulaNotice")
            notice.setWordWrap(True)
            notice.setStyleSheet(themed_style(
                "QLabel#mechanicsFormulaNotice{color:#e3b341;background:#241f12;"
                "border:1px solid #9e6a03;border-radius:10px;padding:10px 12px;"
                "font-size:12px;font-weight:750;}"
            ))
            root.addWidget(notice)
        for section in detail.sections:
            accent = "#d2a8ff" if section.title == "完整公式" else "#30363d"
            root.addWidget(FieldCard(section.title, section.fields, accent=accent, parent=self))
        del open_link
        root.addStretch(1)

    @staticmethod
    def _flow_card(detail: MechanicsDetail) -> QFrame:
        active = detail.badges[0] if detail.badges else ""
        card = QFrame()
        card.setObjectName("formulaFlowCard")
        card.setStyleSheet(themed_style(
            "QFrame#formulaFlowCard{background:transparent;"
            "border:0;border-bottom:1px solid #30363d;}"
        ))
        root = QVBoxLayout(card)
        root.setContentsMargins(13, 10, 13, 10)
        label = QLabel("伤害乘区流程", card)
        label.setStyleSheet(themed_style(
            "color:#8b949e;font-size:11px;font-weight:900;letter-spacing:1px;"
        ))
        root.addWidget(label)
        highlighted = {
            "持续伤害": "状态/层数",
            "持续直伤": "状态/层数",
            "环合基础": "面板或等级基础",
            "环合": "增伤/专属区",
            "倾陷": "增伤/专属区",
            "独立增伤": "增伤/专属区",
            "最终取整": "最终结算",
            "生命结算": "最终结算",
            "共享伤害": "最终结算",
        }.get(active, "")
        names = [f"【{name}】" if name == highlighted else name for name in FLOW]
        flow = QLabel("  →  ".join(names), card)
        flow.setWordWrap(True)
        flow.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:12px;font-weight:700;"
        ))
        root.addWidget(flow)
        return card
