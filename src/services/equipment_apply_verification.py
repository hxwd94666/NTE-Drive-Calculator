# 将极速装配稳定快照的差异诊断集中在纯函数中，供单角色与批量流程复用。
"""Pure verification helpers for equipment-apply snapshots."""

from __future__ import annotations

from src.i18n import tr

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
            return tr("卡带 UID {uid} 不在复核快照中", uid=core_pair)
        if not verified_core["equipped"]:
            return tr("卡带 UID {uid} 未装备", uid=core_pair)
        if verified_core["equipped_character_id"] != character_id:
            return (
                tr("卡带 UID {uid} 装到了角色 {actual}，目标角色为 {expected}",
                   uid=core_pair, actual=verified_core["equipped_character_id"], expected=character_id)
            )
        if verified_core["equipped_character_uid"] != character_uid:
            return (
                tr("卡带 UID {uid} 的角色实例不一致：实际 {actual}，目标 {expected}",
                   uid=core_pair, actual=verified_core["equipped_character_uid"], expected=character_uid)
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
            tr("角色装备集合与方案不一致：缺少 {missing}，额外 {unexpected}",
               missing=missing or tr("无"), unexpected=unexpected or tr("无"))
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
    """Check only whether planned items are equipped in a residual packet.

    Residual packets do not reliably carry target placement or owner fields.
    They are therefore suitable for detecting a missing equipment operation,
    not for asserting an exact role/grid loadout.
    """

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
            return tr("装备 UID {uid} 未装备", uid=uid_pair)
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
            return tr("驱动 UID {uid} 不在复核快照中", uid=uid_pair)
        if not item["equipped"]:
            return tr("驱动 UID {uid} 未装备", uid=uid_pair)
        if item["equipped_character_id"] != character_id:
            return (
                tr("驱动 UID {uid} 装到了角色 {actual}，目标角色为 {expected}",
                   uid=uid_pair, actual=item["equipped_character_id"], expected=character_id)
            )
        if item["equipped_character_uid"] != character_uid:
            return (
                tr("驱动 UID {uid} 的角色实例不一致：实际 {actual}，目标 {expected}",
                   uid=uid_pair, actual=item["equipped_character_uid"], expected=character_uid)
            )
        if ignore_placement:
            continue
        actual_placement = item["equipped_placement"]
        if actual_placement is None and allow_missing_placement:
            # Equipment residual packets reliably identify the equipped item
            # and owner, but may omit grid placement.  Treat that as a
            # successful scoped state observation rather than a false repair.
            continue
        if actual_placement != expected_placement:
            return (
                tr("驱动 UID {uid} 的位置不一致：实际 {actual}，目标 {expected}",
                   uid=uid_pair, actual=actual_placement, expected=expected_placement)
            )
    return None
