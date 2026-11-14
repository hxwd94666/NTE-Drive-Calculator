# 验证两个管理的固定未匹配策略、旧关闭值兼容与资料不足保护，不派发游戏动作。
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.domain.equipment.eligibility.management_settings import normalize_warehouse_config
from src.domain.equipment.warehouse.warehouse_decisions import DecisionStatus, scoring_decision
from src.domain.inventory.post_actions import default_post_action_config, merge_post_action_config, evaluate_target_state_for_item
from src.models.equipment import Tape
from src.domain.equipment.eligibility.management_settings import MatchingScope
from src.services.scanning.management_settings import load_scan_post_action_config, save_scan_post_action_config
from src.services.inventory.management.management_settings import ManagementSettingsService
from src.storage.sqlite.user_data_dao import UserDataDao


@pytest.mark.parametrize("legacy", [None, {}, {"discard": {"discard_unmatched": False}}])
def test_scan_default_enables_complete_unmatched_even_for_old_disabled_value(legacy):
    assert merge_post_action_config(legacy)["discard"]["discard_unmatched"] is True
    assert default_post_action_config()["discard"]["discard_unmatched"] is True


def test_legacy_account_false_value_is_effective_on_load_and_preserved_by_scoped_save(tmp_path):
    database = tmp_path / "user.sqlite3"
    old = default_post_action_config()
    old["discard"].update(enabled=True, discard_unmatched=False)
    warehouse = normalize_warehouse_config({"lock": {"enabled": True}})
    with UserDataDao(database, account_id="fixture") as dao:
        dao.patch_application_setting_copy(
            "ui", {"full_scan_post_action_config": old, "warehouse_management_config": warehouse}
        )
    loaded = load_scan_post_action_config(None, user_database_path=database)
    assert loaded["discard"]["discard_unmatched"] is True
    scan = ManagementSettingsService(database, scope=MatchingScope.SCAN)
    value, matching = scan.load()
    assert value["discard"]["discard_unmatched"] is True
    # 读取只解释旧数据，不擅自覆写账号；明确保存才规范化本 owner 的字段。
    with UserDataDao(database) as dao:
        assert (
            dao.list_application_setting_copies()["ui"]["full_scan_post_action_config"]["discard"]["discard_unmatched"]
            is False
        )
    save_scan_post_action_config(None, old, user_database_path=database)
    with UserDataDao(database) as dao:
        saved = dao.list_application_setting_copies()["ui"]
    assert saved["full_scan_post_action_config"]["discard"]["discard_unmatched"] is True
    assert saved["warehouse_management_config"] == warehouse
    old["discard"]["grade"] = "B"
    result = scan.save(old, matching, expected_config=value, expected_matching=matching)
    assert result["discard"]["discard_unmatched"] is True
    assert ManagementSettingsService(database, scope=MatchingScope.WAREHOUSE).load()[0] == warehouse


def test_legacy_json_false_value_and_no_config_follow_the_same_default(tmp_path):
    import json

    path = tmp_path / "scan_post_actions.json"
    path.write_text(json.dumps({"discard": {"enabled": True, "discard_unmatched": False}}), encoding="utf-8")
    assert load_scan_post_action_config(tmp_path)["discard"]["discard_unmatched"] is True
    assert load_scan_post_action_config(None)["discard"]["discard_unmatched"] is True


@pytest.mark.parametrize("owner", ["scan", "warehouse"])
@pytest.mark.parametrize(
    "case,expected",
    [
        ("complete", "discarded"),
        ("disabled", "normal"),
        ("outside_type", "normal"),
        ("outside_set", "normal"),
        ("outside_quality", "normal"),
        ("locked_skip", "locked"),
        ("discarded_skip", "discarded"),
        ("empty_roles", "normal"),
        ("missing_roles", "normal"),
        ("incomplete", "normal"),
    ],
)
def test_both_managements_discard_only_complete_in_scope_unmatched(owner, case, expected):
    config = merge_post_action_config({"discard": {"enabled": True, "discard_unmatched": False}})
    if owner == "warehouse":
        config = normalize_warehouse_config(config)
    config["discard"]["set_names"] = ["测试套装"]
    item = Tape(uid="fixture", quality="Gold", area=15, set_name="测试套装", main_stats="攻击力%", sub_stats={})
    scorer = SimpleNamespace(roles_db={"role": {}}, get_grade_tag=lambda *_: pytest.fail("未匹配不评分"))
    context = SimpleNamespace(missing_character_ids=(), errors_for_roles=lambda _: (), for_item=lambda _: [])
    state = "normal"
    if case == "disabled":
        config["discard"]["enabled"] = False
    elif case == "outside_type":
        config["discard"]["type_scope"] = "drive"
    elif case == "outside_set":
        config["discard"]["set_names"] = ["另一套装"]
    elif case == "outside_quality":
        config["discard"]["quality_scope"] = "gold"
        item = item.model_copy(update={"quality": "Purple"})
    elif case == "locked_skip":
        state = "locked"
    elif case == "discarded_skip":
        state = "discarded"
        config["discard"]["on_discarded"] = "skip"
    elif case == "empty_roles":
        config["discard"]["role_scope"] = "selected"
    elif case == "missing_roles":
        context.missing_character_ids = (999,)
    elif case == "incomplete":
        context.errors_for_roles = lambda _: ("missing evidence",)
    original = deepcopy(config)
    if owner == "scan":
        result, detail = evaluate_target_state_for_item(
            item, state, config, scorer, selected_roles={"discard": [], "lock": []}, score_context=context
        )
        assert result == expected
        if case == "complete":
            assert detail["discard"]["score"] is None and detail["discard"]["grade"] is None
    else:
        raw = {"locked": state == "locked", "discarded": state == "discarded", "equipped": False}
        decision = scoring_decision(item, raw, config, scorer, context, {"discard": [], "lock": []})
        assert (decision.target or state) == expected
        if case in {"empty_roles", "missing_roles", "incomplete"}:
            assert decision.status == DecisionStatus.UNKNOWN
    assert config == original
