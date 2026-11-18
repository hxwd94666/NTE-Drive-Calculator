# 装配方案的人读摘要与截图核验：只读方案与截图，不派发输入、不修改游戏状态。
"""摘要与核验是执行前后的只读检查；目标位置是否已放置仅凭亮度采样判断，不替代整张图纸校验。"""
from __future__ import annotations

from typing import Any

import numpy as np

from src.domain.localization.catalog import localized_text
from src.ui.common.enum_text import ui_enum_text


def verify_blueprint_against_screenshot(
    image: np.ndarray,
    rect: Any,
    role_plan: dict[str, Any],
    sample_radius: int = 4,
    brightness_threshold: float = 22.0,
) -> dict[str, Any]:
    """Check that expected drive block target positions look occupied in a screenshot."""

    if image is None or image.size == 0:
        return {"ok": False, "reason": "empty_screenshot", "missing_blocks": []}
    missing: list[dict[str, Any]] = []
    for block in role_plan.get("drive_blocks", []) or []:
        position = block.get("pixel_position")
        if not position:
            continue
        x = int(position[0]) - int(getattr(rect, "left", 0))
        y = int(position[1]) - int(getattr(rect, "top", 0))
        if not _sample_position_looks_occupied(image, x, y, sample_radius, brightness_threshold):
            drive = block.get("drive") if isinstance(block.get("drive"), dict) else {}
            missing_block = {
                "block_id": block.get("block_id"),
                "position": tuple(position),
            }
            if _is_duplicate_drive_block(block):
                missing_block.update(
                    {
                        "is_duplicate_drive": True,
                        "duplicate_count": block.get("duplicate_count") or drive.get("duplicate_count"),
                        "shape_id": block.get("drive_type") or drive.get("shape_id"),
                    }
                )
            missing.append(missing_block)
    return {"ok": not missing, "missing_blocks": missing}


def summarize_assembly_plan(plan: dict[str, Any], *, text_catalog=None) -> str:
    """Return a concise human-readable plan summary."""

    if "role_plans" in plan:
        lines = [localized_text(
            text_catalog,
            "drive_assembly.session.summary.roles",
            "可装配角色：{ready}/{total}",
            ready=plan.get('ready_count', 0),
            total=plan.get('role_count', 0),
        )]
        for role_plan in plan.get("role_plans", []):
            if role_plan.get("available"):
                lines.append(
                    localized_text(
                        text_catalog,
                        "drive_assembly.session.summary.role_counts",
                        "- {role}：卡带 {tape}，驱动 {drive}",
                        role=role_plan['role_name'],
                        tape=role_plan.get('tape_count', 0),
                        drive=role_plan.get('drive_count', 0),
                    )
                )
            else:
                lines.append(localized_text(
                    text_catalog,
                    "drive_assembly.session.summary.role_reason",
                    "- {role}：{reason}",
                    role=role_plan['role_name'],
                    reason=role_plan.get('reason', ui_enum_text(text_catalog, "不可装配")),
                ))
        return "\n".join(lines)
    if not plan.get("available"):
        return localized_text(
            text_catalog,
            "drive_assembly.session.summary.role_reason_plain",
            "{role}：{reason}",
            role=plan.get('role_name', ui_enum_text(text_catalog, "角色")),
            reason=plan.get('reason', ui_enum_text(text_catalog, "不可装配")),
        )
    return localized_text(
        text_catalog,
        "drive_assembly.session.summary.role_counts_plain",
        "{role}：卡带 {tape}，驱动 {drive}",
        role=plan['role_name'],
        tape=plan.get('tape_count', 0),
        drive=plan.get('drive_count', 0),
    )


def _sample_position_looks_occupied(
    image: np.ndarray,
    x: int,
    y: int,
    radius: int,
    brightness_threshold: float,
) -> bool:
    height, width = image.shape[:2]
    x1 = max(0, min(width, x - radius))
    x2 = max(0, min(width, x + radius + 1))
    y1 = max(0, min(height, y - radius))
    y2 = max(0, min(height, y + radius + 1))
    if x1 >= x2 or y1 >= y2:
        return False
    patch = image[y1:y2, x1:x2]
    return float(np.mean(patch)) >= brightness_threshold


def _is_duplicate_drive_block(block: dict[str, Any]) -> bool:
    raw_drive = block.get("drive")
    drive: dict[str, Any] = raw_drive if isinstance(raw_drive, dict) else {}
    return bool(
        block.get("is_duplicate_drive")
        or block.get("is_duplicate_equipment")
        or drive.get("is_duplicate_drive")
        or drive.get("is_duplicate_equipment")
        or int(block.get("duplicate_count") or 0) > 1
        or int(drive.get("duplicate_count") or 0) > 1
    )
