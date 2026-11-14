# 先在同一冻结库存内完成所选队伍，再保留其装备并分配其他角色，不重写角色优先配置。
from dataclasses import replace

from src.services.allocation.axis.team_axis_selection import verify_team_selection
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.teams.enhancement_results import freeze_team_result_inputs, attach_team_result_preview


def execute_team_enhancement(facade, inventory, priority_list, selection, *, mode, options):
    verify_team_selection(selection, facade.user_database_path)
    result_inputs = freeze_team_result_inputs(selection, facade.user_database_path)
    team_roles = [role for role in selection.roles if role in priority_list]
    remaining = [role for role in priority_list if role not in selection.roles]
    reserved = set(options.get("locked_uids") or ())
    if mode == "update_mode":
        with UserDataDao(facade.user_database_path) as dao:
            reserved.update(f"nte-{'module' if row['kind'] == 'module' else 'core'}-{row['uid_slot']}-{row['uid_serial']}"
                            for row in dao.list_active_loadout_equipment_owners())
    bindings = [binding for binding in selection.teams if any(role in team_roles for role in binding.roles)]
    plans = {}
    if team_roles:
        axes = tuple((replace(binding.axis, rank_baseline=True, search_mode=selection.search_mode), tuple(role for role in binding.roles if role in team_roles)) for binding in bindings)
        plans, _ = facade.execute_allocation_inventory(inventory, team_roles, mode="axis_priority",
            axis_selection=axes if len(axes) == 2 else axes[0][0],
            **{**options, "locked_uids": reserved, "priority_groups": [team_roles]})
        for plan in plans.values():
            if not plan.get("valid"):
                continue
            for item in [plan.get("assigned_tape"), *plan.get("assigned_set_drives", ()), *plan.get("assigned_extra_drives", ())]:
                if item is not None:
                    uid = item.get("uid") if isinstance(item, dict) else item.uid
                    if uid:
                        reserved.add(uid)
    if remaining:
        groups = options.get("priority_groups")
        groups = [[role for role in group if role in remaining] for group in groups] if groups is not None else None
        rest, _ = facade.execute_allocation_inventory(inventory, remaining, mode=mode,
            **{**options, "locked_uids": reserved, "priority_groups": [g for g in groups if g] if groups is not None else None})
        plans.update(rest)
    verify_team_selection(selection, facade.user_database_path)
    return attach_team_result_preview(plans, result_inputs, facade.user_database_path), None


def ranked_team_baseline(selections, priority_list):
    """两队同名次并列；冻结顺序只进入本次请求，锁定成员不改变队内原始名次。"""
    groups = {}
    if any(not selection.damage_priority_groups for selection, _ in selections):
        return list(priority_list), [list(priority_list)], selections
    for selection, roles in selections:
        names = dict(zip(selection.selected_team_character_ids, roles))
        for rank, ids in enumerate(selection.damage_priority_groups):
            groups.setdefault(rank, []).extend(names[cid] for cid in ids if cid in names)
    ordered_groups = [members for _, members in sorted(groups.items()) if members]
    order = [role for members in ordered_groups for role in members]
    if set(order) != set(priority_list) or len(order) != len(priority_list):
        raise ValueError("战报伤害名次与本次计算角色不一致，请重新选择")
    revised = []
    for selection, roles in selections:
        ids = dict(zip(roles, selection.selected_team_character_ids))
        local_order = tuple(role for role in order if role in ids)
        revised.append((replace(selection, selected_team_character_ids=tuple(ids[role] for role in local_order)), local_order))
    return order, ordered_groups, tuple(revised)


def freeze_joint_axes(client, request, selections, **options):
    from src.services.allocation.axis.axis_postprocess import freeze_axis_allocation_inputs
    frozen = []
    for selection, roles in selections:
        partial_request = replace(request, role_order=roles)
        frozen.append(freeze_axis_allocation_inputs(client, partial_request, selection, **options))
    root, *others = frozen
    if any((item.snapshot_id, item.inventory_json, item.owners_sha256) !=
           (root.snapshot_id, root.inventory_json, root.owners_sha256) for item in others):
        from concurrent.futures import CancelledError
        raise CancelledError("双队冻结期间库存或配装锁已变更，请重新计算")
    identities = tuple(pair for item in frozen for pair in item.role_character_ids)
    reason = next((item.reason for item in frozen if item.reason), "")
    if not getattr(client, "supports_battle_axis_joint_teams", False):
        reason = "双队联合优化组件尚未就绪，已保留本次基础配装"
    return replace(root, additional_axes=tuple(others), role_character_ids=identities, reason=reason,
                   selection=replace(root.selection, participating_character_ids=tuple(cid for _, cid in identities)))
