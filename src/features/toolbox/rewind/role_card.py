# 将倒带槽位评分投影到公共角色选择卡，不维护另一套卡片样式。
from __future__ import annotations

from src.ui.roles.role_selection_card import RoleSelectionCard
from src.features.toolbox.rewind.role_selection import rewind_role_card_presentation


class RewindRoleCard(RoleSelectionCard):
    def __init__(self, parent, role, selected_reference, portrait, *, catalog=None):
        super().__init__(parent, role.name, portrait, catalog=catalog)
        self.set_reference(role, selected_reference)

    def set_reference(self, role, selected_reference):
        display = rewind_role_card_presentation(role, selected_reference, text_catalog=getattr(self, 'text_catalog', None))
        self.selection_button.setProperty("rewindCalculationScore", display.score)
        self.selection_button.setToolTip(display.detail)
        self.selection_button.setAccessibleDescription(display.detail)
        self.set_score(display.score, text=display.score_text, grade=display.grade)
        self.slot_button.set_caption(display.slot_text, available=display.can_switch)
        self.slot_button.setToolTip(display.slot_detail)
        self.slot_button.setAccessibleName(display.slot_detail)
