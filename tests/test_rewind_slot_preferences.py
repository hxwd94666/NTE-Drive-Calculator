# 验证倒带槽位偏好的账号隔离、一致读取与保存失败回滚。
import pytest

from src.domain.rewind_loadout import RewindSlotReference, read_slot_preferences
from src.services.rewind.recommendation import RewindShapeRecommendationService
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError


def create_account(path, identifier):
    with UserDataDao(path, account_id=identifier) as dao:
        # 使用完整原生事件及明确装备状态；方案 UID 只引用这份合成库存，不绕过完整性校验。
        snapshot_id = dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
            "complete": True, "generation": 1, "sequence": 1,
            "observed_at_unix_ms": 1_784_308_856_895, "item_count": 1,
            "items": [{"uid": {"serial": 11, "slot": 22}, "kind": "module",
                       "item_id": "cell4_style1_1_Orange", "suit_id": "Suit1", "geometry": "Hen4",
                       "grid": 4, "quality": "orange", "level": 20, "max_level": 20,
                       "locked": False, "equipped": False,
                       "main_stats": [], "sub_stats": []}],
        }})
        plan_id = dao.save_loadout_plan(name="测试", character_id=1004,
            source_snapshot_id=snapshot_id, score=10, is_active=True,
            assignments=[{"uid_slot": 22, "uid_serial": 11, "kind": "module",
                          "target_row": 1, "target_column": 1, "rotation": 0}],
            payload={"schema": "allocation-official-snapshot-v1"})
        slot = dao.list_loadout_slots(1004)[0]
        empty = dao.create_loadout_slot(1004, "空槽")
        assert len(dao.list_visible_loadout_slots_with_plans()) == 2
        return RewindSlotReference(1004, slot["slot_id"], plan_id), empty


def service_for(path, static):
    return RewindShapeRecommendationService(user_database_path=path, static_database_path=static)


def test_preferences_preserve_unrelated_fields_and_do_not_cross_accounts(tmp_path):
    first, second = tmp_path / "first.sqlite3", tmp_path / "second.sqlite3"
    ref, _ = create_account(first, "first")
    create_account(second, "second")
    service = service_for(first, tmp_path / "static.sqlite3")
    value = {"slot_selection_version": 1, "selected_slots": {"1004": ref.slot_id},
             "saved_rewind_shape_ids": ["shape-a"] * 8, "rewind_qualities": ["gold"], "other": 4}
    service.save_preferences(value)
    assert service.load_preferences() == value
    assert service_for(second, tmp_path / "static.sqlite3").load_preferences() == {}


def test_invalid_preference_save_preserves_previous_settings(tmp_path):
    path = tmp_path / "user.sqlite3"
    ref, _ = create_account(path, "fixture")
    service = service_for(path, tmp_path / "static.sqlite3")
    service.save_preferences({"saved_rewind_shape_ids": ["old"] * 8})
    for value in ({"slot_selection_version": 1.0, "selected_slots": {}},
                  {"slot_selection_version": 1, "selected_slots": {"1004": True}},
                  {"slot_selection_version": 1, "selected_slots": {"1005": ref.slot_id}}):
        with pytest.raises(ValueError):
            service.save_preferences(value)
        assert service.load_preferences()["saved_rewind_shape_ids"] == ["old"] * 8


def test_strategy_slot_preferences_persist_independently_and_are_account_private(tmp_path):
    first, second = tmp_path / "first.sqlite3", tmp_path / "second.sqlite3"
    ref, empty = create_account(first, "first")
    create_account(second, "second")
    service = service_for(first, tmp_path / "static.sqlite3")
    value = {"slot_selection_version": 2,
             "target_character_ids": [1004], "main_character_ids": [1004],
             "selected_slots_by_strategy": {"balanced": {"1004": ref.slot_id}, "focused": {"1004": empty}}}
    service.save_preferences(value)
    saved = service.load_preferences()
    assert saved == value
    assert read_slot_preferences(saved, strategy="balanced") == {1004: ref.slot_id}
    assert read_slot_preferences(saved, strategy="focused") == {1004: empty}
    assert service_for(second, tmp_path / "static.sqlite3").load_preferences() == {}
    invalid = {**value, "selected_slots_by_strategy": {
        "balanced": {"1004": ref.slot_id}, "focused": {"1005": ref.slot_id}}}
    with pytest.raises(ValueError):
        service.save_preferences(invalid)
    assert service.load_preferences() == value


@pytest.mark.parametrize("maps", [None, {"balanced": {}}, {"balanced": {}, "focused": []},
                                  {"balanced": {}, "focused": {"1004": True}},
                                  {"balanced": {}, "focused": {}, "unknown": {}}])
def test_invalid_strategy_slot_maps_do_not_replace_previous_preferences(tmp_path, maps):
    path = tmp_path / "user.sqlite3"
    create_account(path, "fixture")
    service = service_for(path, tmp_path / "static.sqlite3")
    service.save_preferences({"other": "original"})
    with pytest.raises(ValueError):
        service.save_preferences({"slot_selection_version": 2, "selected_slots_by_strategy": maps})
    assert service.load_preferences() == {"other": "original"}


def test_atomic_recommendation_save_rejects_replaced_plan_and_keeps_original(tmp_path):
    path = tmp_path / "user.sqlite3"
    ref, _ = create_account(path, "fixture")
    with UserDataDao(path) as dao:
        dao.replace_application_setting_copy("rewind_recommendation", {"saved_rewind_shape_ids": ["old"] * 8})
        for bad in (ref.plan_id + 1, None):
            with pytest.raises(UserDataValidationError, match="已变化"):
                dao.replace_application_setting_copy("rewind_recommendation", {"saved_rewind_shape_ids": ["new"] * 8},
                    expected_loadout_plans=({"character_id": ref.character_id, "slot_id": ref.slot_id, "plan_id": bad},))
            assert dao.list_application_setting_copies()["rewind_recommendation"]["saved_rewind_shape_ids"] == ["old"] * 8
        dao.replace_application_setting_copy("rewind_recommendation", {"saved_rewind_shape_ids": ["new"] * 8},
            expected_loadout_plans=({"character_id": ref.character_id, "slot_id": ref.slot_id, "plan_id": ref.plan_id},))
        assert dao.list_application_setting_copies()["rewind_recommendation"]["saved_rewind_shape_ids"] == ["new"] * 8


def test_consistent_read_ends_cleanly_and_retains_empty_slots(tmp_path):
    path = tmp_path / "user.sqlite3"
    ref, empty = create_account(path, "fixture")
    with UserDataDao(path) as dao:
        with dao.read_consistent_state():
            rows = {slot["slot_id"]: slot for slot in dao.list_visible_loadout_slots_with_plans()}
            assert rows[ref.slot_id]["current_plan"]["plan_id"] == ref.plan_id
            assert rows[empty]["current_plan"] is None
        dao.replace_application_setting_copy("rewind_recommendation", {"other": 1})
        with pytest.raises(RuntimeError):
            with dao.read_consistent_state():
                raise RuntimeError("取消读取")
        assert dao.list_application_setting_copies()["rewind_recommendation"] == {"other": 1}
