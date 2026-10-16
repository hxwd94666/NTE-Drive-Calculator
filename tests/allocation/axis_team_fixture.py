# 为隔离分配用例建立四人队伍观测及显式合成的冻结装备，不读取个人账号。
from copy import deepcopy
import json

from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_native_battle_team_continuity import observed_team


def attach_native_team(case, ids=(1072, 1003, 1004, 1008)):
    snapshot, attempt, record = observed_team()
    actors = [dict(slotIndex=i, objectKey=f"{10+i}:20", DefaultCharacterID=str(cid)) for i, cid in enumerate(ids)]
    snapshot["domains"]["team"]["records"] = [dict(EquippedPlayers=deepcopy(actors),
        CharacterItems=[dict(slotIndex=i, ItemID=str(cid)) for i, cid in enumerate(ids)])]
    native = record["native_capture"]
    for i, ctx in enumerate(native["contextEvents"]):
        ctx["team"]["actors"] = deepcopy(actors)
        ctx["observedUnixUs"] = "100000000" if i == 0 else "101250000"
    attempt.update(firstUnixSeconds=100.5, lastUnixSeconds=102.0)
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        conn = dao._db()
        raw = json.loads(conn.execute("SELECT raw_record_json FROM battle_axis_capture").fetchone()[0])
        raw["native_capture"] = native
        raw.setdefault("calc_capture_context", {})["native_scope_builds"] = {
            "combat": {"attempt_id": "1", "snapshot": snapshot}}
        conn.execute("UPDATE battle_axis_capture SET raw_record_json=?", (json.dumps(raw),))
        conn.commit()


def attach_fixed_builds(case, *, game_locked=False):
    """用发行模板生成独立真实UID的合成装备，复用正式养成与属性投影冻结四人A。"""
    from src.integrations.static_catalog_release import static_catalog_read_unit
    from src.domain.equipment.equipment_normalizer import calculate_drive_main_stats
    from src.services.battle_report.persistence import BattleReportPersistenceService
    from src.observability import OperationContext
    deps = case["dependencies"]
    ids = (1072, 1003, 1004, 1008)
    with UserDataDao(deps.user_database_path) as dao, static_catalog_read_unit(deps.static_database_path) as unit:
        _, actual = dao.export_inventory_snapshot(case["source_snapshot_id"])
        native = [{**row, "uid": {"slot": row["uid_slot"], "serial": row["uid_serial"]}, "grid": row["grid_count"]}
                  for row in actual]
        templates = {row["item_id"]: row for row in unit.dao.list_equipment_items()}
        core = next(row for row in native if row["kind"] == "core" and row["uid_serial"] == 100)
        frozen_items = []
        for cid in ids:
            plan = unit.dao.get_equipment_plan(cid)
            anchors = [row for row in plan["cells"] if row["anchor_item_id"]]
            for i, identifier in enumerate([core["item_id"], *plan["module_item_ids"]]):
                template = templates[identifier]
                if cid == 1072:
                    item = next(row for row in native if row["uid_serial"] == (100 if i == 0 else i)
                                and row["kind"] == ("core" if i == 0 else "module"))
                else:
                    item = deepcopy(core)
                    item.update(kind=template["kind"], item_id=identifier, suit_id=template["suit_id"],
                                geometry=template["geometry_id"], grid=template["grid_count"],
                                uid={"slot": 4 if i == 0 else 3, "serial": cid * 100 + i},
                                uid_slot=4 if i == 0 else 3, uid_serial=cid * 100 + i,
                                grid_count=template["grid_count"], sub_stats=[], locked=game_locked)
                    if i:
                        intrinsic = calculate_drive_main_stats(template["grid_count"], "Gold")
                        item["main_stats"] = [dict(property_id=key, value=value, percent=False, names={})
                            for key, value in zip(("AtkAdd", "HPMaxAdd"), intrinsic.values())]
                    native.append(item)
                item.update(equipped=True, equipped_character_id=cid,
                    equipped_placement=None if i == 0 else dict(row=anchors[i-1]["row"]-1, column=anchors[i-1]["column"]-1))
                frozen_items.append(item)
        case["source_snapshot_id"] = dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
            "complete": True, "generation": 1, "sequence": 3, "observed_at_unix_ms": 1_787_000_000_002,
            "item_count": len(native), "items": native}})
        service = BattleReportPersistenceService(dependencies=deps, context_is_current=lambda _: True,
            operation_context=OperationContext.create("battle_report"))
        build = service._freeze_capture_build(dao, ids)
        conn = dao._db()
        raw = json.loads(conn.execute("SELECT raw_record_json FROM battle_axis_capture").fetchone()[0])
        context = raw["calc_capture_context"]
        entry = context["native_scope_builds"]["combat"]
        entry.update(profiles=build["profiles"], stat_snapshots=build["stat_snapshots"], equipment=build["equipment"],
                     world_bonus=build["world_bonus"], unavailable_character_ids=[])
        context["native_scope_validation"] = {"combat": {"state": "ready", "reason": None}}
        conn.execute("UPDATE battle_axis_capture SET raw_record_json=?", (json.dumps(raw),))
        conn.commit()
    case["fixed_equipment_by_character"] = {
        cid: tuple(f"nte-{row['kind']}-{row['uid_slot']}-{row['uid_serial']}" for row in frozen_items
                   if row["equipped_character_id"] == cid) for cid in ids}
