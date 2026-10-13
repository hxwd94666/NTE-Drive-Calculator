# 验证新战报装备只保留一份，分半场冲突不丢失，历史输入不改写。
from copy import deepcopy
from src.storage.sqlite.battle_report.battle_equipment_storage import compact_equipment_context


def test_new_record_uses_one_materialized_equipped_copy():
    item = {"uid_slot": 1, "uid_serial": 2, "equipped": True, "equipped_character_id": 1042}
    context = {"equipment": [item], "native_runtime_snapshot": {"copy": [item]},
        "native_scope_builds": {"combat": {"equipment": [item], "snapshot": {
            "character_projection": {"battleEquipment": {"items": [item]}},
            "domains": {"character": {"records": [{"equippedCassettes": [{"item": {"UniqueID": {"solt": 1, "serial": 2}, "raw": "value"}}]}]}}}}}}
    before = deepcopy(context)
    result = compact_equipment_context(context, [item], {1042})
    assert context == before
    assert "equipment" not in result and "native_runtime_snapshot" not in result
    assert "scope_equipment" not in result["equipment_storage"]
    entry = result["native_scope_builds"]["combat"]
    assert "equipment" not in entry
    assert entry["equipment_refs"][0]["storage"] == "battle_equipment_snapshot"
    assert "items" not in entry["snapshot"]["character_projection"]["battleEquipment"]
    assert "item" not in entry["snapshot"]["domains"]["character"]["records"][0]["equippedCassettes"][0]


def test_conflicting_scope_input_is_preserved_once_without_candidate_collection():
    item = {"uid_slot": 1, "uid_serial": 2, "equipped": True, "equipped_character_id": 1042}
    context = {"native_scope_builds": {scope: {"equipment": [item], "snapshot": {}} for scope in ("upper", "lower")}}
    result = compact_equipment_context(context, [], set())
    assert len(result["equipment_storage"]["scope_equipment"]) == 1
    assert result["native_scope_builds"]["upper"]["equipment_refs"] == result["native_scope_builds"]["lower"]["equipment_refs"]
