# 格式化倒带角色选择卡的只读计算方案摘要。
"""Presentation helpers for rewind role-selection cards."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QToolButton

from src.app.theme import GRADE_COLORS
from src.domain.allocation_rating import loadout_total_grade
from src.services.rewind_shape_recommendation_service import RewindTargetRole


def configure_rewind_role_score_card(
    card: QToolButton,
    role: RewindTargetRole,
) -> QLabel:
    """Reserve one score row and color it like complete loadouts."""

    card.setText(role.name)
    card.setProperty("rewindCalculationScore", role.calculation_score)
    card.setFixedSize(116, 132)
    label = QLabel(card)
    label.setObjectName("rewindRoleCalculationScore")
    label.setAlignment(Qt.AlignCenter)
    label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    label.setGeometry(4, 108, 108, 18)
    if role.calculation_score is None:
        label.setText("")
        card.setToolTip(role.name)
        return label
    grade = loadout_total_grade(role.calculation_score)
    color = GRADE_COLORS.get(grade, "#58a6ff")
    score = f"{role.calculation_score:.2f}".rstrip("0").rstrip(".")
    label.setText(f"最高分 {score} · {grade}")
    label.setStyleSheet(
        f"color:{color};font-size:11px;font-weight:800;"
        "border:none;background:transparent;padding:0"
    )
    card.setToolTip(
        f"{role.name}\n当前计算配装最高分：{score}（{grade}）"
    )
    return label
