# 构造基础权重页的官方额外形状只读展示行。
"""Read-only release-static shape widgets for the basic-weight page."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel


def official_shape_display_rows(window, role_data: dict) -> tuple[tuple[str, QLabel], ...]:
    tooltip = "官方角色额外形状由发行静态资源库提供，不可在基础权重页编辑。"
    shape_label = QLabel(str(role_data.get("extra_shape_label") or "静态资源库未提供"))
    shape_label.setToolTip(tooltip)

    property_labels = getattr(window, "_config_weight_property_labels", {}) or {}
    rows = []
    for property_id, raw_value in (role_data.get("extra_shape_buffs") or {}).items():
        label = str(property_labels.get(property_id) or property_id)
        rows.append(f"{label}：{float(raw_value):g}")
    bonus = QLabel("、".join(rows) if rows else "静态资源库未提供")
    bonus.setToolTip(tooltip)
    return (("额外形状标签", shape_label), ("额外形状加成", bonus))
