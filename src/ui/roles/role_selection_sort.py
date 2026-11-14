# 统一角色选择框的可见分数排序，按分数排序时未知置后，身份只用于稳定同分顺序。
from __future__ import annotations

import math

from PySide6.QtWidgets import QComboBox

from src.domain.names.role_name_order import role_name_sort_key
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog


class RoleSelectionSort(QComboBox):
    def __init__(self, parent=None, *, catalog=None):
        super().__init__(parent)
        text = resolve_text_catalog(catalog).render
        self.setAccessibleName(text("selector.role_sort"))
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(8)
        self.setMaximumWidth(210)
        self.currentTextChanged.connect(lambda text: self.setToolTip(plain_tooltip(text)))
        self.addItem(text("selector.sort_desc"), "score_desc")
        self.addItem(text("selector.sort_asc"), "score_asc")
        self.addItem(text("selector.sort_name"), "name")

    def sort_key(self, score, name, identity):
        name_key = (role_name_sort_key(name), identity)
        if self.currentData() == "name":
            return name_key
        known = isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score)
        value = float(score) if known else 0.0
        return (not known, -value if self.currentData() == "score_desc" else value, *name_key)
