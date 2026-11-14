# 规范化显式槽位选择并复核冻结的current身份；不存在与空选择都不补成主力。
from collections.abc import Mapping


def freeze_slot_selection(mapping):
    if mapping is None:
        return None
    if not isinstance(mapping, Mapping):
        raise ValueError('恢复槽位选择必须是角色到槽位的mapping')
    rows = []
    for cid, slot in mapping.items():
        if type(cid) is not int or type(slot) is not int or cid < 1 or slot < 1:
            raise ValueError('恢复槽位必须包含正式角色ID与槽位ID')
        rows.append((cid, slot))
    return tuple(sorted(rows))


def assert_slot_bindings_current(dao, bindings):
    for binding in bindings:
        slot = dao.get_loadout_slot(binding.slot_id)
        plan = (slot or {}).get('current_plan') or {}
        payload = plan.get('payload') or {}
        if (slot is None or slot.get('is_archived') or plan.get('allocation_locked')
                or slot['character_id'] != binding.character_id
                or slot['current_plan_id'] != binding.plan_id
                or plan.get('slot_id') != binding.slot_id
                or plan.get('character_id') != binding.character_id
                or plan.get('source_snapshot_id') != binding.source_snapshot_id
                or payload.get('profile_id') != binding.profile_id
                or payload.get('profile_version') != binding.profile_version):
            raise RuntimeError('所选配装槽位或方案身份已变化，请重新读取保存方案。')


def assert_binding_comparisons(bindings, comparisons):
    for binding in bindings:
        row = next((row for row in comparisons.get(binding.character_id, ())
                    if row.slot_id == binding.slot_id), None)
        diff = row.diff if row is not None else {}
        if (diff.get('baseline_slot_id') != binding.slot_id
                or diff.get('baseline_plan_id') != binding.plan_id
                or diff.get('baseline_snapshot_id') != binding.source_snapshot_id):
            raise RuntimeError('恢复方案与冻结的配装比较基线不一致，请重新读取。')
