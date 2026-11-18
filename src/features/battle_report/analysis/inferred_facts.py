# 展示战报派生角色事实的只读视图。
"""Read-only presentation for battle-derived character facts."""

from __future__ import annotations

from src.ui.common.text_catalog import resolve_text_catalog
from collections.abc import Sequence

from PySide6.QtWidgets import QLabel

from src.domain.battle_report.build.inputs import BattleInferredCharacterFact


class BattleInferredFactLabel(QLabel):
    def __init__(self, *, text_catalog=None) -> None:
        super().__init__()
        self.text_catalog = resolve_text_catalog(text_catalog)
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
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.render_facts.self')
            + "；".join(
                resolve_text_catalog(getattr(self, 'text_catalog', None)).render(
                    "battle_report.inferred_fact.row", character_id=f"{fact.character_id}",
                    value=f"{fact.fact_value}", effect=f"{fact.source_gameplay_effect_id}",
                    confidence=f"{fact.confidence}")
                for fact in facts
            )
        )
        self.show()

    def clear_facts(self) -> None:
        self.clear()
        self.hide()
