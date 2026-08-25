"""Read-only presentation for battle-derived character facts."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import QLabel

from src.domain.battle_report import BattleInferredCharacterFact


class BattleInferredFactLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self.setWordWrap(True)
        self.hide()

    def render_facts(
        self,
        facts: Sequence[BattleInferredCharacterFact],
    ) -> None:
        if not facts:
            self.clear_facts()
            return
        self.setText(
            "推断事实（默认用于本场计算，不改写觉醒选择）："
            + "；".join(
                f"角色 {fact.character_id} · {fact.fact_value} · "
                f"{fact.source_gameplay_effect_id} · 置信度{fact.confidence}"
                for fact in facts
            )
        )
        self.show()

    def clear_facts(self) -> None:
        self.clear()
        self.hide()
