# 用发行模板建立四名角色的独立真实装备和保存槽位，所有账号数据只在临时目录。
from pathlib import Path

from src.domain.equipment.properties.equipment_normalizer import calculate_drive_main_stats
from src.domain.teams.models import TeamDraft
from src.domain.teams.overview import SourceMode
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.services.equipment.loadouts.game_loadout_projection_service import GameLoadoutProjectionService
from src.services.teams.overview import TeamOverviewService
from src.storage.sqlite.user_data_dao import UserDataDao

ROOT = Path(__file__).resolve().parents[3]


def overview_case(directory, ids=(1072, 1003, 1004, 1008)):
    static = ROOT / "data/game_static.sqlite3"
    path = directory / "overview.sqlite3"
    native = []
    with static_catalog_read_unit(static) as unit:
        templates = {row["item_id"]: row for row in unit.dao.list_equipment_items()}
        for character_id in ids:
            plan = unit.dao.get_equipment_plan(character_id)
            for ordinal, item_id in enumerate((plan["core_item_id"], *plan["module_item_ids"])):
                item = templates[item_id]
                main = ([{"property_id": "AtkUp", "value": .05, "percent": True}] if ordinal == 0 else
                        [{"property_id": key, "value": value, "percent": False}
                         for key, value in zip(("AtkAdd", "HPMaxAdd"), calculate_drive_main_stats(item["grid_count"], "Gold").values())])
                native.append(dict(uid={"slot": 4 if ordinal == 0 else 3, "serial": character_id*100+ordinal},
                    kind=item["kind"], item_id=item_id, suit_id=item["suit_id"], geometry=item["geometry_id"],
                    grid=item["grid_count"], quality="orange", level=0, max_level=item["max_level"], locked=False,
                    discarded=False, equipped=True, equipped_character_id=character_id,
                    equipped_character_uid={"slot": 1, "serial": character_id}, equipped_placement=None,
                    names={}, suit_names={}, main_stats=main,
                    sub_stats=[{"property_id": "CritBase", "value": .01, "percent": True}]))
        with UserDataDao(path, account_id="overview") as dao:
            snapshot = dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
                "complete": True, "generation": 1, "sequence": 1, "observed_at_unix_ms": 1787000000000,
                "item_count": len(native), "items": native}})
            team = dao.save_team(TeamDraft("四人队", ids))
            projections = GameLoadoutProjectionService(dao, unit.dao).project_current()
            assert all(role.status == "ready" for role in projections.roles)
            slots = {}
            for role in projections.roles:
                slot = dao.create_loadout_slot(role.character_id, role.role_name, slot_key="primary")
                dao.save_plan_to_slot(slot, name="冻结方案", assignments=role.assignments,
                    source_snapshot_id=snapshot, status="ready", score=1,
                    payload={"schema": "game-observed-loadout-v1", "source_role_name": role.role_name})
                slots[role.character_id] = slot
            dao.save_team_source(team, dao.get_team_source(team.team_id), mode=SourceMode.CALC,
                                 bindings=tuple(slots.items()))
    service = TeamOverviewService(account_id="overview", generation=1, database_path=path,
        static_database_path=static, context_is_current=lambda: True)
    return service, team, slots
