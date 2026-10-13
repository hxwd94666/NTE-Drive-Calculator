# 验证首击只依赖角色已装备证据，不读整背包，跨页冲突仍拒绝。
from copy import deepcopy
import pytest
from src.integrations.native_inventory_snapshot import _equipped_subset
from src.integrations.nte_core_protocol import NteCoreProtocolError
from src.services.battle_report.capture.build_context import native_equipment_projection
from src.services.battle_report.inputs.scopes import select_native_team_snapshot
from src.services.battle_report.inputs.preparation import first_hit_snapshot_ready
from tests.test_battle_capture_build_freeze import native_snapshot, capture, finish, scoped  # noqa: F401
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.battle_report.inputs.helpers import attempt


def equipped_source():
    snapshot = native_snapshot()
    items = snapshot.pop("inventory_projection")["items"]
    snapshot["domains"].pop("inventory")
    snapshot["character_projection"]["battleEquipment"] = {"schemaVersion": 1,
        "scope": "character_equipped_only", "complete": True, "items": items,
        "characters": [{"character_id": 1072}], "statProvenance": {"main": "static_catalog_curve"}}
    return snapshot


def test_first_hit_is_ready_with_equipped_only_and_survives_exit():
    source = equipped_source()
    original = deepcopy(source)
    selected = select_native_team_snapshot(source)
    assert native_equipment_projection(selected)
    assert first_hit_snapshot_ready(selected, attempt(), {"native_capture": {"providerId": "p"}})
    assert source == original
    assert "inventory" not in selected["domains"]
    selected["character_projection"]["revision"] = "later-scene"
    assert native_equipment_projection(selected) is None


def test_page_union_preserves_equipped_only_scope_and_rejects_duplicate_items():
    page = equipped_source()["character_projection"]["battleEquipment"]
    result = _equipped_subset([page])
    assert result["complete"] is True
    assert result["scope"] == "character_equipped_only"
    other = deepcopy(page)
    other["characters"][0]["character_id"] = 1004
    with pytest.raises(NteCoreProtocolError):
        _equipped_subset([page, other])
    assert _equipped_subset([page, None])["complete"] is False


def test_missing_equipped_member_does_not_become_empty_complete_build():
    source = select_native_team_snapshot(equipped_source())
    source["character_projection"]["battleEquipment"]["characters"] = []
    assert native_equipment_projection(source) is None


def test_equipped_only_capture_materializes_without_inventory_and_keeps_database_unchanged(capture):
    service, deps, current_id, _ = capture
    source = equipped_source()
    service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(source))
    outcome = finish(service)
    assert outcome.warning_message is None
    with UserDataDao(deps.user_database_path) as dao:
        build = dao.load_battle_build_snapshot(outcome.battle_record_id)
        assert build["characters"][0]["equipment"][0]["uid_serial"] == 202
        assert build["characters"][0]["profile"]["capture_equipment_source"] == "native_first_hit_projection"
        assert dao.current_inventory_snapshot_id() == current_id
        context = dao.load_battle_capture_build("capture")
        assert "equipment" not in context and "native_runtime_snapshot" not in context
        assert "inventory" not in context["native_scope_builds"]["combat"]["snapshot"]["domains"]
