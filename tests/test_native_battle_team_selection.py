# 验证战报只保存队伍证据和装备，未出场角色及整包基线不会进入最终战报。
from copy import deepcopy
from unittest.mock import patch
import json

from src.services.native_battle_team_snapshot import select_native_team_snapshot
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_battle_capture_build_freeze import capture, finish, native_snapshot, scoped  # noqa: F401
from tests.test_battle_axis_dao import _equipped_item


def full_source():
    snapshot = native_snapshot()
    snapshot.pop("selection")
    snapshot["domains"]["character"]["records"] = [
        {"ItemID": str(cid), "bIsTemporary": False, "bUnSaved": False} for cid in (1072, 1004)]
    snapshot["character_projection"]["profiles"].append({"character_id": 1004, "character_level": 1})
    snapshot["inventory_projection"]["items"].append(_equipped_item(999, 99, 1004))
    snapshot["inventory_projection"]["characters"] = [{"character_id": cid} for cid in (1072, 1004)]
    snapshot["domains"]["inventory"].update(recordCount=2, records=[
        {"UniqueID": {"solt": 22, "serial": 202}}, {"UniqueID": {"solt": 99, "serial": 999}}])
    return snapshot


def test_team_selection_keeps_original_identity_without_claiming_full_inventory():
    source = full_source()
    before = deepcopy(source)
    result = select_native_team_snapshot(source)
    assert source == before
    assert result["selection"] == {"kind": "team_subset", "character_ids": [1072]}
    assert result["domains"]["inventory"]["recordCount"] == 2  # Source count, not a fabricated complete page.
    assert len(result["domains"]["inventory"]["records"]) == 1
    assert len(result["domains"]["character"]["records"]) == 1
    assert len(result["inventory_projection"]["items"]) == 1
    assert len(result["character_projection"]["profiles"]) == 1
    assert select_native_team_snapshot(result) == result


def test_saved_context_and_calculation_copy_exclude_full_baseline(capture):
    service, deps, _, _ = capture
    source = full_source()
    with patch.object(service, "_resolve_character_stat_snapshots", return_value={}):
        service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(source))
    result = finish(service)
    assert result.warning_message is None
    with UserDataDao(deps.user_database_path) as dao:
        raw = json.loads(dao._db().execute("SELECT raw_record_json FROM battle_axis_capture").fetchone()[0])
        context = raw["calc_capture_context"]
        assert set(context["profiles"]) == {"1072"}
        assert len(context["equipment"]) == 1
        assert context["equipment"][0]["uid_serial"] == 202
        for snapshot in (context["native_runtime_snapshot"]["scopes"]["combat"]["snapshot"],
                         context["native_scope_builds"]["combat"]["snapshot"]):
            assert len(snapshot["inventory_projection"]["items"]) == 1
            assert len(snapshot["domains"]["character"]["records"]) == 1
        assert len(dao.load_battle_build_snapshot(result.battle_record_id)["characters"]) == 1
