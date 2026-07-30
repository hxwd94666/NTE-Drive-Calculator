# 生成加权配装结果中未分配角色和缺失卡带的说明文本。
"""Human-readable result explanations independent from Qt layout code."""

from __future__ import annotations

from src.services.allocation_context import AllocationContext


def unassigned_reason(
    owner,
    context: AllocationContext | None,
    character_ids: tuple[int, ...],
) -> str:
    if context is None:
        return "部分角色没有可用的完整方案。"
    names = getattr(owner, "_weighted_role_names", {})
    suits = getattr(owner, "_weighted_suit_names", {})
    attributes = getattr(owner, "_weighted_property_names", {})
    reasons = []
    for role in context.roles:
        if role.character_id not in character_ids:
            continue
        cores = [item for item in context.candidates if item.kind == "core"]
        if role.target_suit_id:
            cores = [
                item for item in cores if item.suit_id == role.target_suit_id
            ]
        if role.core_main_property_id:
            cores = [
                item
                for item in cores
                if any(
                    stat.property_id == role.core_main_property_id
                    for stat in item.main_stats
                )
            ]
        if not cores:
            suit = suits.get(
                role.target_suit_id, role.target_suit_id or "任意套装"
            )
            attribute = attributes.get(
                role.core_main_property_id,
                role.core_main_property_id or "任意主词条",
            )
            reasons.append(
                f"{names.get(role.character_id, role.character_id)}："
                f"缺少 {suit}＋{attribute} 主词条卡带"
            )
        else:
            reasons.append(
                f"{names.get(role.character_id, role.character_id)}："
                "缺少可组成完整图纸的驱动"
            )
    return "；".join(reasons)


def missing_core_text(owner, role, reason: str | None = None) -> str:
    if reason:
        return f"卡带缺失：{reason}（驱动图纸已匹配，方案将按不完整状态保存）"
    if role is None:
        return "卡带未分配"
    suits = getattr(owner, "_weighted_suit_names", {})
    attributes = getattr(owner, "_weighted_property_names", {})
    suit = suits.get(role.target_suit_id, role.target_suit_id or "任意套装")
    attribute = attributes.get(
        role.core_main_property_id,
        role.core_main_property_id or "任意主词条",
    )
    return (
        f"卡带缺失：缺少 {suit}＋{attribute} 主词条卡带"
        "（驱动图纸已匹配）"
    )
