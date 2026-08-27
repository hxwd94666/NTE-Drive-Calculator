# 提供战报长页复用的分区和表格组件。
"""Shared presentation builders for the battle report analysis page."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QFrame,
    QHeaderView,
    QLabel,
    QScrollArea,
    QScrollBar,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style


def _conflict_environment_name(analysis: object, condition: object) -> str:
    name = str(
        getattr(condition, "target_name", "")
        or getattr(analysis, "detected_environment_name", "")
        or "已推断环境"
    ).strip()
    if getattr(condition, "environment_kind", "") != "feast":
        return name
    parts = tuple(part.strip() for part in name.split("·") if part.strip())
    return " · ".join(parts[:2]) if len(parts) >= 2 else name


def _highest_max_hp_target_name(analysis: object) -> str:
    resolved = []
    for index, row in enumerate(
        tuple(getattr(analysis, "target_instance_resolutions", ()) or ())
    ):
        target_condition = getattr(row, "target_condition", None)
        name = str(getattr(target_condition, "target_name", "") or "").strip()
        if name and name != "未知目标":
            resolved.append((float(getattr(row, "initial_max_hp", 0.0)), -index, name))
    if resolved:
        return max(resolved)[2]
    targets = []
    for index, row in enumerate(tuple(getattr(analysis, "targets", ()) or ())):
        name = str(getattr(row, "target_name", "") or "").strip()
        max_hp = getattr(row, "max_hp", None)
        if name and name != "未知目标" and max_hp is not None:
            targets.append((float(max_hp), -index, name))
    return "" if not targets else max(targets)[2]


def apply_inferred_scope_warning(
    label: QLabel,
    analysis: object,
    condition: object,
) -> None:
    """Keep a residual-selected scope visible while marking weak evidence."""

    inferred_source = getattr(condition, "source_kind", "") == (
        "inferred_encounter_hp_injective_default"
    )
    detected = condition is None and bool(
        getattr(analysis, "detected_environment_kind", "")
    )
    confidence = str(
        getattr(analysis, "target_identity_inference_confidence", "") or "低"
    )
    ambiguous = bool(
        getattr(analysis, "target_identity_inference_ambiguous", False)
    )
    if not (inferred_source or detected) or not (ambiguous or confidence == "低"):
        return
    environment_name = _conflict_environment_name(analysis, condition)
    target_name = _highest_max_hp_target_name(analysis)
    suffix = (
        ""
        if not target_name or target_name in environment_name
        else f" · {target_name}"
    )
    label.setText(f"候选冲突：{environment_name}{suffix}")
    label.setStyleSheet(themed_style("color:#f85149;font-weight:700"))


def analysis_table(
    headers: tuple[str, ...],
    minimum_height: int,
    *,
    default_widths: tuple[int, ...],
) -> QTableWidget:
    if len(headers) != len(default_widths):
        raise ValueError("battle table headers and default widths must match")
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.verticalHeader().setVisible(False)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
    header = table.horizontalHeader()
    header.setMinimumSectionSize(70)
    header.setStretchLastSection(False)
    for column, width in enumerate(default_widths):
        header.setSectionResizeMode(column, QHeaderView.Interactive)
        table.setColumnWidth(column, width)
    table.setMinimumHeight(minimum_height)
    return table


def analysis_section(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    heading = QLabel(title)
    heading.setObjectName("cardTitle")
    layout.addWidget(heading)
    if description:
        label = QLabel(description)
        label.setWordWrap(True)
        label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(label)
    return card, layout


def ancestor_vertical_scroll_positions(
    widget: QWidget,
) -> tuple[tuple[QScrollBar, int], ...]:
    positions: list[tuple[QScrollBar, int]] = []
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            scrollbar = parent.verticalScrollBar()
            positions.append((scrollbar, scrollbar.value()))
        parent = parent.parentWidget()
    return tuple(positions)


def restore_vertical_scroll_positions(
    positions: tuple[tuple[QScrollBar, int], ...],
) -> None:
    for scrollbar, value in positions:
        try:
            scrollbar.setValue(min(value, scrollbar.maximum()))
        except RuntimeError:
            # 延迟分页恢复执行前，所属页面可能已被 Qt 销毁。
            continue
