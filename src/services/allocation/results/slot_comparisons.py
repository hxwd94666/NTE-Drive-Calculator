# 为实际计算按钮冻结槽位基线并生成同权重装备变动。
from __future__ import annotations

from dataclasses import replace

from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_REMOVED,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
)
from src.optimizer.plan_diff import build_plan_diff
from src.services.allocation.results.comparison_scoring import (
    freeze_comparison_scorers,
    persist_comparison_diff,
)
from src.services.equipment.loadouts.saved_state_loadout_bridge import resolve_character_id_for_allocation_role
from src.services.equipment.loadouts.weighted_loadout_comparison_service import (
    freeze_loadout_comparison_batches,
    select_frozen_comparison,
)
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.storage.sqlite.user_data_dao import UserDataDao


def freeze_legacy_slot_comparisons(database_path, static_path, request, engine, *, snapshot_id, checkpoint=lambda: None, static_read_unit=None):
    # 权重逐角色冻结；本请求共用独立规则副本与缓存，不跨账号或跨计算复用。
    result = {}
    # 所有角色的槽位与来源在同一读事务冻结；批量读取不跨事务补最新状态。
    with UserDataDao(database_path) as dao, dao.read_consistent_state(), \
            static_catalog_read_unit(static_path, read_unit=static_read_unit) as unit:
        static = unit.dao
        attributes = static.list_equipment_attributes()
        dataset = static.summary()["dataset"]["dataset_id"]
        requests = []
        for name in request.role_order:
            checkpoint()
            role = request.roles_db[name]
            character_id = resolve_character_id_for_allocation_role(name, static, dao, snapshot_id=snapshot_id)
            slots = dao.list_loadout_slots(int(character_id))
            if not slots:
                result[name] = ()
                continue
            preferences = request.stat_priority_configs.get(name) or {}
            requests.append((name, slots, dict(
                character_id=character_id, weights=role.get("weights", {}),
                main_weights=role.get("main_weights"),
                zero_weight_stats=preferences.get("blacklist", ()) if preferences.get("blacklist_zero_weight") else (),
            )))
        scorers = freeze_comparison_scorers(engine, [spec for _, _, spec in requests],
            attributes=attributes, dataset_id=dataset, static_release=unit.release, checkpoint=checkpoint)
        comparisons = freeze_loadout_comparison_batches(dao, static,
            [(scorer, slots) for scorer, (_, slots, _) in zip(scorers, requests)], checkpoint=checkpoint)
        for (name, _, _), rows in zip(requests, comparisons):
            result[name] = rows
            for row in result[name]:
                row.diff["calculation_snapshot_id"] = snapshot_id
    return {name: result[name] for name in request.role_order}


def refresh_legacy_slot_comparisons(comparisons, plans):
    result = {}
    for name, rows in comparisons.items():
        updated = []
        for row in rows:
            tape = next((item for item in row.old_items if item.get("type") == "tape"), None)
            drives = [item for item in row.old_items if item.get("type") != "tape"]
            diff = build_plan_diff({name: {ROLE_EQUIPPED_TAPE: tape, ROLE_EQUIPPED_DRIVES: drives}},
                                   {name: plans.get(name, {})})[name]
            old_by_uid = {item["uid"]: item for item in row.old_items}
            diff[DIFF_REMOVED] = [dict(old_by_uid[item["uid"]]) for item in diff[DIFF_REMOVED]]
            if row.scorer is not None:
                diff[DIFF_ADDED] = [row.scorer.verify_result_score(item) for item in diff[DIFF_ADDED]]
            updated.append(replace(row, diff={**row.diff, **diff}))
        result[name] = tuple(updated)
    return result


def single_slot_comparison_diffs(comparisons):
    return {name: persist_comparison_diff(rows[0].diff) for name, rows in comparisons.items() if len(rows) == 1}


def selected_legacy_comparison_diffs(comparisons, plans, targets):
    refreshed = refresh_legacy_slot_comparisons(comparisons, plans)
    return {name: select_frozen_comparison(refreshed.get(name, ()), slot_id)
            for name, (_character_id, slot_id) in targets.items()}
