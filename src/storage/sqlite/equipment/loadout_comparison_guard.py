# 在配装写事务内校验冻结对比的目标槽位基线。
from src.storage.sqlite.user_data_support import UserDataValidationError


def assert_comparison_baseline(dao, slot_id, diff):
    if diff is None:
        return
    slot = dao.get_loadout_slot(int(slot_id))
    baseline_slot = diff.get("baseline_slot_id")
    plan = (slot or {}).get("current_plan") or {}
    if (slot is None or slot.get("is_archived")
            or (baseline_slot is not None and int(baseline_slot) != int(slot_id))
            or plan.get("plan_id") != diff.get("baseline_plan_id")
            or plan.get("source_snapshot_id") != diff.get("baseline_snapshot_id")):
        raise UserDataValidationError("目标配装方案已改变，请重新计算后保存。")
