# 真实提交后只按返回plan IDs核对回执，沿原冻结输入推进同槽比较游标。
from dataclasses import replace
from pathlib import Path

from src.domain.allocation.slot_binding import WeightedSaveReceipt, WeightedSlotBinding
from src.services.allocation.locks import verify_allocation_lock_snapshot
from src.services.allocation.slot_binding import assert_binding_comparisons, assert_slot_bindings_current
from src.services.equipment.weighted_loadout_comparison_service import freeze_weighted_loadout_comparisons
from src.integrations.static_catalog_release import StaticCatalogReleaseReader
from src.services.allocation.weighted_save import require_weighted_preview_release
from src.storage.sqlite.user_data_dao import UserDataDao


def _check_identity(preview, ownership, user, static):
    ownership.checkpoint()
    identity = ownership.identity
    if (preview.account_id != identity.account_id or preview.context.account_id != identity.account_id
            or Path(preview.user_database_path).resolve() != identity.user_database_path
            or user.profile()['account_id'] != identity.account_id
            or Path(static.database_path).resolve() != Path(preview.static_database_path).resolve()
):
        raise RuntimeError('保存回执的账号或静态身份已变化，请核对已保存方案。')
    StaticCatalogReleaseReader().ensure_unchanged(require_weighted_preview_release(preview))
    verify_allocation_lock_snapshot(user, preview.lock_snapshot)


def read_weighted_save_receipt(preview, plan_ids, *, ownership, slot_ids_by_character=None):
    """首槽None仅从本次真实ID解析；不读最新库存、profile或历史active补齐。"""
    ownership.checkpoint()
    if ownership.preview is not preview:
        raise RuntimeError('保存回执未绑定本次冻结预览')
    options = {row.character_id: row for row in preview.result.unified.selected}
    ids = tuple(plan_ids)
    if (not options or len(options) != len(preview.result.unified.selected)
            or len(ids) != len(options) or len(set(ids)) != len(ids)
            or any(type(value) is not int or value < 1 for value in ids)):
        raise RuntimeError('真实提交没有返回完整且唯一的方案ID')
    targets = ({row.character_id: row.slot_id for row in preview.slot_bindings}
               if preview.slot_bindings else
               dict(slot_ids_by_character) if slot_ids_by_character is not None else {
                   cid: next((row.slot_id for row in preview.loadout_comparisons.get(cid, ())
                              if row.slot_key == 'primary'), None) for cid in options})
    if set(targets) != set(options):
        raise RuntimeError('保存回执缺少冻结的目标角色')
    release = require_weighted_preview_release(preview)
    with UserDataDao(preview.user_database_path) as user, StaticCatalogReleaseReader().read_unit(
            preview.static_database_path, expected_release=release) as unit:
        static = unit.dao
        with user.read_consistent_state():
            _check_identity(preview, ownership, user, static)
            plans = user.get_loadout_plans(ids)
            if {row['plan_id'] for row in plans} != set(ids):
                raise RuntimeError('真实提交的方案ID读取不完整')
            bindings, seen = [], set()
            dataset = {name: getattr(preview.static_dataset, name) for name in
                       ('schema_version', 'dataset_id', 'importer_version', 'built_at_utc')}
            for plan in plans:
                ownership.checkpoint()
                cid, slot = plan['character_id'], plan['slot_id']
                option = options.get(cid)
                payload = plan.get('payload') or {}
                scores = ({f'nte-{row.kind}-{row.uid[0]}-{row.uid[1]}': row.score
                           for row in option.assignments} if option is not None else {})
                assignments = plan.get('assignments') or ()
                if (option is None or cid in seen or type(slot) is not int or slot < 1
                        or (targets[cid] is not None and slot != targets[cid])
                        or plan['source_snapshot_id'] != preview.result.snapshot_id
                        or plan.get('score') != option.score
                        or payload.get('schema') != 'allocation-official-snapshot-v1'
                        or payload.get('source') != 'weighted_allocation'
                        or payload.get('profile_id') != preview.result.profile_id
                        or payload.get('profile_version') != preview.result.profile_version
                        or payload.get('solver_version') != preview.result.solver_version
                        or payload.get('static_dataset') != dataset
                        or payload.get('assignment_scores') != scores
                        or len(assignments) != len(option.assignments)
                        or {f"nte-{row['kind']}-{row['uid_slot']}-{row['uid_serial']}" for row in assignments} != set(scores)):
                    raise RuntimeError('保存回执与冻结角色、来源或逐件评分不一致')
                seen.add(cid)
                bindings.append(WeightedSlotBinding(cid, slot, plan['plan_id'],
                    plan['source_snapshot_id'], payload['profile_id'], payload['profile_version']))
            bindings = tuple(sorted(bindings, key=lambda row: row.character_id))
            assert_slot_bindings_current(user, bindings)
            # 只投影本次实际保存角色；context及评分规则仍是原冻结对象，不读取当前配置。
            selected_context = replace(preview.context,
                roles=tuple(row for row in preview.context.roles if row.character_id in options))
            comparisons = freeze_weighted_loadout_comparisons(user, static, selected_context,
                preview.result.unified.selected, checkpoint=ownership.checkpoint,
                slot_ids_by_character={row.character_id: row.slot_id for row in bindings})
            assert_binding_comparisons(bindings, comparisons)
            _check_identity(preview, ownership, user, static)
    ownership.checkpoint()
    receipt = WeightedSaveReceipt(ownership.identity.account_id, ownership.identity.generation,
        str(ownership.identity.user_database_path), ids, bindings,
        slot_ids_by_character is not None or preview.explicit_slots)
    return receipt, comparisons


def assert_weighted_save_receipt_current(preview, ownership):
    """Qt交付前窄复核实际回执；迟到结果不覆盖已有页面游标。"""
    receipt = preview.save_receipt
    if (receipt is None or receipt.account_id != ownership.identity.account_id
            or receipt.generation != ownership.identity.generation
            or receipt.user_database_path != str(ownership.identity.user_database_path)
            or receipt.slot_bindings != preview.slot_bindings
            or receipt.explicit_slots != preview.explicit_slots):
        raise RuntimeError('保存交付缺少同一账号代次的真实回执')
    release = require_weighted_preview_release(preview)
    with UserDataDao(preview.user_database_path) as user, StaticCatalogReleaseReader().read_unit(
            preview.static_database_path, expected_release=release) as unit:
        static = unit.dao
        with user.read_consistent_state():
            _check_identity(preview, ownership, user, static)
            assert_slot_bindings_current(user, receipt.slot_bindings)
            assert_binding_comparisons(receipt.slot_bindings, preview.loadout_comparisons)
    ownership.checkpoint()
