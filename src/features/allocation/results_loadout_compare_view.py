# 展示计算结果与既有配装方案的对比信息。
"""Render game-observed versus saved-calculator attribute comparisons."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.features.allocation.results_bonus_view import (
    _bonus_more_button,
    _configure_bonus_more_button,
)


class EquipmentLoadoutComparisonPresentationMixin:
    """Public presentation surface for saved-plan diffs and cross-mode stats."""

    def plan_diff_dialog(self, role_name: str, diff: dict):
        return self._build_plan_diff_dialog(role_name, diff)

    def role_loadout_comparison_panel(
        self,
        role_name,
        game_tape,
        game_drives,
        saved_tape,
        saved_drives,
        priority_stats=None,
        header_control: QWidget | None = None,
    ):
        priority_stats = list(
            priority_stats
            if priority_stats is not None
            else self._role_stat_priority_stats(role_name)
        )
        state = {"mode": "equipment"}
        box = QFrame()
        box.setObjectName("gameCalculationBonusComparison")
        box.setMinimumWidth(560)
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        box.setStyleSheet(
            themed_style(
                "QFrame{background:#0d1117;border:1px solid #30363d;"
                "border-radius:8px;padding:6px}"
            )
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(4)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        mode_switch = self._make_bonus_mode_switch(
            state["mode"],
            lambda mode: self._refresh_loadout_comparison_panel(
                box,
                role_name,
                game_tape,
                game_drives,
                saved_tape,
                saved_drives,
                priority_stats,
                mode,
            ),
        )
        mode_switch.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        header.addWidget(mode_switch, 0, Qt.AlignLeft)
        more_button = _bonus_more_button()
        more_button.setVisible(False)
        header.addWidget(more_button)
        header.addStretch()
        if header_control is not None:
            header.addWidget(header_control, 0, Qt.AlignRight)
        layout.addLayout(header)
        content_host = QWidget()
        content_layout = QVBoxLayout(content_host)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)
        layout.addWidget(content_host)
        box._bonus_summary_content_layout = content_layout
        box._bonus_summary_more_button = more_button
        box._bonus_summary_state = state
        self._refresh_loadout_comparison_panel(
            box,
            role_name,
            game_tape,
            game_drives,
            saved_tape,
            saved_drives,
            priority_stats,
            state["mode"],
        )
        layout.addStretch()
        return box

    def _refresh_loadout_comparison_panel(
        self,
        box,
        role_name,
        game_tape,
        game_drives,
        saved_tape,
        saved_drives,
        priority_stats,
        mode,
    ) -> None:
        box._bonus_summary_state["mode"] = mode
        content_layout = box._bonus_summary_content_layout
        self._clear_layout_widgets(content_layout)
        game_rows = self._sort_bonus_rows_for_role(
            role_name,
            self._bonus_rows_for_mode(role_name, game_tape, game_drives, mode),
            mode,
        )
        saved_rows = self._sort_bonus_rows_for_role(
            role_name,
            self._bonus_rows_for_mode(role_name, saved_tape, saved_drives, mode),
            mode,
        )
        content_layout.addWidget(
            self._bonus_comparison_widget(
                role_name,
                game_rows,
                saved_rows,
                has_old=True,
                compact=True,
                priority_stats=priority_stats,
                mode=mode,
                old_title="游戏",
                new_title="计算",
            )
        )
        _configure_bonus_more_button(
            box._bonus_summary_more_button,
            lambda checked=False: self._show_bonus_comparison_dialog(
                role_name,
                game_rows,
                saved_rows,
                list(priority_stats),
                mode,
                old_title="游戏",
                new_title="计算",
            ),
        )
