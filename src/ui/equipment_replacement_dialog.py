# 角色与词条配装共用的卡片式装备替换比较窗口。
"""Shared warehouse-card replacement picker with explicit confirmation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QBoxLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.inventory.warehouse import WarehouseResultCard


@dataclass(frozen=True, slots=True)
class EquipmentReplacementCard:
    key: str
    item_view: Mapping[str, Any]
    score: float | None
    grade: str | None
    direct_damage_score: float | None
    payload: Any
    note: str = ""


def _stat_key(stat: Mapping[str, Any]) -> str:
    return str(stat.get("property_id") or stat.get("label") or "")


def _stat_numeric_value(stat: Mapping[str, Any] | None) -> float:
    value = (stat or {}).get("value") or 0.0
    if isinstance(value, str):
        value = value.replace("+", "").replace("%", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _comparison_item_views(
    current: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Mark each displayed stat by its actual old-to-new direction.

    Both exclusive stats and the same stat with a different value are relevant:
    the current card is red for a loss and the selected card is green for a
    gain.  The former implementation reversed the sides and only handled
    exclusive property IDs, which made a replacement appear to have gains only.
    """

    old_view = deepcopy(dict(current))
    new_view = deepcopy(dict(selected))

    def stats(view: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            stat
            for stat in [
                *(view.get("main_stats") or ()),
                *(view.get("sub_stats") or ()),
            ]
            if isinstance(stat, dict) and _stat_key(stat)
        ]

    old_stats = stats(old_view)
    new_stats = stats(new_view)
    old_by_key = {_stat_key(stat): stat for stat in old_stats}
    new_by_key = {_stat_key(stat): stat for stat in new_stats}
    for key in set(old_by_key) | set(new_by_key):
        old_stat = old_by_key.get(key)
        new_stat = new_by_key.get(key)
        old_value = _stat_numeric_value(old_stat)
        new_value = _stat_numeric_value(new_stat)
        if abs(new_value - old_value) < 0.0001:
            continue
        if new_value > old_value:
            if old_stat is not None:
                old_stat["comparison_background"] = "#f85149"
            if new_stat is not None:
                new_stat["comparison_background"] = "#2ea043"
        else:
            if old_stat is not None:
                old_stat["comparison_background"] = "#2ea043"
            if new_stat is not None:
                new_stat["comparison_background"] = "#f85149"
    return old_view, new_view


def show_equipment_replacement_dialog(
    parent: QWidget,
    *,
    title: str,
    role_name: str,
    summary: str,
    current: EquipmentReplacementCard,
    candidates: list[EquipmentReplacementCard],
    on_confirm: Callable[[EquipmentReplacementCard], None],
) -> bool:
    """Show one current card, selectable candidate cards, then commit explicitly."""

    dialog = QDialog(parent)
    dialog.setObjectName("equipmentReplacementDialog")
    dialog.setWindowTitle(title)
    dialog.resize(980, 920)
    root = QVBoxLayout(dialog)
    root.setSpacing(10)

    header = QLabel(f"装配角色：{role_name}")
    header.setStyleSheet(themed_style(
        "font-size:15px;font-weight:800;color:#4dd0e1;"
        "border:1px solid #4dd0e1;border-radius:7px;padding:5px 12px;"
        "background:rgba(77,208,225,0.10)"
    ))
    root.addWidget(header)
    description = QLabel(summary)
    description.setWordWrap(True)
    description.setStyleSheet(themed_style("color:#8b949e"))
    root.addWidget(description)

    comparison_group = QGroupBox("候选装备与当前装备")
    comparison_group.setObjectName("equipmentReplacementComparisonGroup")
    comparison_group.setLayoutDirection(Qt.LeftToRight)
    comparison_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    comparison_layout = QHBoxLayout(comparison_group)
    comparison_layout.setDirection(QBoxLayout.LeftToRight)
    comparison_layout.setContentsMargins(8, 4, 8, 6)
    comparison_layout.setSpacing(10)
    current_column = QVBoxLayout()
    current_column.setSpacing(3)
    current_label = QLabel("当前装备")
    current_label.setStyleSheet(themed_style("font-weight:700;color:#c9d1d9"))
    current_column.addWidget(current_label)
    current_host = QWidget(comparison_group)
    current_host.setFixedSize(WarehouseResultCard.CARD_SIZE)
    current_host_layout = QVBoxLayout(current_host)
    current_host_layout.setContentsMargins(0, 0, 0, 0)
    current_card = WarehouseResultCard(
        current.item_view,
        score=current.score,
        grade=current.grade,
        direct_damage_score=current.direct_damage_score,
        split_metrics=True,
        parent=current_host,
    )
    current_host_layout.addWidget(current_card)
    current_column.addWidget(current_host)
    comparison_layout.addLayout(current_column)

    comparison_layout.addStretch(1)

    comparison_text = QLabel("点击下方候选卡片进行比较。")
    comparison_text.setObjectName("equipmentReplacementComparison")
    comparison_text.setAlignment(Qt.AlignCenter)
    comparison_text.setWordWrap(True)
    comparison_text.setMinimumWidth(260)
    comparison_text.setMaximumWidth(300)
    comparison_text.setStyleSheet(themed_style(
        "font-size:13px;color:#c9d1d9;padding:6px;"
    ))
    comparison_layout.addWidget(comparison_text, 0, Qt.AlignVCenter)

    comparison_layout.addStretch(1)

    selected_column = QVBoxLayout()
    selected_column.setSpacing(3)
    selected_label = QLabel("候选装备")
    selected_label.setStyleSheet(themed_style("font-weight:700;color:#c9d1d9"))
    selected_column.addWidget(selected_label)
    selected_host = QWidget(comparison_group)
    selected_host.setFixedSize(WarehouseResultCard.CARD_SIZE)
    selected_host_layout = QVBoxLayout(selected_host)
    selected_host_layout.setContentsMargins(0, 0, 0, 0)
    selected_placeholder = QLabel("点击下方卡片\n在此生成比较副本", selected_host)
    selected_placeholder.setAlignment(Qt.AlignCenter)
    selected_placeholder.setStyleSheet(themed_style(
        "color:#8b949e;border:1px dashed #484f58;border-radius:9px;"
    ))
    selected_host_layout.addWidget(selected_placeholder)
    selected_column.addWidget(selected_host)
    comparison_layout.addLayout(selected_column)
    root.addWidget(comparison_group)

    candidate_group = QGroupBox(f"候选装备 ({len(candidates)}个)")
    candidate_group.setObjectName("equipmentReplacementCandidateGroup")
    candidate_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
    candidate_group.setMinimumHeight(390)
    candidate_layout = QVBoxLayout(candidate_group)
    scroll = QScrollArea(candidate_group)
    scroll.setWidgetResizable(True)
    content = QWidget()
    grid = QGridLayout(content)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(10)
    candidate_widgets: dict[str, WarehouseResultCard] = {}
    selected: list[EquipmentReplacementCard | None] = [None]

    confirm = QPushButton("确定替换")
    confirm.setObjectName("btnAction")
    confirm.setEnabled(False)

    def select(choice: EquipmentReplacementCard) -> None:
        selected[0] = choice
        for key, card in candidate_widgets.items():
            card.set_selected(key == choice.key)
        current_view, selected_view = _comparison_item_views(
            current.item_view,
            choice.item_view,
        )
        while selected_host_layout.count():
            widget = selected_host_layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        selected_copy = WarehouseResultCard(
            selected_view,
            score=choice.score,
            grade=choice.grade,
            direct_damage_score=choice.direct_damage_score,
            split_metrics=True,
            parent=selected_host,
        )
        selected_copy.setObjectName("equipmentReplacementSelectedPreview")
        selected_host_layout.addWidget(selected_copy)
        while current_host_layout.count():
            widget = current_host_layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        compared_current = WarehouseResultCard(
            current_view,
            score=current.score,
            grade=current.grade,
            direct_damage_score=current.direct_damage_score,
            split_metrics=True,
            parent=current_host,
        )
        compared_current.setObjectName("equipmentReplacementCurrentPreview")
        current_host_layout.addWidget(compared_current)
        current_direct_text = (
            "--"
            if current.direct_damage_score is None
            else f"{float(current.direct_damage_score):.2f}%"
        )
        selected_direct_text = (
            "--"
            if choice.direct_damage_score is None
            else f"{float(choice.direct_damage_score):.2f}%"
        )
        comparison_text.setText(
            f"已选择：{choice.item_view.get('display_name') or choice.key}\n"
            f"直伤边际收益：{current_direct_text} → {selected_direct_text}"
            + (f"\n{choice.note}" if choice.note else "")
        )
        confirm.setEnabled(True)

    for index, choice in enumerate(candidates):
        card = WarehouseResultCard(
            choice.item_view,
            score=choice.score,
            grade=choice.grade,
            direct_damage_score=choice.direct_damage_score,
            split_metrics=True,
            replacement_callback=lambda value=choice: select(value),
            parent=content,
        )
        card.setToolTip("点击选择，并在上方与当前装备比较")
        candidate_widgets[choice.key] = card
        grid.addWidget(
            card,
            index // 3,
            index % 3,
            Qt.AlignLeft | Qt.AlignTop,
        )
    grid.setColumnStretch(3, 1)
    scroll.setWidget(content)
    candidate_layout.addWidget(scroll)
    root.addWidget(candidate_group, 1)

    actions = QHBoxLayout()
    actions.addStretch()
    cancel = QPushButton("取消")
    cancel.clicked.connect(dialog.reject)
    actions.addWidget(cancel)
    actions.addWidget(confirm)
    root.addLayout(actions)

    def commit() -> None:
        choice = selected[0]
        if choice is None:
            return
        try:
            on_confirm(choice)
        except Exception as exc:
            QMessageBox.warning(dialog, "替换失败", str(exc))
            return
        dialog.accept()

    confirm.clicked.connect(commit)
    return dialog.exec() == QDialog.Accepted
