# 将极速装配稳定快照的差异诊断集中在纯函数中，供单角色与批量流程复用。
"""Pure verification helpers for equipment-apply snapshots."""

from __future__ import annotations

from typing import Any


def plan_mismatch(
    *,
    items: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    core_assignment: dict[str, Any] | None,
    character_id: int,
    character_uid: dict[str, int],
) -> str | None:
    """返回完整方案与稳定快照的首个具体差异。"""

    by_uid = {(item["uid_serial"], item["uid_slot"]): item for item in items}
    expected_uids = {
        (assignment["uid_serial"], assignment["uid_slot"])
        for assignment in modules
    }
    core_pair: tuple[int, int] | None = None
    if core_assignment is not None:
        core_pair = (
            core_assignment["uid_serial"],
            core_assignment["uid_slot"],
        )
        expected_uids.add(core_pair)

    mismatch = module_plan_mismatch(
        items=items,
        modules=modules,
        character_id=character_id,
        character_uid=character_uid,
    )
    if mismatch is not None:
        return mismatch

    if core_pair is not None:
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


def module_plan_mismatch(
    *,
    items: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    character_id: int,
    character_uid: dict[str, int],
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
        if item["equipped_placement"] != expected_placement:
            return (
                f"驱动 UID {uid_pair} 的位置不一致："
                f"实际 {item['equipped_placement']}，目标 {expected_placement}"
            )
    return None
