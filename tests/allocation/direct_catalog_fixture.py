# 由发行静态身份构造临时完整库存，复用真实官方角色投影而不读取个人账号。
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from src.domain.equipment.equipment_normalizer import calculate_drive_main_stats
from src.integrations.static_catalog_release import StaticCatalogReleaseReader
from src.services.allocation.inventory import SqliteAllocationInventory, legacy_stat_name
from src.services.equipment.tape_main_value import full_level_tape_main_value
from src.services.official_role.attributes import calculate_official_role_combat_stat_components
from src.services.official_role.page import load_official_role_detail
from src.storage.sqlite.user_data_dao import UserDataDao


ROOT = Path(__file__).resolve().parents[2]


def create_catalog_case(directory: Path, *, copies: int = 4, account_id="official-catalog-fixture"):
    """Official IDs/layout/growth are read once; only synthetic UIDs and rolls are generated."""
    static_path = ROOT / "data/game_static.sqlite3"
    database = directory / "synthetic-catalog-user.sqlite3"
    with StaticCatalogReleaseReader().read_unit(static_path) as unit:
        static = unit.dao
        character = next(row for row in static.list_role_template_characters() if row["character_id"] == 1003)
        role, character_id = character["name_zh"], character["character_id"]
        plan = static.get_equipment_plan(character_id)
        templates = {row["item_id"]: row for row in static.list_equipment_items()}
        element = str(character["element_type"]).rsplit("_", 1)[-1].title()
        element_property = f"DamageUp{element}Base"
        items = []

        def item(template, main, sub):
            ordinal = len(items) + 1
            return {
                "uid": {"slot": 1000 + ordinal, "serial": 200000 + ordinal}, "kind": template["kind"],
                "item_id": template["item_id"], "suit_id": template["suit_id"],
                "geometry": template["geometry_id"], "grid": template["grid_count"],
                "quality": template["quality"].casefold(), "level": 20, "max_level": template["max_level"],
                "locked": False, "discarded": False, "equipped": False,
                "equipped_character_id": None, "equipped_character_uid": None, "equipped_placement": None,
                "names": {}, "suit_names": {}, "main_stats": main,
                "sub_stats": [{"property_id": key, "value": value, "percent": key != "AtkAdd", "names": {}}
                              for key, value in sub],
            }

        for item_id in plan["module_item_ids"]:
            template = templates[item_id]
            intrinsic = calculate_drive_main_stats(template["grid_count"], "Gold")
            main = [{"property_id": "AtkAdd", "value": intrinsic["攻击力"], "percent": False, "names": {}},
                    {"property_id": "HPMaxAdd", "value": intrinsic["生命值"], "percent": False, "names": {}}]
            for copy in range(copies):
                rolls = [("AtkUp", 0.05), ("AtkAdd", 16.0), ("DamageUpGeneralBase", 0.02),
                         (element_property, 0.025)] if copy == 0 else [
                    ("AtkUp", 0.0125 * (1 + copy % 3)), ("CritBase", 0.01 * (1 + copy % 5)),
                    ("CritDamageBase", 0.02 * (1 + copy % 7)), (element_property, 0.0125)]
                items.append(item(template, main, rolls))
        core = templates[plan["core_item_id"]]
        main_id = plan["core_attribute_ids"][0]
        main_name = legacy_stat_name(main_id)
        main_value = full_level_tape_main_value(main_name, core["quality"])
        if main_value is None:
            raise AssertionError("official catalog core main has no frozen full-level projection")
        main = [{"property_id": main_id, "value": main_value / 100.0, "percent": True, "names": {}}]
        for copy in range(copies):
            rolls = [("AtkUp", 0.05), ("AtkAdd", 16.0), ("DamageUpGeneralBase", 0.02),
                     (element_property, 0.025)] if copy == 0 else [
                ("AtkUp", 0.0125), ("CritBase", 0.04), ("CritDamageBase", 0.08), (element_property, 0.0125)]
            items.append(item(core, main, rolls))
        payload = {"method": "event.inventory.snapshot", "params": {
            "complete": True, "generation": 1, "sequence": 1, "observed_at_unix_ms": 1_800_000_000_000,
            "item_count": len(items), "items": items,
        }}
        with UserDataDao(database, account_id=account_id) as dao:
            snapshot = dao.import_inventory_snapshot(payload)
            projected = SqliteAllocationInventory(dao, static).build(snapshot)
        detail = load_official_role_detail(database, character_id, include_inventory_contexts=False,
                                           include_replacement_candidates=False,
                                           static_database_path=static_path, static_read_unit=unit)
        non_equipment = {row.key: row.value for row in calculate_official_role_combat_stat_components(detail, [])}
        return {
            "user_database_path": database, "static_database_path": static_path,
            "dataset_id": unit.release.dataset_id, "snapshot_id": snapshot, "selected_role": role,
            "character_id": character_id, "profile": deepcopy(detail["profile"]),
            "inventory": list(projected.items), "native_snapshot": payload,
            "core_main_filters": {role: [main_name]},
            "crit_rate_baselines": {role: round((non_equipment["CritBase"] - 0.05) * 100.0, 4)},
        }
