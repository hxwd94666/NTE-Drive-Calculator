# 从一次一致SQLite读取冻结所选current方案，不以历史active或其他槽位填补缺项。
from math import isfinite
from pathlib import Path

from src.domain.allocation.inputs import StaticDatasetReference
from src.domain.allocation.slot_binding import WeightedSlotBinding
from src.services.allocation.context_builder import ALLOCATION_CONTEXT_SOLVER_VERSION
from src.services.allocation.slot_binding import freeze_slot_selection
from src.storage.sqlite.user_data_dao import UserDataDao


def read_persistence(user_database_path, static_database_path=None, equipment_only=False,
                     *, slot_ids_by_character=None, ownership=None):
    # DTO仍由既有公开feature facade拥有；局部引用避免reader与runner导入环。
    from .runner import (WeightedAllocationPersistence, WeightedAllocationRequest,
                         WeightedSavedPlanSignature, WeightedSavedAssignmentSignature)
    database_path = Path(user_database_path)
    selection = freeze_slot_selection(slot_ids_by_character)
    if ownership is not None:
        ownership.checkpoint()
    if not database_path.is_file():
        return WeightedAllocationPersistence(database_path, None, None, (), None, (), None,
                                             selected_slots=selection)
    with UserDataDao(database_path) as dao, dao.read_consistent_state():
        if ownership is not None:
            ownership.checkpoint()
            if dao.profile()['account_id'] != ownership.identity.account_id:
                raise RuntimeError('恢复数据库不属于读取前冻结的账号')
        profile = next((row for row in dao.list_optimization_profiles()
                        if row.get('name') == '__weighted_allocation_role_priority__'), None)
        if profile is None or not isinstance(profile.get('version'), dict):
            return WeightedAllocationPersistence(database_path, None, None, (), None, (), None,
                                                 selected_slots=selection)
        version = profile['version']
        profile_id, profile_version = int(profile['profile_id']), int(version['version_number'])
        characters = tuple(dict(row) for row in version.get('characters') or ())
        expected = {int(row['character_id']) for row in characters}
        targets = dict(selection) if selection is not None else {}
        if selection is None:
            targets = {int(row['character_id']): int(row['slot_id'])
                       for row in dao.list_visible_loadout_slots()
                       if row['slot_key'] == 'primary' and int(row['character_id']) in expected}
        matching, bindings, observed = {}, [], []
        valid = (bool(expected) and len(expected) == len(characters) and set(targets) == expected
                 and len(set(targets.values())) == len(targets))
        for cid, slot_id in sorted(targets.items()):
            slot = dao.get_loadout_slot(slot_id)
            plan = (slot or {}).get('current_plan') or {}
            payload = plan.get('payload') or {}
            observed.append((cid, slot_id, (slot or {}).get('current_plan_id'),
                             plan.get('source_snapshot_id'), bool(plan.get('allocation_locked')),
                             bool((slot or {}).get('is_archived')), payload.get('profile_version')))
            scores = payload.get('assignment_scores')
            assignments = plan.get('assignments') or ()
            complete_scores = (isinstance(scores, dict) and bool(assignments)
                and all(f"nte-{row['kind']}-{row['uid_slot']}-{row['uid_serial']}" in scores
                        and isinstance(scores[f"nte-{row['kind']}-{row['uid_slot']}-{row['uid_serial']}"], (int, float))
                        and isfinite(scores[f"nte-{row['kind']}-{row['uid_slot']}-{row['uid_serial']}"])
                        for row in assignments))
            if (slot is None or slot.get('is_archived') or slot.get('character_id') != cid
                    or slot.get('current_plan_id') is None or plan.get('plan_id') != slot['current_plan_id']
                    or plan.get('slot_id') != slot_id or plan.get('character_id') != cid
                    or plan.get('allocation_locked') or type(plan.get('source_snapshot_id')) is not int
                    or plan['source_snapshot_id'] < 1 or plan.get('score') is None
                    or not isfinite(float(plan['score'])) or not complete_scores
                    or payload.get('schema') != 'allocation-official-snapshot-v1'
                    or payload.get('source') != 'weighted_allocation'
                    or payload.get('profile_id') != profile_id
                    or payload.get('profile_version') != profile_version):
                valid = False
                continue
            matching[cid] = plan
            bindings.append(WeightedSlotBinding(cid, slot_id, plan['plan_id'],
                            plan['source_snapshot_id'], profile_id, profile_version))
        if ownership is not None:
            ownership.checkpoint()
    def unavailable():
        return WeightedAllocationPersistence(database_path, profile_id, profile_version,
            characters, None, (), None, selected_slots=selection, selection_identity=tuple(observed))
    if not valid or set(matching) != expected:
        return unavailable()
    snapshots = {plan['source_snapshot_id'] for plan in matching.values()}
    dataset_rows = [plan['payload'].get('static_dataset') for plan in matching.values()]
    if (len(snapshots) != 1
            or {plan['payload'].get('solver_version') for plan in matching.values()} != {ALLOCATION_CONTEXT_SOLVER_VERSION}
            or not dataset_rows or not isinstance(dataset_rows[0], dict)
            or any(row != dataset_rows[0] for row in dataset_rows[1:])):
        return unavailable()
    dataset = dataset_rows[0]
    try:
        static_dataset = StaticDatasetReference(schema_version=int(dataset['schema_version']),
            dataset_id=str(dataset['dataset_id']), importer_version=int(dataset['importer_version']),
            built_at_utc=str(dataset['built_at_utc']))
        if (static_dataset.schema_version < 1 or static_dataset.importer_version < 1
                or not static_dataset.dataset_id.strip() or not static_dataset.built_at_utc.strip()):
            return unavailable()
    except (KeyError, TypeError, ValueError):
        return unavailable()
    signatures = tuple(
        WeightedSavedPlanSignature(
            plan_id=int(plan["plan_id"]),
            character_id=character_id,
            score=float(plan["score"]),
            uids=frozenset(
                (int(item["uid_slot"]), int(item["uid_serial"]))
                for item in plan.get("assignments") or ()
            ),
            assignments=tuple(
                WeightedSavedAssignmentSignature(
                    uid=(int(item["uid_slot"]), int(item["uid_serial"])),
                    kind=str(item["kind"]),
                    target_row=(int(item["target_row"]) if item.get("target_row") is not None else None),
                    target_column=(int(item["target_column"]) if item.get("target_column") is not None else None),
                    score=(
                        float((plan["payload"].get("assignment_scores") or {}).get(
                            f"nte-{item['kind']}-{item['uid_slot']}-{item['uid_serial']}"
                        ))
                        if f"nte-{item['kind']}-{item['uid_slot']}-{item['uid_serial']}"
                        in (plan["payload"].get("assignment_scores") or {})
                        else None
                    ),
                    virtual=bool(
                        (item.get("raw_assignment") or {}).get("virtual")
                    ),
                    item_id=(
                        (item.get("raw_assignment") or {})
                        .get("virtual_equipment", {})
                        .get("item_id")
                    ),
                    suit_id=(
                        (item.get("raw_assignment") or {})
                        .get("virtual_equipment", {})
                        .get("suit_id")
                    ),
                    geometry=(
                        (item.get("raw_assignment") or {}).get("geometry")
                        or (item.get("raw_assignment") or {})
                        .get("virtual_equipment", {})
                        .get("geometry")
                    ),
                    grid_count=(
                        int(
                            (item.get("raw_assignment") or {}).get("grid_count")
                            or (item.get("raw_assignment") or {})
                            .get("virtual_equipment", {})
                            .get("grid_count")
                        )
                        if (
                            (item.get("raw_assignment") or {}).get("grid_count")
                            or (item.get("raw_assignment") or {})
                            .get("virtual_equipment", {})
                            .get("grid_count")
                        ) is not None
                        else None
                    ),
                )
                for item in plan.get("assignments") or ()
            ),
        )
        for character_id, plan in sorted(matching.items())
    )
    request = WeightedAllocationRequest(database_path, snapshot_id=next(iter(snapshots)),
        profile_id=profile_id, profile_version=profile_version, top_k=1, include_role_top_k=False,
        static_database_path=static_database_path, equipment_only=equipment_only,
        slot_bindings=tuple(bindings))
    if ownership is not None:
        ownership.checkpoint()
    return WeightedAllocationPersistence(database_path, profile_id, profile_version, characters,
        request, signatures, static_dataset, slot_bindings=tuple(bindings), selected_slots=selection,
        selection_identity=tuple(observed))
