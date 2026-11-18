# 在账号一致读取边界内冻结每队来源，真实 UID 冲突遵循跨快照公共身份规则。
from src.domain.localization.catalog import localized_text
from dataclasses import replace
import hashlib
import json

from src.domain.equipment.loadouts.loadout_equipment_identity import source_snapshots_share_equipment_uids
from src.domain.equipment.loadouts.virtual_equipment import is_virtual_equipment_assignment, normalized_equipment_assignment
from src.domain.teams.overview import MemberOverview, SourceMode, TeamOverview
from src.domain.teams.source_selection import with_single_slot_defaults
from src.services.equipment.loadouts.frozen_loadout import resolve_plan_inventory_items
from src.services.equipment.loadouts.game_loadout_projection_service import GameLoadoutProjectionService


def input_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def freeze_team_sources(dao, static_dao, teams, *, checkpoint=lambda: None, source_overrides=None,
                        text_catalog=None):
    """调用者持有一致读取；当前指针和每份来源快照均在本次批内只解析一次。"""
    checkpoint()
    teams = tuple(teams)
    slots = dao.list_visible_loadout_slots(character_ids={key for team in teams for key in team.character_ids})
    by_id = {row["slot_id"]: row for row in slots}
    configs = {team.team_id: with_single_slot_defaults(
                   ((source_overrides or {}).get(team.team_id) or dao.get_team_source(team.team_id)).validated(),
                   (slot for slot in slots if slot["character_id"] in team.character_ids))
               for team in teams}
    selected = {slot_id for config in configs.values() if config.mode == SourceMode.CALC
                for _, slot_id in config.bindings}
    # 空槽参与默认选择，但未选备用槽位的历史内容不进入水合域。
    plans = {plan["plan_id"]: plan for plan in dao.get_loadout_plans(
        [slot["current_plan_id"] for slot in slots
         if slot["slot_id"] in selected and slot["current_plan_id"] is not None])}
    source_uids = {}
    for slot in slots:
        checkpoint()
        plan = plans.get(slot["current_plan_id"]) if slot["slot_id"] in selected else None
        slot["current_plan"] = plan
        if plan is not None and plan.get("source_snapshot_id") is not None:
            uids = source_uids.setdefault(plan["source_snapshot_id"], set())
            uids.update((int(row["uid_serial"]), int(row["uid_slot"]))
                        for row in plan.get("assignments") or ()
                        if not is_virtual_equipment_assignment(normalized_equipment_assignment(row)))
    game_ids = {key for team in teams if configs[team.team_id].mode == SourceMode.GAME for key in team.character_ids}
    game = (GameLoadoutProjectionService(dao, static_dao).project_current(character_ids=game_ids, checkpoint=checkpoint)
            if any(config.mode == SourceMode.GAME for config in configs.values()) else None)
    game_roles = {role.character_id: role for role in game.roles} if game else {}
    snapshots = {None: (None, [])}
    for source, uids in source_uids.items():
        inventory, ordered = [], sorted(uids)
        for offset in range(0, len(ordered), 400):
            checkpoint()
            inventory.extend(dao.list_inventory_items(source, uids=ordered[offset:offset + 400]))
        snapshots[source] = dao.inventory_snapshot_summary(source), inventory
    result = []
    custom = {row["character_id"]: row["name_zh"] for row in dao.list_custom_characters()}
    for team in teams:
        config = configs[team.team_id]
        members = []
        sources = {}
        for character_id in team.character_ids:
            checkpoint()
            character = static_dao.get_character(character_id) or {}
            name = character.get("name_zh") or custom.get(character_id) or localized_text(text_catalog, 'auto.services.teams.source_inputs.freeze_team_sources.f84645dad', '角色 {character_id}', character_id=f'{character_id}')
            issues, items, slot_id, plan_id, snapshot_id = [], (), None, None, None
            if config.mode == SourceMode.GAME:
                snapshot_id = game.snapshot_id
                role = game_roles.get(character_id)
                if not game.supported:
                    issues.append(game.message)
                elif role is None or role.status != "ready":
                    issues.append(role.reason if role else localized_text(
                    text_catalog, "teams.source_inputs.issue.character_not_confirmed",
                    "该角色缺少已确认的完整游戏配装"))
                if role:
                    items = role.items
                sources[character_id] = game.source
                identity = (snapshot_id, items)
            else:
                slot_id = dict(config.bindings).get(character_id)
                slot = by_id.get(slot_id)
                plan = slot.get("current_plan") if slot else None
                if slot is None or slot["character_id"] != character_id:
                    issues.append(localized_text(text_catalog, "teams.source_inputs.issue.slot_missing",
                                         "所选槽位已删除／归档或尚未选择"))
                else:
                    slot_id = slot["slot_id"]
                    if plan is None:
                        issues.append(localized_text(text_catalog, "teams.source_inputs.issue.slot_without_plan",
                                         "所选槽位缺少已保存方案"))
                if plan:
                    plan_id, snapshot_id = plan["plan_id"], plan.get("source_snapshot_id")
                    summary, inventory = snapshots[snapshot_id]
                    sources[character_id] = (summary or {}).get("source")
                    items = tuple(resolve_plan_inventory_items(dao, plan, snapshot_items=inventory))
                    if not summary or not summary.get("complete"):
                        issues.append(localized_text(text_catalog, "teams.source_inputs.issue.plan_snapshot_missing",
                                         "方案自身来源快照缺失或不完整"))
                    assignments = plan.get("assignments") or ()
                    if len(items) != len(assignments) or any(is_virtual_equipment_assignment(row.get("raw_assignment") or row) for row in assignments):
                        issues.append(localized_text(text_catalog, "teams.source_inputs.issue.plan_missing_equipment",
                                         "方案含缺失装备或理论占位"))
                    if plan.get("status") != "ready" or not items or sum(item["kind"] == "core" for item in items) != 1:
                        issues.append(localized_text(text_catalog, "teams.source_inputs.issue.plan_incomplete",
                                         "方案尚非完整配装"))
                identity = (slot_id, plan, items)
            if any(not item.get("main_stats") or not item.get("sub_stats") for item in items):
                issues.append(localized_text(text_catalog, "teams.source_inputs.issue.missing_real_stats",
                                         "装备缺少真实词条"))
            members.append(MemberOverview(character_id, name, tuple(items), slot_id, plan_id, snapshot_id,
                                          input_digest(identity), tuple(dict.fromkeys(issues))))
        conflicts = set()
        for index, left in enumerate(members):
            left_uids = {(item["uid_slot"], item["uid_serial"]) for item in left.items if int(item.get("uid_slot") or 0) > 0}
            for right in members[index+1:]:
                if not source_snapshots_share_equipment_uids(left.snapshot_id, sources.get(left.character_id),
                                                           right.snapshot_id, sources.get(right.character_id)):
                    continue
                if left_uids & {(item["uid_slot"], item["uid_serial"]) for item in right.items if int(item.get("uid_slot") or 0) > 0}:
                    conflicts.update((left.character_id, right.character_id))
        members = tuple(replace(member, issues=(*member.issues, localized_text(
                    text_catalog, "teams.source_inputs.issue.duplicate_equipment",
                    "成员间真实装备重复")))
                        if member.character_id in conflicts else member for member in members)
        issues = (localized_text(text_catalog, "teams.source_inputs.issue.team_size",
                                 "队伍未满四人"),) if len(members) != 4 else ()
        if conflicts:
            issues += (localized_text(text_catalog, "teams.source_inputs.issue.equipment_conflict",
                                  "队伍存在装备冲突"),)
        result.append(TeamOverview(team, config, members, issues))
    return tuple(result)
