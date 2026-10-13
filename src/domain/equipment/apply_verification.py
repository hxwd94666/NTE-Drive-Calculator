# 纯对比装配方案与完整或局部观测，保留UID、角色实例和格位的未知边界。
"""完整快照与局部片段使用各自事实范围进行纯差异判断。"""

from __future__ import annotations

from typing import Any


def plan_mismatch(
    *,
    items: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    core_assignment: dict[str, Any] | None,
    character_id: int,
    character_uid: dict[str, int],
    ignore_module_placement: bool = False,
) -> str | None:
    """返回完整方案与稳定快照的首个具体差异。"""

    expected_uids = {
        (assignment["uid_serial"], assignment["uid_slot"])
        for assignment in modules
    }
    if core_assignment is not None:
        expected_uids.add((
            core_assignment["uid_serial"],
            core_assignment["uid_slot"],
        ))

    mismatch = module_plan_mismatch(
        items=items,
        modules=modules,
        character_id=character_id,
        character_uid=character_uid,
        ignore_placement=ignore_module_placement,
    )
    if mismatch is not None:
        return mismatch

    if core_assignment is not None:
        core_pair = (core_assignment["uid_serial"], core_assignment["uid_slot"])
        by_uid = {(item["uid_serial"], item["uid_slot"]): item for item in items}
        verified_core = by_uid.get(core_pair)
        if verified_core is None:
            return f"卡带 UID {core_pair} 不在复核快照中"
        if not verified_core["equipped"]:
            return f"卡带 UID {core_pair} 未装备"
        if verified_core["equipped_character_id"] != character_id:
            return (
                f"卡带 UID {core_pair} 装到了角色 "
                f"{verified_core['equipped_character_id']}，目标角色为 {character_id}"
            )
        if verified_core["equipped_character_uid"] != character_uid:
            return (
                f"卡带 UID {core_pair} 的角色实例不一致："
                f"实际 {verified_core['equipped_character_uid']}，目标 {character_uid}"
            )

    actual_uids = {
        (item["uid_serial"], item["uid_slot"])
        for item in items
        if item["equipped"]
        and item["equipped_character_uid"] == character_uid
        and item["equipped_character_id"] == character_id
    }
    if actual_uids != expected_uids:
        missing = sorted(expected_uids - actual_uids)
        unexpected = sorted(actual_uids - expected_uids)
        return (
            "角色装备集合与方案不一致："
            f"缺少 {missing or '无'}，额外 {unexpected or '无'}"
        )
    return None


def scoped_plan_mismatch(
    *,
    items: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    core_assignment: dict[str, Any] | None,
    character_id: int,
    character_uid: dict[str, int],
) -> str | None:
    """局部片段只确认已观测目标是否装备，不断言缺失的角色归属或格位。
    
    缺少目标项保持未知；只有已见目标明确未装备才返回缺失装配差异。"""

    by_uid = {(item["uid_serial"], item["uid_slot"]): item for item in items}
    assignments = [*modules]
    if core_assignment is not None:
        assignments.append(core_assignment)
    for assignment in assignments:
        uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
        item = by_uid.get(uid_pair)
        if item is None:
            continue
        if not item["equipped"]:
            return f"装备 UID {uid_pair} 未装备"
    return None


def module_plan_mismatch(
    *,
    items: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    character_id: int,
    character_uid: dict[str, int],
    allow_missing_placement: bool = False,
    ignore_placement: bool = False,
) -> str | None:
    """复核仅含驱动的方案，不要求更改角色当前卡带。"""

    by_uid = {(item["uid_serial"], item["uid_slot"]): item for item in items}
    for assignment in modules:
        uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
        item = by_uid.get(uid_pair)
        expected_placement = {
            "row": assignment["target_row"],
            "column": assignment["target_column"],
        }
        if item is None:
            return f"驱动 UID {uid_pair} 不在复核快照中"
        if not item["equipped"]:
            return f"驱动 UID {uid_pair} 未装备"
        if item["equipped_character_id"] != character_id:
            return (
                f"驱动 UID {uid_pair} 装到了角色 {item['equipped_character_id']}，"
                f"目标角色为 {character_id}"
            )
        if item["equipped_character_uid"] != character_uid:
            return (
                f"驱动 UID {uid_pair} 的角色实例不一致："
                f"实际 {item['equipped_character_uid']}，目标 {character_uid}"
            )
        if ignore_placement:
            continue
        actual_placement = item["equipped_placement"]
        if actual_placement is None and allow_missing_placement:
            # 局部事件可能省略格位；仅在显式允许时确认其已有装备/归属事实，不误发修复。
            continue
        if actual_placement != expected_placement:
            return (
                f"驱动 UID {uid_pair} 的位置不一致："
                f"实际 {actual_placement}，目标 {expected_placement}"
            )
    return None
