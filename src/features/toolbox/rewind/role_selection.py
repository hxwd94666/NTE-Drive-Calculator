# 格式化倒带角色卡的评分、槽位名称与完整提示。
from __future__ import annotations

from dataclasses import dataclass

from src.domain.allocation_rating import loadout_total_grade
from src.domain.rewind_loadout import RewindSlotReference, finite_score
from src.domain.role_name_order import role_name_sort_key
from src.services.rewind.recommendation import RewindTargetRole


@dataclass(frozen=True, slots=True)
class RewindRoleCardPresentation:
    score: float | None
    score_text: str
    grade: str
    slot_text: str
    detail: str
    slot_detail: str
    can_switch: bool


def rewind_role_sort_key(role: RewindTargetRole):
    scores = [score for slot in role.slots if (score := finite_score(slot.score)) is not None]
    highest = max(scores) if scores else None
    return (highest is None, -highest if highest is not None else 0,
            role_name_sort_key(role.name), role.character_id)


def rewind_role_card_presentation(
    role: RewindTargetRole, selected_reference: RewindSlotReference | None,
) -> RewindRoleCardPresentation:
    slot = next((row for row in role.slots if row.reference == selected_reference), None)
    score = slot.score if slot else None
    grade = loadout_total_grade(score) if score is not None else ""
    if score is not None:
        score_text = f"{score:.2f}".rstrip("0").rstrip(".")
        if len(score_text) > 10:
            score_text = f"{score:.3g}"
    elif not role.slots or (slot and slot.state == "empty"):
        score_text = "暂无方案"
    else:
        score_text = "评分资料不足" if slot else "待选槽位"
    if not role.slots:
        caption = "无可用配装"
    elif slot is None:
        caption = "选择槽位"
    else:
        caption = slot.slot_name
    if slot is not None:
        exact_score = f"{score:g}分（{grade}）" if score is not None else score_text
        detail = f"{role.name}\n{slot.slot_name}：{exact_score}\n{slot.reason}"
    else:
        detail = f"{role.name}\n{score_text}\n{caption}"
    slot_detail = f"切换 {role.name} 的配装槽位\n{detail}" if role.slots else detail
    return RewindRoleCardPresentation(score, score_text, grade, caption, detail, slot_detail, bool(role.slots))


# 已保存的阈值与角色选择同属倒带草稿，拒绝bool和越界值，不另设13行转发入口。
def preference_custom_percent(value: object) -> float | None:
    """Read a persisted optional custom rewind threshold without trusting old data."""

    if isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float)):
        return None
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return None
    return percent if 1.0 <= percent <= 100.0 else None
